"""Evidence-bound follow-up operations for a published Upgrade Blueprint.

These are read-only decision *inspection* tools, not a second planner or a
prospective npm/peer solver.  In particular, an alternative package version
does not inherit the original plan's target security or compatibility result.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

MAX_BLUEPRINT_BYTES = 16 * 1024 * 1024
MAX_ASSIGNMENT_KEYS = 256
HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
EXACT_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


class FollowupError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def _error(exc: FollowupError) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": exc.code, "message": str(exc)},
        "authority": "PLANNING_ONLY",
        "verificationStatus": "NOT_VERIFIED",
        "compatibility": "UNKNOWN",
    }


def _checked_path(value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise FollowupError("invalid-blueprint-path", "blueprint_path must be an absolute file path")
    path = Path(value).expanduser()
    if not path.is_absolute() or path.name != "upgrade-blueprint.json":
        raise FollowupError("invalid-blueprint-path", "expected an absolute upgrade-blueprint.json path from get_upgrade_plan")
    if path.is_symlink() or not path.is_file():
        raise FollowupError("blueprint-unavailable", "the requested published Blueprint is missing or a symlink")
    if path.stat().st_size > MAX_BLUEPRINT_BYTES:
        raise FollowupError("blueprint-too-large", "the Blueprint exceeds the 16 MiB read limit")
    return path.resolve()


def load_blueprint(path_value: str, expected_decision_digest: str | None = None) -> tuple[dict, str]:
    """Validate the complete published file and its semantic decision digest."""
    path = _checked_path(path_value)
    if expected_decision_digest is not None and (
        not isinstance(expected_decision_digest, str)
        or not HEX_DIGEST.fullmatch(expected_decision_digest)
    ):
        raise FollowupError("invalid-decision-digest", "expected_decision_digest must be a lowercase SHA-256")
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, ValueError) as exc:
        raise FollowupError("blueprint-invalid", "cannot read/parse the published Blueprint") from exc
    if not isinstance(value, dict) or value.get("kind") != "upgrade-blueprint" or value.get("schemaVersion") != 2:
        raise FollowupError("blueprint-unsupported", "a version-2 Upgrade Blueprint is required")
    if value.get("blueprintFile") != str(path):
        raise FollowupError("blueprint-path-mismatch", "published Blueprint path does not match the requested file")
    status = value.get("status") or {}
    if (status.get("authority") != "PLANNING_ONLY"
            or status.get("verificationStatus") != "NOT_VERIFIED"
            or status.get("draftStatus") not in ("DRAFT_READY", "DRAFT_PARTIAL")):
        raise FollowupError("blueprint-contract", "Blueprint does not have a supported planning-only contract")
    from deploom_mcp.blueprint import _validate_blueprint_schema
    validation = _validate_blueprint_schema(value)
    if validation is not None:
        raise FollowupError("blueprint-schema", "Blueprint failed the bundled JSON Schema")
    identities = value.get("identities") or {}
    if not all(isinstance(identities.get(k), str) and HEX_DIGEST.fullmatch(identities[k])
               for k in ("decisionDigest", "evidenceDigest", "policyDigest")):
        raise FollowupError("blueprint-unsupported", "Blueprint lacks normalized decision/evidence identities")
    if expected_decision_digest is not None and identities["decisionDigest"] != expected_decision_digest:
        raise FollowupError("decision-digest-mismatch", "Blueprint differs from the expected decision identity")
    # Recalculate using the same projection rules. A modified, well-formed JSON
    # file must not inherit the old published evidence or user authority.
    from deploom_mcp.decisions import enrich_decisions
    candidate = copy.deepcopy(value)
    options = value.get("planningOptions") or {}
    cap = options.get("draftMaxCandidates", 3)
    cap = cap if type(cap) is int and 1 <= cap <= 128 else 3
    enrich_decisions(candidate, candidate_cap=cap)
    for key in ("decisionDigest", "evidenceDigest", "policyDigest"):
        if candidate["identities"].get(key) != identities.get(key):
            raise FollowupError("blueprint-identity-mismatch", "published decision/evidence identity no longer matches its contents")
    return value, hashlib.sha256(raw).hexdigest()


def source_freshness(blueprint: Mapping[str, Any]) -> dict[str, Any]:
    """Compare recorded bytes, without claiming all semantic settings are replayed."""
    by_project = (blueprint.get("source") or {}).get("semanticInputsByProject") or {}
    if not isinstance(by_project, dict):
        return {"status": "UNKNOWN", "matchedFiles": 0, "reason": "SEMANTIC_INPUTS_UNAVAILABLE"}
    seen: set[str] = set()
    matching = 0
    missing = 0
    mismatches: list[str] = []
    for project in by_project.values():
        if not isinstance(project, dict):
            continue
        for file in project.get("files") or []:
            if not isinstance(file, dict) or not isinstance(file.get("path"), str):
                continue
            path_text = file["path"]
            if path_text in seen:
                continue
            seen.add(path_text)
            recorded = file.get("contentHash")
            if not isinstance(recorded, str) or not HEX_DIGEST.fullmatch(recorded):
                # Virtual effective settings have semantic hashes but no source
                # path. Invalid/missing required inputs cannot be certified.
                if file.get("required") and file.get("status") != "PRESENT":
                    missing += 1
                continue
            path = Path(path_text)
            if not path.is_absolute() or path.is_symlink() or not path.is_file():
                mismatches.append(str(file.get("name") or "<unknown>"))
                continue
            try:
                if path.stat().st_size > MAX_BLUEPRINT_BYTES:
                    mismatches.append(str(file.get("name") or "<large-file>"))
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                mismatches.append(str(file.get("name") or "<unreadable>"))
                continue
            if digest != recorded:
                mismatches.append(str(file.get("name") or "<changed>"))
            else:
                matching += 1
    return {
        "status": "STALE" if mismatches else "UNKNOWN" if not matching or missing else "MATCHES_RECORDED_FILES",
        "matchedFiles": matching, "unverifiableRequiredFiles": missing,
        "changedFiles": sorted(set(mismatches))[:30],
        "note": "Semantic settings, registry/OSV evidence and solver snapshot are not independently replayed.",
    }


def _row_baseline(row: Mapping[str, Any]) -> str | None:
    if row.get("status") == "PROPOSED" and isinstance(row.get("target"), str) and row["target"]:
        return row["target"]
    version = row.get("current")
    return version if isinstance(version, str) and version else None


def evaluate_assignment(*, blueprint_path: str, assignment: dict[str, str],
                        expected_decision_digest: str | None = None) -> dict[str, Any]:
    """Evaluate documented user/policy constraints; never claim a new Z3 proof.

    Keys can be package names or published declarationIds. Bare package names
    with conflicting duplicate declarations are rejected as ambiguous.
    """
    try:
        if not isinstance(assignment, dict) or len(assignment) > MAX_ASSIGNMENT_KEYS:
            raise FollowupError("invalid-assignment", "assignment must map at most 256 package/declaration IDs to versions")
        if any(not isinstance(k, str) or not k or not isinstance(v, str) or
               not EXACT_VERSION.fullmatch(v) for k, v in assignment.items()):
            raise FollowupError("invalid-assignment", "keys must be non-empty and values exact stable/prerelease semver versions")
        bp, file_sha = load_blueprint(blueprint_path, expected_decision_digest)
        rows = list(bp.get("dependencies") or [])
        if not rows or len(rows) > 2048:
            raise FollowupError("blueprint-unsupported", "expected 1..2048 published dependency rows")
        by_name: dict[str, list[dict]] = {}
        by_id: dict[str, dict] = {}
        for row in rows:
            name, identity = row.get("package"), row.get("declarationId")
            if not isinstance(name, str) or not isinstance(identity, str) or identity in by_id:
                raise FollowupError("blueprint-unsupported", "dependency identities are missing or ambiguous")
            by_name.setdefault(name, []).append(row)
            by_id[identity] = row
        unknown = [k for k in assignment if k not in by_name and k not in by_id]
        if unknown:
            raise FollowupError("unknown-declaration", "assignment contains a package not present in the published planning scope")
        policy = bp.get("policy") or {}
        contract = policy.get("decisionContract") or {}
        from deploom_mcp.decisions import normalize_decision_contract
        try:
            contract = normalize_decision_contract(contract)
        except ValueError as exc:
            raise FollowupError("blueprint-contract", "published decision contract cannot be normalized") from exc
        excludes = set(policy.get("exclusions") or [])
        mutable = contract.get("mutablePackages")
        approved = contract.get("approvedVersionLines") or {}
        checks = []
        selected = []
        changes = 0
        replan = False
        hard = False
        for row in rows:
            name, identity = row["package"], row["declarationId"]
            baseline = _row_baseline(row)
            if name in assignment and identity not in assignment and len(by_name[name]) > 1:
                # Package-only syntax is safe only for a uniform original
                # assignment. Otherwise an agent must select declaration IDs.
                previous = {_row_baseline(r) for r in by_name[name]}
                if len(previous) != 1:
                    raise FollowupError("ambiguous-package", "use declarationId for a package declared with different baselines")
            selected_version = assignment.get(identity, assignment.get(name, baseline))
            current = row.get("current")
            if not isinstance(selected_version, str) or not EXACT_VERSION.fullmatch(selected_version):
                checks.append({"code": "VERSION_UNKNOWN", "severity": "REVIEW",
                               "declarationId": identity, "package": name})
                continue
            selected.append({"declarationId": identity, "package": name,
                             "current": current, "publishedTarget": baseline,
                             "candidate": selected_version})
            # Inspect contractual hard boundaries even when the candidate
            # matches a published target: an inconsistent source plan should
            # not be blindly accepted as authorized.
            if selected_version != current and (name in excludes or row.get("status") == "EXCLUDED"):
                checks.append({"code": "EXCLUDED_PACKAGE_CHANGED", "severity": "BLOCKER",
                               "declarationId": identity, "package": name})
                hard = True
            if mutable is not None and name not in mutable and selected_version != current:
                checks.append({"code": "IMMUTABLE_PACKAGE_CHANGED", "severity": "BLOCKER",
                               "declarationId": identity, "package": name})
                hard = True
            current_match = EXACT_VERSION.fullmatch(current or "")
            candidate_match = EXACT_VERSION.fullmatch(selected_version)
            allowed = approved.get(name)
            if allowed is None and contract["objective"] == "latest-compatible-in-approved-lines":
                allowed = [int(current_match.group(1))] if current_match else None
            if allowed is not None and int(candidate_match.group(1)) not in allowed and selected_version != current:
                checks.append({"code": "MAJOR_LINE_NOT_APPROVED", "severity": "BLOCKER",
                               "declarationId": identity, "package": name,
                               "approvedMajors": allowed, "candidateMajor": int(candidate_match.group(1))})
                hard = True
            elif allowed is None and contract["objective"] == "latest-compatible-in-approved-lines":
                checks.append({"code": "MAJOR_AUTHORITY_UNKNOWN", "severity": "REVIEW",
                               "declarationId": identity, "package": name})
            if selected_version == baseline:
                continue
            changes += 1
            if contract["targetMode"] == "pinned":
                replan = True
                checks.append({"code": "PINNED_TARGET_CHANGE_REQUIRES_REPLAN", "severity": "REVIEW",
                               "declarationId": identity, "package": name})
            checks.append({"code": "ALTERNATIVE_PEER_SECURITY_POLICY_NOT_RECHECKED", "severity": "REVIEW",
                           "declarationId": identity, "package": name,
                           "note": "Published target evidence cannot be assigned to another exact version."})
        if len(selected) < len(rows):
            checks.append({"code": "INCOMPLETE_CURRENT_OR_TARGET_VERSION", "severity": "REVIEW"})
        coverage = bp.get("workspaceCoverage") or {}
        if coverage.get("reconciliationRequired") or coverage.get("discoveryComplete") is not True:
            checks.append({"code": "WORKSPACE_RECONCILIATION_REQUIRED", "severity": "REVIEW",
                           "note": "A selected-manifest plan is not a jointly solved workspace."})
        if (bp.get("status") or {}).get("draftStatus") != "DRAFT_READY":
            checks.append({"code": "PARTIAL_BLUEPRINT", "severity": "REVIEW"})
        evidence = bp.get("decisionEvidence") or []
        if changes == 0:
            for item in evidence:
                if item.get("severity") == "BLOCKER":
                    checks.append({"code": "PUBLISHED_PLAN_BLOCKER", "severity": "BLOCKER",
                                   "evidenceCode": item.get("code"), "evidenceRef": item.get("evidenceRef"),
                                   "package": item.get("package"), "scope": item.get("scope")})
                    hard = True
        elif any(x.get("severity") == "BLOCKER" for x in evidence):
            checks.append({"code": "PUBLISHED_BLOCKERS_REQUIRE_REASSESSMENT", "severity": "REVIEW",
                           "note": "A different assignment does not inherit resolved/unresolved conflict status."})
        freshness = source_freshness(bp)
        verdict = ("STALE_BLUEPRINT" if freshness["status"] == "STALE" else
                   "BLOCKED_BY_KNOWN_CONSTRAINTS" if hard else
                   "REPLAN_REQUIRED" if replan else
                   "REVIEW_REQUIRED" if changes or any(c["severity"] == "REVIEW" for c in checks)
                   or freshness["status"] == "UNKNOWN" else
                   "MATCHES_PUBLISHED_PLAN")
        return {
            "ok": True, "protocol": "assignment-assessment", "schemaVersion": 1,
            "verdict": verdict, "authority": "PLANNING_ONLY",
            "verificationStatus": "NOT_VERIFIED", "compatibility": "UNKNOWN",
            "installedTreeVerified": False, "policyAttainment": "NOT_RECOMPUTED",
            "peerConstraints": "NOT_RESOLVED_FOR_ALTERNATIVE",
            "securityAssessment": "NOT_RECOMPUTED_FOR_ALTERNATIVE",
            "sourceFreshness": freshness,
            "decision": {"mode": contract["targetMode"], "objective": contract["objective"],
                         "publishedDecisionDigest": bp["identities"]["decisionDigest"],
                         "blueprintSha256": file_sha,
                         "changedDeclarationsFromPublishedPlan": changes,
                         "totalDeclarations": len(rows)},
            "selection": selected[:150],
            "selectionClipped": len(selected) > 150,
            "checks": checks[:150], "totalChecks": len(checks),
            "checksClipped": len(checks) > 150,
            "nextAction": ("REGENERATE_BLUEPRINT" if freshness["status"] == "STALE" else
                           "RESOLVE_KNOWN_CONSTRAINTS" if hard else
                           "REQUEST_REPLAN" if replan else
                           "VERIFY_ALTERNATIVE_PEERS_SECURITY_POLICY_AND_RUNTIME" if changes else
                           "REVIEW_PUBLISHED_EVIDENCE_AND_VERIFY_INSTALL_RUNTIME"),
        }
    except FollowupError as exc:
        return _error(exc)


def explain_conflict(*, blueprint_path: str, package: str | None = None,
                     expected_decision_digest: str | None = None,
                     max_items: int = 20) -> dict[str, Any]:
    """Explain existing typed conflict/uncertainty evidence, never invent fixes."""
    try:
        if package is not None and (not isinstance(package, str) or not package.strip() or len(package) > 214):
            raise FollowupError("invalid-package", "package must be a non-empty package name")
        if type(max_items) is not int or not 1 <= max_items <= 50:
            raise FollowupError("invalid-limit", "max_items must be an integer from 1 through 50")
        bp, file_sha = load_blueprint(blueprint_path, expected_decision_digest)
        rows = bp.get("dependencies") or []
        if package is not None and not any(x.get("package") == package for x in rows):
            raise FollowupError("unknown-declaration", "package is not in the selected manifest's Blueprint")
        evidence = bp.get("decisionEvidence") or []
        reasons = [x for x in evidence if x.get("severity") in ("BLOCKER", "REVIEW")
                   and (package is None or x.get("package") == package
                        or x.get("scope") == "aggregate"
                        or (x.get("scope") == "project" and any(
                            row.get("project") == x.get("project")
                            for row in rows if row.get("package") == package)))]
        reasons.sort(key=lambda x: (0 if x.get("severity") == "BLOCKER" else 1,
                                    str(x.get("evidenceRef") or "")))
        selected_rows = [x for x in rows if package is None or x.get("package") == package]
        packages = sorted({x.get("package") for x in selected_rows if isinstance(x.get("package"), str)})
        components = []
        for idx, component in enumerate(bp.get("solverComponents") or []):
            if not isinstance(component, dict):
                continue
            names = component.get("packages") or component.get("members") or []
            if package is None or package in names:
                components.append({"index": idx, "packages": names[:30] if isinstance(names, list) else [],
                                   "source": "PUBLISHED_SOLVER_COMPONENT_NOT_A_NEW_SOLVE"})
        simplified = []
        for item in reasons[:max_items]:
            simplified.append({k: (str(item[k])[:512] if k == "message" else item[k])
                               for k in ("code", "severity", "scope", "project", "package",
                                         "lifecycle", "message", "evidenceRef", "nextAction") if k in item})
        freshness = source_freshness(bp)
        return {
            "ok": True, "protocol": "conflict-explanation", "schemaVersion": 1,
            "verdict": ("STALE_BLUEPRINT" if freshness["status"] == "STALE" else
                        "DOCUMENTED_CONSTRAINTS" if reasons else "NO_DOCUMENTED_BLOCKER_OR_REVIEW"),
            "authority": "PLANNING_ONLY", "verificationStatus": "NOT_VERIFIED",
            "compatibility": "UNKNOWN", "sourceFreshness": freshness,
            "source": {"decisionDigest": bp["identities"]["decisionDigest"],
                       "blueprintSha256": file_sha},
            "package": package, "packagesInScope": packages[:50],
            "reasons": simplified, "totalReasons": len(reasons),
            "reasonsClipped": len(reasons) > max_items, "requiresArtifactRead": len(reasons) > max_items,
            "solverComponents": components[:10], "solverComponentsClipped": len(components) > 10,
            "candidateVersionsFromPublishedPlan": [
                {"declarationId": r.get("declarationId"), "package": r.get("package"),
                 "current": r.get("current"), "publishedTarget": r.get("target"),
                 "latest": r.get("latest"), "status": r.get("status"),
                 "note": "Candidate versions are not independently checked alternatives."}
                for r in selected_rows[:50]
            ],
            "workspaceReconciliationRequired": bool((bp.get("workspaceCoverage") or {}).get("reconciliationRequired")),
            "nextAction": ("REGENERATE_BLUEPRINT" if freshness["status"] == "STALE" else
                           "FOLLOW_TYPED_EVIDENCE_NEXT_ACTIONS" if reasons else
                           "INSPECT_FULL_BLUEPRINT_AND_VERIFY_EXTERNAL_CONSTRAINTS"),
            "limitations": "Explanations come from published evidence; no new Z3 solve, registry query, npm install or runtime check.",
        }
    except FollowupError as exc:
        return _error(exc)
