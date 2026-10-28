"""Decision evidence and identity at the planning boundary.

This module does not choose versions or solve peers. It preserves planner
evidence, describes the single-manifest boundary honestly, and hashes the
published decision independently of run directories and timestamps.
"""
from __future__ import annotations

import fnmatch
from functools import lru_cache
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from deploom_mcp.target_assessment import target_assessment, target_decision_evidence

TARGET_MODES = ("pinned", "advisory")
OBJECTIVES = ("minimum-change-to-policy", "latest-compatible-in-approved-lines")
MAX_DRAFT_CANDIDATES = 128
MAX_SCOPE_PACKAGES = 1024


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_decision_contract(intent: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if intent is not None and not isinstance(intent, Mapping):
        raise ValueError("intent must be a JSON object")
    value = intent or {}
    mode = value.get("targetMode", "pinned")
    objective = value.get("objective", OBJECTIVES[0])
    if mode not in TARGET_MODES:
        raise ValueError("targetMode must be pinned or advisory")
    if objective not in OBJECTIVES:
        raise ValueError("unsupported objective")
    mutable = value.get("mutablePackages")
    if mutable is not None:
        if not isinstance(mutable, list) or len(mutable) > MAX_SCOPE_PACKAGES:
            raise ValueError("mutablePackages must be null or an array of at most 1024 package names")
        if any(not isinstance(x, str) or not x.strip() for x in mutable):
            raise ValueError("mutablePackages entries must be non-empty package names")
        mutable = sorted(set(x.strip() for x in mutable))
    lines = value.get("approvedVersionLines")
    if lines is None:
        lines = {}
    if not isinstance(lines, dict) or len(lines) > MAX_SCOPE_PACKAGES:
        raise ValueError("approvedVersionLines must map package names to allowed major numbers")
    normalized_lines = {}
    for name, majors in lines.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(majors, list):
            raise ValueError("approvedVersionLines entries require a package and an array of majors")
        if len(majors) > 128 or any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in majors):
            raise ValueError("approvedVersionLines majors must be non-negative integers (at most 128 per package)")
        normalized_lines[name.strip()] = sorted(set(majors))
    policies = value.get("policies") or {}
    if mutable is not None and isinstance(policies, dict):
        outside = [name for name, action in policies.items() if action == "required" and name not in mutable]
        if outside:
            raise ValueError("required packages must be included in mutablePackages: " + ", ".join(sorted(outside)))
    return {
        "version": 1, "targetMode": mode, "objective": objective,
        "mutablePackages": mutable, "approvedVersionLines": dict(sorted(normalized_lines.items())),
    }


def requires_contract_support(contract: Mapping[str, Any]) -> bool:
    return (contract.get("targetMode") != "pinned"
            or contract.get("objective") != OBJECTIVES[0]
            or contract.get("mutablePackages") is not None
            or bool(contract.get("approvedVersionLines")))


def _read_object(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _patterns(root: Path, errors: Optional[list[str]] = None) -> list[str]:
    def read_config(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        if errors is not None:
            errors.append(str(path))
        return {}

    manifest = read_config(root / "package.json")
    workspaces = manifest.get("workspaces")
    declared = workspaces is not None
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    if not isinstance(workspaces, list):
        if declared and errors is not None:
            errors.append(str(root / "package.json") + "#workspaces")
        workspaces = read_config(root / "lerna.json").get("packages", [])
    if not isinstance(workspaces, list):
        if errors is not None:
            errors.append(str(root / "lerna.json") + "#packages")
        return []
    valid = []
    for pattern in workspaces[:128]:
        if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > 512:
            if errors is not None:
                errors.append(str(root / "package.json") + "#workspace-pattern")
            continue
        candidate = pattern.lstrip("!")
        if candidate.startswith("/") or ".." in candidate.split("/") or "{" in candidate or "}" in candidate:
            if errors is not None:
                errors.append(str(root / "package.json") + "#unsupported-workspace-pattern")
            continue
        valid.append(pattern.rstrip("/"))
    if len(workspaces) > 128 and errors is not None:
        errors.append(str(root / "package.json") + "#workspace-pattern-limit")
    return valid


def _glob_match(relative: str, pattern: str) -> bool:
    # A workspace '*' is one directory component, '**' may span components.
    while pattern.startswith("./"):
        pattern = pattern[2:]
    parts, patterns = relative.split("/"), pattern.split("/")

    @lru_cache(maxsize=None)
    def match(i: int, j: int) -> bool:
        if j == len(patterns):
            return i == len(parts)
        if patterns[j] == "**":
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], patterns[j]) and match(i + 1, j + 1)

    return match(0, 0)


def workspace_coverage(project: Path, *, max_directories: int = 2048, max_manifests: int = 256) -> Dict[str, Any]:
    """Bounded discovery, explicitly NOT a joint assignment or a peer proof."""
    project = project.resolve()
    root = project
    errors: list[str] = []
    if not (project / "package.json").is_file():
        errors.append(str(project / "package.json"))
    patterns = _patterns(root, errors)
    if not patterns:
        for parent in project.parents:
            candidate = _patterns(parent, errors)
            relative = project.relative_to(parent).as_posix()
            if candidate and any(_glob_match(relative, p) for p in candidate if not p.startswith("!")):
                root, patterns = parent, candidate
                break
    manifest = project / "package.json"
    discovered = {str(manifest)}
    complete = not errors and not any("{" in p or "}" in p for p in patterns)
    if patterns:
        discovered.add(str(root / "package.json"))
        visited = 0

        def discovery_error(error: OSError) -> None:
            nonlocal complete
            complete = False
            errors.append(str(error.filename or root))

        for directory, dirs, files in os.walk(root, followlinks=False, onerror=discovery_error):
            eligible = [d for d in dirs if d not in {"node_modules", ".git", ".venv"}]
            if any((Path(directory) / d).is_symlink() for d in eligible):
                complete = False
            dirs[:] = sorted(d for d in eligible if not (Path(directory) / d).is_symlink())
            visited += 1
            if visited > max_directories:
                complete = False
                break
            rel = Path(directory).relative_to(root).as_posix()
            included = any(_glob_match(rel, p) for p in patterns if not p.startswith("!"))
            excluded = any(_glob_match(rel, p[1:]) for p in patterns if p.startswith("!"))
            if "package.json" in files and included and not excluded:
                discovered.add(str(Path(directory) / "package.json"))
                if len(discovered) >= max_manifests:
                    complete = False
                    break
    evaluated = [str(manifest)]
    missing = sorted(discovered - set(evaluated))
    reconciliation = bool(patterns or missing or not complete)
    return {
        "mode": "SINGLE_MANIFEST_PLANNING", "workspaceRoot": str(root),
        "discoveredManifests": sorted(discovered), "evaluatedManifests": evaluated,
        "unevaluatedManifests": missing, "discoveryComplete": complete,
        "discoveryErrors": errors,
        "workspaceGraphCoverage": "PARTIAL" if reconciliation else "SINGLE_PROJECT",
        "jointAssignment": "NOT_COMPUTED", "reconciliationRequired": reconciliation,
        "nextAction": "RECONCILE_WORKSPACE_PLANS" if reconciliation else "VERIFY_SCOPED_PLAN",
        "note": "Input fingerprints may read other manifests; that does not mean those manifests were jointly planned.",
    }


def _git_identity(directory: Path, *, require_root: bool = False, source_name: Optional[str] = None) -> Dict[str, Any]:
    try:
        revision = subprocess.run(["git", "-C", str(directory), "rev-parse", "--show-toplevel", "HEAD"],
                                  capture_output=True, text=True, timeout=2, check=False)
        lines = revision.stdout.strip().splitlines()
        if revision.returncode or len(lines) != 2 or not re.fullmatch(r"[0-9a-f]{40,64}", lines[1]):
            return {"revision": None, "workingTreeDirty": None}
        if require_root and Path(lines[0]).resolve() != directory.resolve():
            return {"revision": None, "workingTreeDirty": None,
                    "containingRepository": {"path": lines[0], "revision": lines[1]}}
        if source_name:
            tracked = subprocess.run(["git", "-C", str(directory), "ls-files", "--error-unmatch", "--", source_name],
                                     capture_output=True, text=True, timeout=2, check=False)
            if tracked.returncode:
                return {"revision": None, "workingTreeDirty": None,
                        "containingRepository": {"path": lines[0], "revision": lines[1]}}
        dirty = subprocess.run(["git", "-C", str(directory), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=2, check=False)
        return {"revision": lines[1],
                "workingTreeDirty": bool(dirty.stdout.strip()) if not dirty.returncode else None}
    except (OSError, subprocess.TimeoutExpired):
        return {"revision": None, "workingTreeDirty": None}


def runtime_provenance(generator: Path) -> Dict[str, Any]:
    from deploom_mcp import __version__
    from deploom_mcp.distribution import release_manifest_path, skill_root

    module_dir = Path(__file__).resolve().parent
    code = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(module_dir.glob("*.py"))}
    skill_dir = skill_root()
    skill_manifest = _read_object(skill_dir / "contract.json")
    declared_skill_files = skill_manifest.get("files") or {}
    skill_files = {}
    for name in declared_skill_files:
        path = (skill_dir / name).resolve()
        if path.is_relative_to(skill_dir.resolve()) and path.is_file() and name != "contract.json":
            skill_files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    skill_hash = digest(skill_files) if skill_files and len(skill_files) == len(declared_skill_files) else None
    release = _read_object(release_manifest_path())
    expected_core = release.get("core") or {}
    expected_files = expected_core.get("files") or {}
    actual_files = {}
    for name in expected_files:
        path = (generator.parent / name).resolve()
        if path.is_relative_to(generator.parent.resolve()) and path.is_file():
            actual_files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    actual_bundle_hash = digest(actual_files) if expected_files and len(actual_files) == len(expected_files) else None
    return {
        "core": {"generatorPath": str(generator),
                 "generatorSha256": hashlib.sha256(generator.read_bytes()).hexdigest(),
                 **_git_identity(generator.parent, require_root=True, source_name=generator.name), "bundleContentSha256": actual_bundle_hash,
                 "matchesExpectedRelease": actual_bundle_hash == expected_core.get("contentSha256") if actual_bundle_hash else None},
        "mcp": {"version": __version__, "codeSha256": digest(code), **_git_identity(module_dir, source_name="__init__.py")},
        "skill": {"contractVersion": skill_manifest.get("contractVersion"),
                  "sourceBundleSha256": skill_hash,
                  "declaredBundleSha256": skill_manifest.get("bundleSha256"),
                  "sourceMatchesManifest": bool(skill_hash and skill_hash == skill_manifest.get("bundleSha256")) if skill_manifest else None,
                  "loadedClientContent": "UNVERIFIED"},
        "expectedRelease": ({"mcpVersion": release.get("mcpVersion"),
                             "core": {k: expected_core.get(k) for k in ("repository", "revision", "version", "contentSha256")},
                             "schemaSha256": release.get("schemaSha256"),
                             "skillManifestSha256": release.get("skillManifestSha256")} if release else None),
    }


def _evidence_kind(message: str, status: str = "") -> tuple[str, str, str]:
    upper = message.upper()
    if "TYPE_STUB_REMOVE_PROVED" in upper:
        return "TYPE_STUB_REMOVAL", "REVIEW", "REVIEW_REMOVAL_PROOF"
    if "PEER_RESOLUTION_FALLBACK" in upper:
        return "PEER_FALLBACK", "REVIEW", "REVIEW_RESOLVED_ASSIGNMENT"
    if "PEER_RESOLUTION_DEFERRED" in upper or "MISSING PEER" in upper:
        return "PEER_CONSTRAINT", "BLOCKER", "RECONCILE_PEER_ASSIGNMENT"
    if "COHORT_BLOCKED" in upper or status == "BLOCKED":
        return "PLANNER_BLOCKER", "BLOCKER", "READ_CONSTRAINT_EVIDENCE"
    if status == "PEER_UNRESOLVED":
        return "PEER_UNRESOLVED", "REVIEW", "RESOLVE_PEER_EVIDENCE"
    if "CANDIDATE" in upper and "TRUNCAT" in upper or status == "CANDIDATE_SEARCH_TRUNCATED":
        return "CANDIDATE_CAP", "REVIEW", "EXPAND_CANDIDATE_EVIDENCE"
    if status == "UNKNOWN_METADATA":
        return "METADATA_UNKNOWN", "REVIEW", "FETCH_MISSING_METADATA"
    if status == "UNKNOWN_SECURITY":
        return "SECURITY_UNKNOWN", "REVIEW", "FETCH_SECURITY_EVIDENCE"
    if status in {"UNKNOWN", "NO_TARGET", "NOT_ATTEMPTED", "INTERRUPTED", "DEFERRED"}:
        return "UNRESOLVED_TARGET", "REVIEW", "INSPECT_UNRESOLVED_TARGET"
    if status == "POLICY_SATISFIED":
        return "POLICY_SATISFIED", "INFO", "CHECK_UPGRADE_INTENT"
    if status in {"PROPOSED", "NO_CHANGE", "EXCLUDED"}:
        return "PLANNER_RECOMMENDATION", "INFO", "VERIFY_PROPOSED_ASSIGNMENT"
    if "COHORT" in upper:
        return "COHORT_NOTE", "INFO", "REVIEW_COHORT_EVIDENCE"
    return "PLANNER_NOTE", "REVIEW", "READ_PLANNER_EVIDENCE"


def decision_evidence(blueprint: Mapping[str, Any]) -> list[Dict[str, Any]]:
    result: list[Dict[str, Any]] = target_decision_evidence(target_assessment(blueprint))
    for index, row in enumerate(blueprint.get("dependencies") or []):
        evidence = row.get("evidence") or {}
        status = row.get("status", "UNKNOWN")
        reason = row.get("reason") or ""
        code, severity, action = _evidence_kind(str(reason), status)
        if reason or row.get("needsReview") or status == "POLICY_SATISFIED":
            result.append({"code": code, "severity": severity, "package": row.get("package"),
                           "message": str(reason), "nextAction": action,
                           "lifecycle": "CURRENT_ROW",
                           "evidenceRef": f"#/dependencies/{index}"})
        if evidence.get("candidateSearchTruncated") and code != "CANDIDATE_CAP":
            result.append({"code": "CANDIDATE_CAP", "severity": "REVIEW", "package": row.get("package"),
                           "lifecycle": "CURRENT_ROW",
                           "message": "Candidate evidence is capped; this is not proof of incompatibility.",
                           "nextAction": "EXPAND_CANDIDATE_EVIDENCE", "evidenceRef": f"#/dependencies/{index}/evidence"})
        if evidence.get("peerCompatUnresolved") and code != "PEER_UNRESOLVED":
            result.append({"code": "PEER_UNRESOLVED", "severity": "REVIEW", "package": row.get("package"),
                           "lifecycle": "CURRENT_ROW", "message": "Peer compatibility remains unresolved.",
                           "nextAction": "RESOLVE_PEER_EVIDENCE", "evidenceRef": f"#/dependencies/{index}/evidence"})
        if row.get("breakingChanges") or row.get("migrationNotes"):
            result.append({"code": "MIGRATION_REVIEW", "severity": "REVIEW", "package": row.get("package"),
                           "lifecycle": "CURRENT_ROW",
                           "message": "The planner reports breaking changes or source migration notes.",
                           "nextAction": "REVIEW_MIGRATION_WITHIN_AUTHORITY", "evidenceRef": f"#/dependencies/{index}"})
    for category in ("conflicts", "unknowns", "unresolved"):
        for index, item in enumerate(blueprint.get(category) or []):
            parts = [str(item[k]) for k in ("code", "type", "reason", "message", "detail", "notes") if item.get(k)]
            message = "; ".join(parts) or json.dumps(item, ensure_ascii=False, sort_keys=True)
            code, severity, action = _evidence_kind(message)
            active = item.get("active") is True and item.get("stage") == "final"
            lifecycle = "ACTIVE_FINAL" if active else "UNRESOLVED_LEGACY"
            if severity == "BLOCKER" and not active:
                severity = "REVIEW"
            result.append({"code": code, "severity": severity, "package": item.get("package"),
                           "lifecycle": lifecycle,
                           "message": message, "nextAction": action, "evidenceRef": f"#/{category}/{index}"})
    return sorted(result, key=lambda x: ({"BLOCKER": 0, "REVIEW": 1, "INFO": 2}[x["severity"]],
                                        str(x.get("package") or ""), x["evidenceRef"], x["code"]))


def enrich_decisions(blueprint: Dict[str, Any], *, candidate_cap: int = 3,
                     previous_evidence_digest: Optional[str] = None) -> None:
    """Add evidence/identity; never manufacture compatibility or solver proof."""
    source = blueprint.get("source") or {}
    project = source.get("project") or {}
    coverage = blueprint.get("workspaceCoverage") or {}
    project_path = Path(project["path"]) if project.get("path") else None
    root = Path(coverage["workspaceRoot"]) if coverage.get("workspaceRoot") else project_path

    def logical_path(path: str) -> str:
        value = Path(path)
        if root is not None and value.is_absolute():
            try:
                return value.relative_to(root).as_posix()
            except ValueError:
                return "external/" + value.name
        return value.as_posix()

    manifest = logical_path(str(project_path / "package.json")) if project_path else "package.json"
    for row in blueprint.get("dependencies") or []:
        row["declarationId"] = f"{manifest}#{row.get('kind') or 'unknown'}:{row.get('package')}"
    blueprint["targetAssessment"] = target_assessment(blueprint)
    evidence = decision_evidence(blueprint)
    blueprint["decisionEvidence"] = evidence
    by_package: Dict[str, list[Dict[str, Any]]] = {}
    for item in evidence:
        by_package.setdefault(str(item.get("package") or ""), []).append(item)
    for row in blueprint.get("dependencies") or []:
        items = list(by_package.get(str(row.get("package") or ""), []))
        # Project/aggregate findings concern the assignment, not a proven
        # offending package. Keep that attribution when requiring row review.
        items.extend(item for item in evidence if item.get("scope") == "aggregate"
                     or (item.get("scope") == "project" and item.get("project") == row.get("project")))
        items.sort(key=lambda item: (0 if item["severity"] == "BLOCKER" else 1 if item["severity"] == "REVIEW" else 2,
                                    str(item.get("evidenceRef") or ""), item["code"]))
        row["decisionReasons"] = items
        if row.get("status") == "EXCLUDED":
            # The user's scope decision is already resolved. Related peer or
            # unknown evidence still constrains the rest of the plan and stays
            # in decisionReasons/global evidence; it does not reopen this row.
            row["needsReview"] = False
            row["nextAction"] = "KEEP_EXCLUDED_CURRENT"
        else:
            row["needsReview"] = bool(row.get("needsReview") or any(x["severity"] != "INFO" for x in items))
            row["nextAction"] = items[0]["nextAction"] if items else "VERIFY_PROPOSED_ASSIGNMENT"
    blueprint.setdefault("summary", {})["needsReview"] = sum(bool(x.get("needsReview")) for x in blueprint.get("dependencies") or [])
    for group in blueprint.get("recommendationGroups") or []:
        names = set(group.get("packages") or [])
        group["needsReview"] = any(r.get("needsReview") for r in blueprint.get("dependencies") or [] if r.get("package") in names)
    rows = [{k: row.get(k) for k in ("declarationId", "package", "kind", "current", "target", "status")}
            for row in blueprint.get("dependencies") or []]
    rows.sort(key=lambda x: json.dumps(x, sort_keys=True))
    policy = blueprint.get("policy") or {}
    policy_identity = {k: policy.get(k) for k in ("targetLevel", "minLagOkPct", "decisionContract", "acceptancePolicy")}
    policy_identity["exclusions"] = sorted(set(policy.get("exclusions") or []))
    policy_identity["legacyIntent"] = {k: v for k, v in (policy.get("intent") or {}).items()
                                       if k not in {"version", "targetMode", "objective", "mutablePackages", "approvedVersionLines"}}
    coverage_identity = {k: coverage.get(k) for k in ("mode", "workspaceGraphCoverage", "jointAssignment", "discoveryComplete", "reconciliationRequired")}
    for field in ("discoveredManifests", "evaluatedManifests", "unevaluatedManifests"):
        coverage_identity[field] = sorted(logical_path(x) for x in coverage.get(field) or [])
    dynamic_paths = {str(v): f"<artifact:{k}>" for k, v in (blueprint.get("artifacts") or {}).items() if v}
    if project_path:
        dynamic_paths[str(project_path)] = "<project>"
    semantic_reasons = []
    for item in evidence:
        reason = {k: item.get(k) for k in ("code", "severity", "package", "scope", "project", "message", "nextAction", "lifecycle")}
        for path, replacement in sorted(dynamic_paths.items(), key=lambda p: len(p[0]), reverse=True):
            reason["message"] = reason["message"].replace(path, replacement)
        semantic_reasons.append(reason)
    semantic_reasons.sort(key=lambda x: json.dumps(x, sort_keys=True))
    # Fingerprint the normalized, published evidence, NOT all registry data.
    # Source JSON pointers may move when rows reorder and are not semantic.
    evidence_rows = [{"declarationId": r.get("declarationId"), "evidence": r.get("evidence"),
                      "latest": r.get("latest")} for r in blueprint.get("dependencies") or []]
    evidence_rows.sort(key=lambda x: json.dumps(x, sort_keys=True))
    evidence_hash = digest({"rows": evidence_rows, "decisionEvidence": semantic_reasons,
                            "perProject": blueprint.get("perProject") or {},
                            "targetAssessment": blueprint["targetAssessment"],
                            "coverage": {k: (blueprint.get("summary") or {}).get(k) for k in
                                         ("metadataKnown", "metadataTotal", "securityKnown", "securityTotal", "insufficientData")}})
    blueprint["identities"] = {
        "semanticInputHash": (blueprint.get("source") or {}).get("semanticInputHash"),
        "policyDigest": digest(policy_identity), "evidenceDigest": evidence_hash,
        "evidenceScope": "PUBLISHED_PLANNING_EVIDENCE",
        "decisionDigest": digest({"rows": rows, "policy": policy_identity, "reasons": semantic_reasons,
                                  "coverage": coverage_identity, "semanticInputHash": source.get("semanticInputHash")}),
    }
    capped = sum(x["code"] == "CANDIDATE_CAP" for x in evidence)
    blueprint["recovery"] = {
        "draftMaxCandidates": candidate_cap, "candidateCapEvidenceCount": capped,
        "previousEvidenceComparison": ("NOT_PROVIDED" if previous_evidence_digest is None else
                                       "UNCHANGED_EVIDENCE" if previous_evidence_digest == evidence_hash else "EVIDENCE_CHANGED"),
        "nextAction": ("EXPAND_CANDIDATE_EVIDENCE" if capped and candidate_cap < MAX_DRAFT_CANDIDATES else
                       "INSPECT_CAPPED_EVIDENCE" if capped else "FOLLOW_DECISION_REASONS"),
        "suggestedDraftMaxCandidates": min(MAX_DRAFT_CANDIDATES, candidate_cap * 2) if capped and candidate_cap < MAX_DRAFT_CANDIDATES else None,
        "note": "A larger deadline does not widen the candidate cap. Repeat only when evidence, cap, inputs or policy changes.",
    }
