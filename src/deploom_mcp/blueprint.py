#!/usr/bin/env python3
"""Upgrade Blueprint: headless planning boundary for the DepLoom ecosystem.

Stage 1 adapter. It does NOT reimplement planning. It drives the existing
planner CLI (`dependency_live_roadmap_generator.py`, Draft mode) as an
isolated subprocess, reads the published `plan.json` / `result.json` /
`solver-components.json` and projects them into a stable, machine-readable
"Upgrade Blueprint" document (schemaVersion 2).

Guarantees this boundary provides:

* No writes into the analysed project: the child runs with `cwd` set to a
  caller-controlled control workspace inside `output_dir`, and every
  side-effect path (artifacts, dashboard state, history, index) is pinned
  explicitly. Only `package.json` / the lockfile of the project are read.
* No authoritative claims: the returned blueprint always carries
  `verificationStatus=NOT_VERIFIED`, `authority=PLANNING_ONLY`,
  `compatibility=UNKNOWN` and `parallelExecutionSafety=UNPROVEN`. The
  reserved statuses CONSTRAINT_SATISFIED / MATERIALIZED / PROJECT_VERIFIED
  are declared in the schema but are never emitted by a planning-only run.
* Identity: byte hashes of published artifacts are retained. Semantic input,
  decision and published-evidence hashes are separate; they do not promise
  reproducibility while registry/OSV evidence or runtime changes.
* Isolation: nothing is imported from the generator; the planner runs as a
  child OS process, so this module cannot mutate planner global state and
  planner failures degrade into machine-readable errors, not exceptions.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from deploom_mcp.process_runner import ProcessRun, run_process, run_process_async
from deploom_mcp.decisions import (
    MAX_DRAFT_CANDIDATES, enrich_decisions, normalize_decision_contract,
    requires_contract_support, runtime_provenance, workspace_coverage,
)
from deploom_mcp.distribution import resolve_core_dir, blueprint_schema_path
from deploom_mcp.target_assessment import TARGET_PROJECTION_FIELDS, projected_health
from deploom_mcp.registry_projection import (
    project_no_target_code, project_registry_candidate, reconcile_registry_aggregate,
)

BLUEPRINT_SCHEMA_VERSION = 2
KIND = "upgrade-blueprint"

# Draft row status -> Upgrade Blueprint status vocabulary.
# "proposed" rows carry a concrete target but were NEVER physically verified.
_DRAFT_TO_BLUEPRINT = {
    "proposed": "PROPOSED",
    "no-change": "NO_CHANGE",
    "ok": "POLICY_SATISFIED",
    "excluded": "EXCLUDED",
    "deferred": "DEFERRED",
    "blocked": "BLOCKED",
    "candidate-search-truncated": "CANDIDATE_SEARCH_TRUNCATED",
    "no-target": "NO_TARGET",
    "unknown-security": "UNKNOWN_SECURITY",
    "unknown-metadata": "UNKNOWN_METADATA",
    "peer-compat-unresolved": "PEER_UNRESOLVED",
    "not-attempted": "NOT_ATTEMPTED",
    "interrupted": "INTERRUPTED",
}

# Reserved for the verified pipeline. Planning-only runs must NEVER claim them.
RESERVED_VERIFIED_STATUSES = (
    "CONSTRAINT_SATISFIED",
    "MATERIALIZED",
    "PROJECT_VERIFIED",
)

# Statuses that mean "the plan genuinely needs human/AI clarification".
NEEDS_REVIEW = (
    "CANDIDATE_SEARCH_TRUNCATED",
    "BLOCKED",
    "NO_TARGET",
    "UNKNOWN_SECURITY",
    "UNKNOWN_METADATA",
    "PEER_UNRESOLVED",
)

GENERATOR_BASENAME = "dependency_live_roadmap_generator.py"

# Git-tracked JSON Schema for the published blueprint; validated at runtime
# before a successful result is returned (fail-closed).
CONTRACTS_DIRNAME = "contracts"
BLUEPRINT_SCHEMA_FILENAME = "upgrade-blueprint.schema.json"

# Draft statuses the planning-only boundary may publish. Anything else means an
# unexpected/manifest mode that this adapter must NOT guess about (fail-closed).
PLANNING_DRAFT_STATUSES = ("DRAFT_READY", "DRAFT_PARTIAL")

# Default exclusion reason used when the caller gives package names only.
# The planner's --exclude-dependency contract requires an explicit reason.
DEFAULT_EXCLUSION_REASON = "explicit scope exclusion from the planning request (get_upgrade_plan)"


def _default_core_dir() -> Path:
    """Standalone repository layout: the pinned planning core lives at
    ``<repo root>/upstream/deploom``, while this module lives at
    ``<repo root>/src/deploom_mcp/blueprint.py``."""
    return resolve_core_dir()


@dataclass(frozen=True)
class BlueprintPolicy:
    """The policy the planner was asked to satisfy (yellow/green closure).

    ``exclusions`` are package names excluded from planning scope; they are
    passed to the planner as real ``--exclude-dependency`` overrides (applied
    BEFORE planning), never filtered out of the answer afterwards.
    """

    target_level: str = "yellow"
    min_lag_ok_pct: int = 80
    intent: Optional[Dict[str, Any]] = None
    acceptance_policy: Optional[Dict[str, Any]] = None
    exclusions: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BlueprintOptions:
    """Tuning knobs for the headless Draft run that backs a blueprint."""

    registry: Optional[str] = None
    include_prerelease: bool = False
    use_system_proxy: bool = False
    draft_deadline_seconds: float = 60.0
    draft_max_candidates: int = 3
    max_candidates: Optional[int] = None
    http_timeout: Optional[int] = None
    registry_metadata_cache: Optional[str] = None
    registry_metadata_ttl: float = 3600.0
    output_dir: Optional[str] = None
    python: Optional[str] = None
    generator_path: Optional[str] = None
    core_dir: Optional[str] = None
    run_timeout_seconds: Optional[float] = None
    workspace_id: Optional[str] = None
    previous_evidence_digest: Optional[str] = None


@dataclass(frozen=True)
class UpgradeBlueprintResult:
    ok: bool
    run_id: str
    draft_status: Optional[str]
    blueprint: Optional[Dict[str, Any]]
    blueprint_path: Optional[str]
    artifacts_dir: Optional[str]
    plan_path: Optional[str]
    manifest_path: Optional[str]
    elapsed: float
    python_version: str
    error: Optional[Dict[str, Any]] = None
    stdout: str = ""
    stderr: str = ""


def _python_version() -> str:
    try:
        return sys.version.split()[0]
    except Exception:  # pragma: no cover - defensive
        return "unknown"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _read_json(path: Path) -> Dict[str, Any]:
    # utf-8-sig: BOM-tolerant for JSON file inputs (repo contract,
    # tests/regression/test_json_bom_inputs.py).
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _flatten_rows(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for proposal in plan.get("proposals") or []:
        for row in proposal.get("rows") or []:
            rows.append(dict(row))
    return rows


def draft_status_to_blueprint(status: str) -> str:
    """Map a planner draft status to the closed blueprint vocabulary.

    Anything outside the known mapping collapses to UNKNOWN so an unexpected
    planner status can never be misread, and the reserved verified statuses
    (CONSTRAINT_SATISFIED / MATERIALIZED / PROJECT_VERIFIED) are structurally
    unreachable from a planning-only source.
    """
    key = str(status or "").strip().lower()
    return _DRAFT_TO_BLUEPRINT.get(key, "UNKNOWN")


def _recommendation_groups(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group rows by the planner display group (1..5) without claiming
    parallel-execution safety; the z3 solver's own components are the stronger
    coupling signal and are exposed separately as solverComponents."""
    buckets: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        group = int(row.get("group") or 0)
        bucket = buckets.setdefault(group, {"group": group, "packages": [], "count": 0, "needsReview": False})
        if row.get("package") not in bucket["packages"]:
            bucket["packages"].append(row.get("package"))
        bucket["count"] += 1
        if draft_status_to_blueprint(_as_text(row.get("status"))) in NEEDS_REVIEW:
            bucket["needsReview"] = True
    ordered = [buckets[k] for k in sorted(buckets)]
    for bucket in ordered:
        bucket["packages"] = sorted(bucket["packages"])
        bucket["kind"] = "display-priority"
        bucket["parallelSafety"] = "UNPROVEN"
    return ordered


def _projections(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    meta = manifest.get("metadata") or {}
    keys = (
        "total", "unknown", "metadataKnown", "metadataTotal", "securityKnown",
        "securityTotal", "securityUnknown", "insufficientData", "processed",
        "processedTotal", "pending", "interrupted", "registryFailed",
        "osvUnknown", "noTarget", "candidateTruncated", "candidateTruncatedTargetless",
        "candidateTruncatedProposed", "blocked", "peerCompatUnresolved", "ok",
        *TARGET_PROJECTION_FIELDS,
    )
    return {k: meta.get(k) for k in keys if k in meta}


def transcode_upgrade_blueprint(
    plan: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    project_path: str,
    project_name: str,
    policy: BlueprintPolicy,
    artifacts: Mapping[str, str],
    blueprint_path: str,
) -> Dict[str, Any]:
    """Project planner plan.json/result.json into an Upgrade Blueprint.

    Pure projection: no recomputation, no invented evidence. All statuses are
    lifted verbatim from the Draft manifest (`verificationStatus`,
    `authority`, `compatibility`, `status`, `partialReason`) so a planning-only
    run can never present itself as verified.
    """
    rows = _flatten_rows(plan)
    dependencies = []
    for row in rows:
        status_draft = _as_text(row.get("status"))
        status_bp = draft_status_to_blueprint(status_draft)
        dependencies.append({
            "project": row.get("project"),
            "package": row.get("package"),
            "kind": row.get("kind"),
            "requestedSpec": row.get("requestedSpec"),
            "current": row.get("current"),
            "target": row.get("target"),
            "targetYellow": row.get("targetYellow"),
            "targetGreen": row.get("targetGreen"),
            "latest": row.get("latest"),
            "status": status_bp,
            "needsReview": status_bp in NEEDS_REVIEW,
            "reason": row.get("reason"),
            "group": row.get("group"),
            "breakingChanges": list(row.get("breakingChanges") or []),
            "migrationNotes": list(row.get("migrationNotes") or []),
            "evidence": {
                "candidateSearchTruncated": bool(row.get("candidateSearchTruncated")),
                "peerCompatUnresolved": bool(row.get("peerCompatUnresolved")),
                "draftUnknown": row.get("draftUnknown"),
                "scanState": row.get("scanState"),
            },
        })
        # Optional additive Core evidence. The legacy bundled Core does not
        # publish it; do not synthesize "zero rejected" on its behalf.
        diagnostic = project_registry_candidate(row.get("registryCandidateDiagnostics"))
        if diagnostic is not None:
            dependencies[-1]["registryCandidateDiagnostics"] = diagnostic
        code = project_no_target_code(row.get("noTargetDiagnosticCode"), status_draft, diagnostic)
        if code is not None:
            dependencies[-1]["noTargetDiagnosticCode"] = code

    per_project = {}
    for proposal in plan.get("proposals") or []:
        name = _as_text(proposal.get("project"))
        if not name:
            continue
        health = proposal.get("health") or {}
        per_project[name] = {
            "total": health.get("total"),
            "known": health.get("known"),
            "lagOkPct": health.get("lag_ok_pct"),
            "critical": health.get("critical"),
            "high": health.get("high"),
            "moderate": health.get("moderate"),
            "low": health.get("low"),
            "unknown": health.get("unknown"),
            "excluded": health.get("excluded"),
            "insufficientData": bool(health.get("insufficient_data")),
            **projected_health(health),
        }

    counts = plan.get("counts") or {}
    # Status counts derived from the actual per-row statuses (no double counting:
    # the draft `counts` dict adds a SECONDARY unknown-security counter that is
    # not a row status). The raw published counters stay visible as draftCounts.
    row_status_counts: Dict[str, int] = {}
    for row in rows:
        key = draft_status_to_blueprint(_as_text(row.get("status")))
        row_status_counts[key] = row_status_counts.get(key, 0) + 1
    needs_review_count = sum(1 for row in rows if draft_status_to_blueprint(_as_text(row.get("status"))) in NEEDS_REVIEW)
    proposed_count = int(row_status_counts.get("PROPOSED", 0))

    meta = manifest.get("metadata") or {}
    by_project = manifest.get("inputFilesByProject") or {}
    input_files = by_project.get(project_name) or next(iter(by_project.values()), []) if by_project else []
    source_inputs = [str(x.get("name") or "") for x in input_files if isinstance(x, dict) and x.get("name")]
    project_resolved = Path(project_path).resolve()
    input_files_abs = []
    for name in source_inputs:
        p = Path(name)
        if p.is_absolute():
            input_files_abs.append(str(p))
        else:
            # Project-relative names ("package.json", "./package.json") are
            # relative to the analysed project, NOT to the process CWD.
            input_files_abs.append(str((project_resolved / p).resolve()))
    input_files_unique: List[str] = []
    for value in input_files_abs:
        if value not in input_files_unique:
            input_files_unique.append(value)

    contract = normalize_decision_contract(manifest.get("decisionContract") or policy.intent)
    blueprint = {
        "schemaVersion": BLUEPRINT_SCHEMA_VERSION,
        "kind": KIND,
        "generatedAt": manifest.get("generatedAt"),
        "elapsedMs": manifest.get("elapsedMs"),
        "deadline": manifest.get("deadline"),
        "run": {
            "runId": manifest.get("runId"),
            "workspaceId": manifest.get("workspaceId"),
            "projectId": manifest.get("projectId"),
            "mode": manifest.get("mode"),
            "generator": GENERATOR_BASENAME,
        },
        "source": {
            "project": {"name": project_name, "path": str(project_resolved)},
            "inputFiles": input_files_unique,
            "inputHashes": manifest.get("inputHashes") or {},
            "semanticInputHash": manifest.get("semanticInputHash"),
            "semanticInputsByProject": manifest.get("semanticInputsByProject") or {},
            "inputCoverage": manifest.get("inputCoverage"),
        },
        "policy": {
            "targetLevel": policy.target_level,
            "minLagOkPct": policy.min_lag_ok_pct,
            "policyHash": manifest.get("policyHash"),
            "intent": policy.intent,
            "acceptancePolicy": policy.acceptance_policy,
            "exclusions": list(policy.exclusions),
            "decisionContract": contract,
            "coreContractSupport": "V1" if manifest.get("decisionContract") else "LEGACY",
        },
        "objectiveEvidence": manifest.get("objectiveEvidence") or {
            "objective": contract["objective"], "status": "CANDIDATE_PLAN_ONLY",
            "freshnessOptimality": "NOT_PROVEN", "physicalCompatibility": "UNKNOWN",
        },
        "status": {
            "draftStatus": manifest.get("status"),
            "verificationStatus": manifest.get("verificationStatus", "NOT_VERIFIED"),
            "authority": manifest.get("authority", "PLANNING_ONLY"),
            "compatibility": manifest.get("compatibility", "UNKNOWN"),
            "partialReason": manifest.get("partialReason"),
            "parallelExecutionSafety": "UNPROVEN",
            "verificationBoundaryNote": (
                "planning-only Draft output: statuses PROPOSED/NO_CHANGE/POLICY_SATISFIED/"
                "DEFERRED/EXCLUDED/NEEDS_REVIEW are planning verdicts; "
                "CONSTRAINT_SATISFIED/MATERIALIZED/PROJECT_VERIFIED are reserved "
                "for the verified pipeline and are NOT emitted by this boundary."
            ),
        },
        "summary": {
            "totalDependencies": int(counts.get("total", 0)),
            "proposed": proposed_count,
            "needsReview": needs_review_count,
            "statusCounts": row_status_counts,
            "draftCounts": {
                k: v for k, v in counts.items() if isinstance(v, int)
            },
            "metadataKnown": meta.get("metadataKnown"),
            "metadataTotal": meta.get("metadataTotal"),
            "securityKnown": meta.get("securityKnown"),
            "securityTotal": meta.get("securityTotal"),
            "insufficientData": bool(meta.get("insufficientData")),
            "processed": meta.get("processed"),
            "processedTotal": meta.get("processedTotal"),
            "done": _as_text(manifest.get("summary")),
        },
        "dependencies": dependencies,
        "recommendationGroups": _recommendation_groups(rows),
        "solverComponents": list(manifest.get("solverComponents") or []),
        "unknowns": list(plan.get("unknowns") or []),
        "conflicts": list(plan.get("conflicts") or []),
        "unresolved": list(plan.get("unresolved") or []),
        "perProject": per_project,
        "projections": _projections(manifest),
        "hashes": {
            "plan": (manifest.get("hashes") or {}).get("plan"),
            "prompt": (manifest.get("hashes") or {}).get("prompt"),
        },
        "artifacts": dict(artifacts),
        "blueprintFile": str(Path(blueprint_path).resolve()),
    }
    registry_diagnostics = reconcile_registry_aggregate(
        plan.get("registryDiagnostics"), rows, dependencies,
    )
    if registry_diagnostics is not None:
        blueprint["registryDiagnostics"] = registry_diagnostics
    enrich_decisions(blueprint)
    return blueprint


class BlueprintError(RuntimeError):
    """Raised when the backing Draft run cannot produce a blueprint.

    The primary API (`generate_upgrade_blueprint`) returns an
    `UpgradeBlueprintResult` instead of raising; this exception is available
    for callers that prefer exceptions over result objects.
    """

    def __init__(self, code: str, message: str, detail: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.detail = detail


def _error(code: str, message: str, detail: Optional[str] = None) -> Dict[str, Any]:
    return {"code": code, "message": message, "detail": detail}


def validate_planning_contract(manifest: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Fail-closed planning-only contract check on a Draft manifest.

    Returns an error dict when the manifest claims anything other than
    planning-only semantics, so an unexpected/proven upstream mode can never be
    silently copied into a "successful" blueprint. Unset status fields are
    treated as absent (the safe defaults are NOT_VERIFIED/PLANNING_ONLY/UNKNOWN
    in the projection); a non-empty value that is not the allowed one is a
    contract violation.
    """
    allowed = {
        "verificationStatus": "NOT_VERIFIED",
        "authority": "PLANNING_ONLY",
        "compatibility": "UNKNOWN",
    }
    for field, expected in allowed.items():
        value = manifest.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or value != expected:
            return _error(
                "contract-violation",
                f"planner manifest violates the planning-only contract: "
                f"{field}={value!r}, expected {expected!r}",
                f"manifest field {field}",
            )
    status = manifest.get("status")
    if status is None:
        return _error(
            "contract-violation",
            "planner manifest does not declare a draft status",
            "manifest field status",
        )
    if not isinstance(status, str) or status not in PLANNING_DRAFT_STATUSES:
        return _error(
            "contract-violation",
            f"planner manifest declares an unhandled draft status {status!r}; "
            f"allowed: {', '.join(PLANNING_DRAFT_STATUSES)}",
            "manifest field status",
        )
    return None


def _schema_path() -> Path:
    return blueprint_schema_path()


def _validate_blueprint_schema(blueprint: Mapping[str, Any], schema_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Runtime JSON Schema validation of the final blueprint (fail-closed).

    The published blueprint MUST conform to the shipped schema before a
    successful result is returned. ``jsonschema`` is a runtime dependency of
    this boundary; if it is unavailable the check fails closed instead of
    silently skipping.
    """
    path = Path(schema_path) if schema_path else _schema_path()
    if not path.is_file():
        return _error("schema-missing", f"upgrade-blueprint JSON Schema not found at {path}")
    try:
        import jsonschema
    except ImportError:
        return _error(
            "schema-validator-unavailable",
            "jsonschema is a required runtime dependency of the blueprint boundary",
        )
    try:
        schema = _read_json(path)
        jsonschema.validate(blueprint, schema)
    except jsonschema.ValidationError as exc:
        where = ".".join(str(p) for p in exc.absolute_path) or "<root>"
        return _error(
            "schema-invalid",
            f"blueprint failed JSON Schema validation: {_as_text(exc.message)} (at {where})",
        )
    except (OSError, ValueError) as exc:
        return _error("schema-invalid", f"cannot read blueprint JSON Schema: {exc}")
    return None


def _stable_sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _verify_published_hash(kind: str, declared: Any, path: Optional[Path]) -> Optional[Dict[str, Any]]:
    """Verify one published artifact hash (plan/prompt) against the file bytes.

    The planner hashes the plan/prompt JSON as their exact on-disk bytes
    (stable_sha256_bytes, no normalisation). A declared hash MUST match those
    bytes; a missing declared hash is not an error (edge cases), but a missing
    file while a hash is declared is a broken artifact set.
    """
    if not declared:
        return None
    if path is None or not path.is_file():
        return _error(
            "hash-mismatch",
            f"{kind} hash declared but the artifact file is missing",
            f"declared {kind} hash {declared!r}",
        )
    actual = _stable_sha256_bytes(path.read_bytes())
    if actual != declared:
        return _error(
            "hash-mismatch",
            f"{kind} file content does not match the manifest {kind} hash",
            f"declared={declared} actual={actual}",
        )
    return None


def _read_plan(plan_path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Read and structurally validate plan.json (mandatory artifact)."""
    if not plan_path.is_file():
        return None, _error("plan-missing", f"plan.json was not published at {plan_path}")
    try:
        plan = _read_json(plan_path)
    except (OSError, ValueError) as exc:
        return None, _error("plan-invalid", f"plan.json is not valid JSON: {exc}")
    if not isinstance(plan, dict):
        return None, _error("plan-invalid", "plan.json must be a JSON object")
    if not isinstance(plan.get("proposals"), list):
        return None, _error("plan-invalid", "plan.json must contain a 'proposals' array")
    if not isinstance(plan.get("counts"), dict):
        return None, _error("plan-invalid", "plan.json must contain a 'counts' object")
    return plan, None


def _prepare_argv_full(
    generator: Path,
    projects_file: Path,
    artifacts_dir: Path,
    state_dir: Path,
    history_dir: Path,
    run_id: str,
    workspace_id: str,
    project_id: str,
    policy: BlueprintPolicy,
    options: BlueprintOptions,
) -> List[str]:
    argv = [
        str(generator),
        "--projects-file", str(projects_file),
        "--draft-baseline",
        "--mode", "draft",
        "--run-id", run_id,
        "--workspace-id", workspace_id,
        "--project-id", project_id,
        "--artifacts-dir", str(artifacts_dir),
        "--dashboard-state", str(state_dir / "dashboard-state.json"),
        "--events-log", str(history_dir / "events.jsonl"),
        "--runs-dir", str(history_dir / "runs"),
        "--index-file", str(history_dir / "index.md"),
        "--draft-deadline-seconds", str(options.draft_deadline_seconds),
        "--draft-max-candidates", str(options.draft_max_candidates),
        "--target-level", policy.target_level,
        "--min-lag-ok-pct", str(policy.min_lag_ok_pct),
    ]
    if options.registry:
        argv += ["--registry", options.registry]
    for package in policy.exclusions:
        argv += ["--exclude-dependency", f"{package}|{DEFAULT_EXCLUSION_REASON}"]
    if options.include_prerelease:
        argv += ["--include-prerelease"]
    if options.use_system_proxy:
        argv += ["--use-system-proxy"]
    if options.max_candidates is not None:
        argv += ["--max-candidates", str(options.max_candidates)]
    if options.http_timeout is not None:
        argv += ["--timeout", str(options.http_timeout)]
    if options.registry_metadata_cache:
        argv += [
            "--registry-metadata-cache", str(Path(options.registry_metadata_cache).resolve()),
            "--registry-metadata-ttl", str(options.registry_metadata_ttl),
        ]
    else:
        argv += ["--registry-metadata-ttl", "0"]
    return argv


def _exec_env(policy: BlueprintPolicy, run_id: str, workspace_id: str, project_id: str, options: BlueprintOptions) -> Dict[str, str]:
    env = dict(os.environ)
    # Pin the effective product mode and identity; strip any DEPLOOM_* keys we
    # do not explicitly control so an embedding Draft context cannot leak in.
    for key in list(env):
        if key.startswith("DEPLOOM_"):
            del env[key]
    env["DEPLOOM_MODE"] = "draft"
    env["DEPLOOM_RUN_ID"] = run_id
    env["DEPLOOM_WORKSPACE_ID"] = workspace_id
    env["DEPLOOM_PROJECT_ID"] = project_id
    env["DEPLOOM_BASELINE_TARGET_LEVEL"] = policy.target_level
    env["DEPLOOM_BASELINE_MIN_LAG_OK_PCT"] = str(policy.min_lag_ok_pct)
    env["DEPLOOM_DRAFT_DEADLINE_SECONDS"] = str(options.draft_deadline_seconds)
    env["DEPLOOM_DRAFT_MAX_CANDIDATES"] = str(options.draft_max_candidates)
    if policy.intent is not None:
        env["DEPLOOM_BASELINE_INTENT_JSON"] = _json_dumps(policy.intent)
    if policy.acceptance_policy is not None:
        env["DEPLOOM_ACCEPTANCE_POLICY_JSON"] = _json_dumps(policy.acceptance_policy)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _expand_short_names(path_str: str) -> str:
    """Canonicalize 8.3 short-name components to their long form on Windows
    WITHOUT following reparse points.

    ``GetLongPathNameW`` expands each component's short name (``EXAMPL~1`` ->
    ``example-user``) but treats a junction/symlink entry as a plain directory
    entry, so the reparse component survives on the lexical path -- exactly
    what the filesystem guard needs to detect a route that passes THROUGH the
    project. Non-existent tail components (fresh managed run dirs) are kept
    verbatim. Identity on POSIX.
    """
    if os.name != "nt":
        return path_str
    try:
        import ctypes
    except Exception:  # pragma: no cover - defensive
        return path_str
    get_long = ctypes.windll.kernel32.GetLongPathNameW

    def long_of(candidate: str) -> Optional[str]:
        buf = ctypes.create_unicode_buffer(32768)
        count = get_long(candidate, buf, len(buf))
        if count and count < len(buf):
            return buf[:count]
        return None

    drive, _ = os.path.splitdrive(os.path.abspath(path_str))
    root = (drive + os.sep) if drive else os.sep
    current = root
    rest = os.path.abspath(path_str)
    tail = rest[len(root):].lstrip("\\/")
    for part in tail.split(os.sep):
        candidate = os.path.join(current, part)
        long_v = long_of(candidate)
        current = long_v if long_v is not None else candidate
    return current


def _path_forms(path: Path) -> Tuple[str, str]:
    """Normalized absolute path forms used by the filesystem guard.

    Returns ``(lexical, real)``: both absolute and normalized; ``lexical`` keeps
    symlink/junction components (``normpath``/``abspath`` only, no follow) while
    8.3 short names are expanded so containment comparisons survive differing
    spellings of the same directory; ``real`` follows them
    (``os.path.realpath``). ``os.path.normcase`` makes the comparison
    platform-correct (Windows drive letters, case folding, UNC; identity on
    POSIX). Non-strict, so a not-yet-created directory is fine.
    """
    raw = os.path.expanduser(os.fspath(path))
    lexical = os.path.normcase(_expand_short_names(os.path.abspath(os.path.normpath(raw))))
    real = os.path.normcase(os.path.realpath(lexical))
    return lexical, real


def _is_within(child: str, parent: str) -> bool:
    separ = os.sep
    if parent != separ:
        parent = parent.rstrip(separ)
    if child == parent:
        return True
    return child.startswith(parent + separ)


def _paths_overlap(a: str, b: str) -> bool:
    """True when two (already normalized) paths stand in the ancestor /
    descendant / equal relation -- the forbidden overlap direction."""
    return _is_within(a, b) or _is_within(b, a)


def resolve_output_outside(project: Path, out: Path) -> Optional[Path]:
    """Return a safe (real, resolved) output path when ``out`` does NOT overlap
    the analysed project; ``None`` when it does.

    Overlap is checked on BOTH path forms of each side -- the lexical absolute
    path (symlink/junction components preserved) and the real path after
    follow-in -- so a symlink that routes through the project (in either
    direction) cannot smuggle writes inside it. This single guard is shared by
    the managed state root, the output root and the registry metadata cache.
    """
    for p_form in _path_forms(project):
        for o_form in _path_forms(out):
            if _paths_overlap(p_form, o_form):
                return None
    return Path(_path_forms(out)[1])


def _make_unique_run_dir(base: Path, run_id: str) -> Tuple[Path, str]:
    """Create a per-run directory exclusively; never overwrite another run.

    If the exact run directory already exists (a collided run id), a short
    suffix is appended until an unused directory is found. Raises
    ``BlueprintError`` with ``output-conflict`` when exhausted.
    """
    for attempt in range(16):
        candidate = base / run_id
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate, run_id
        except FileExistsError:
            if not candidate.is_dir():
                return candidate, run_id
            run_id = f"{run_id}-{attempt + 1}"
    raise BlueprintError(
        "output-conflict",
        f"could not allocate a unique run directory under {base}",
    )


@dataclass
class _PreparedRun:
    generator: Path
    interpreter: str
    purl: Path
    project_name: str
    run_id: str
    workspace_id: str
    project_id: str
    argv: List[str]
    env: Dict[str, str]
    timeout: float
    run_root: Path
    artifacts_dir: Path
    state_dir: Path
    manifest_path: Path
    plan_path: Path
    prompt_path: Path
    solver_path: Path
    policy: BlueprintPolicy
    options: BlueprintOptions


def _validate_boundary_inputs(pol: BlueprintPolicy, opts: BlueprintOptions) -> Optional[Dict[str, Any]]:
    """Light fail-closed validation of policy/options numeric knobs.

    The MCP layer performs its own richer input validation first; this guard
    protects the headless CLI path (and any embedding) from NaN/Inf numbers
    that would otherwise flow into the planner with undefined semantics.
    """
    if type(opts.use_system_proxy) is not bool:
        return _error("invalid-system-proxy", "use_system_proxy must be a boolean")
    deadline = opts.draft_deadline_seconds
    if isinstance(deadline, (int, float)) and (not math.isfinite(float(deadline)) or float(deadline) <= 0):
        return _error("invalid-deadline", "draft_deadline_seconds must be a finite positive number")
    if not isinstance(pol.min_lag_ok_pct, int) or not (0 <= pol.min_lag_ok_pct <= 100):
        return _error("invalid-policy", "min_lag_ok_pct must be an integer in [0, 100]")
    if isinstance(opts.draft_max_candidates, bool) or not isinstance(opts.draft_max_candidates, int) or not (1 <= opts.draft_max_candidates <= MAX_DRAFT_CANDIDATES):
        return _error("invalid-candidate-cap", f"draft_max_candidates must be an integer in [1, {MAX_DRAFT_CANDIDATES}]")
    try:
        normalize_decision_contract(pol.intent)
    except ValueError as exc:
        return _error("invalid-intent", str(exc))
    return None


def _prepare_generation(
    project_path: str,
    pol: BlueprintPolicy,
    opts: BlueprintOptions,
    project_name: Optional[str],
    run_id: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[_PreparedRun]]:
    """Resolve every input and build the child invocation (pure, no subprocess)."""
    guard = _validate_boundary_inputs(pol, opts)
    if guard is not None:
        return guard, None

    if opts.generator_path:
        generator = Path(opts.generator_path).resolve()
    elif opts.core_dir:
        generator = Path(opts.core_dir).resolve() / GENERATOR_BASENAME
    else:
        generator = _default_core_dir() / GENERATOR_BASENAME
    if not generator.is_file():
        return (
            _error(
                "core-not-found",
                f"pinned planning core not found at {generator}; "
                "ensure the submodule is initialized (git submodule update --init --recursive)",
            ),
            None,
        )
    # A venv interpreter is commonly a symlink. Resolving it changes Python's
    # environment discovery and silently drops the requested environment/deps.
    interpreter = os.path.abspath(os.path.expanduser(opts.python)) if opts.python else sys.executable

    purl = Path(project_path).expanduser().resolve().absolute()
    if not purl.exists() or not purl.is_dir():
        return _error("project-not-found", f"project directory does not exist: {purl}"), None
    if not (purl / "package.json").is_file():
        return _error("invalid-project", f"no package.json found in {purl}"), None

    project_name = project_name or purl.name

    # --- output isolation (fail-closed at the write boundary) -------------
    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    if opts.output_dir:
        base_raw = Path(opts.output_dir).expanduser()
        base = resolve_output_outside(purl, base_raw)
        if base is None:
            return (
                _error(
                    "output-inside-project",
                    f"output_dir {base_raw} must not be inside or above the analysed project {purl}",
                ),
                None,
            )
    else:
        base = resolve_output_outside(purl, Path(tempfile.gettempdir()) / f"deploom-blueprint-{run_id}")
        if base is None:
            return (
                _error(
                    "output-inside-project",
                    f"managed output overlaps the analysed project {purl}",
                ),
                None,
            )
    try:
        run_root, run_id = _make_unique_run_dir(base, run_id)
    except BlueprintError as exc:
        return _error(exc.code, str(exc)), None

    if opts.registry_metadata_cache:
        cache_raw = Path(opts.registry_metadata_cache).expanduser()
        if resolve_output_outside(purl, cache_raw) is None:
            return (
                _error(
                    "output-inside-project",
                    f"registry metadata cache {cache_raw} must not be inside the analysed "
                    f"project {purl}",
                ),
                None,
            )

    workspace_id = opts.workspace_id or f"ws-{project_name}-{run_id[4:]}"
    project_id = project_name

    control = run_root / "exec"
    control.mkdir(parents=True, exist_ok=True)
    artifacts_dir = run_root / "artifacts"
    state_dir = run_root / "state"
    history_dir = run_root / "history"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)

    projects_file = control / "projects.txt"
    projects_file.write_text(str(purl) + "\n", encoding="utf-8", newline="\n")

    argv = _prepare_argv_full(
        generator, projects_file, artifacts_dir, state_dir, history_dir,
        run_id, workspace_id, project_id, pol, opts,
    )
    env = _exec_env(pol, run_id, workspace_id, project_id, opts)

    timeout = opts.run_timeout_seconds
    if timeout is None:
        timeout = max(float(opts.draft_deadline_seconds) * 2 + 30, 120.0)
    timeout_deviation = not (isinstance(timeout, (int, float)) and math.isfinite(float(timeout)) and timeout > 0)
    if timeout_deviation or (isinstance(timeout, (int, float)) and float(timeout) > 3600):
        timeout = max(float(opts.draft_deadline_seconds) * 2 + 30, 120.0)

    draft_dir = artifacts_dir / "runs" / run_id / "draft"
    return None, _PreparedRun(
        generator=generator,
        interpreter=interpreter,
        purl=purl,
        project_name=project_name,
        run_id=run_id,
        workspace_id=workspace_id,
        project_id=project_id,
        argv=argv,
        env=env,
        timeout=float(timeout),
        run_root=run_root,
        artifacts_dir=artifacts_dir,
        state_dir=state_dir,
        manifest_path=draft_dir / "result.json",
        plan_path=draft_dir / "plan.json",
        prompt_path=draft_dir / "prompt.md",
        solver_path=draft_dir / "solver-components.json",
        policy=pol,
        options=opts,
    )


def _result_from_prepare(guard: Dict[str, Any], run_id: str, start: float) -> UpgradeBlueprintResult:
    return UpgradeBlueprintResult(
        ok=False, run_id=run_id, draft_status=None, blueprint=None,
        blueprint_path=None, artifacts_dir=None, plan_path=None, manifest_path=None,
        elapsed=time.perf_counter() - start, python_version=_python_version(),
        error=guard,
    )


def _finalize(prep: _PreparedRun, outcome: ProcessRun, start: float) -> UpgradeBlueprintResult:
    """Turn one child outcome into the final UpgradeBlueprintResult (shared by
    the sync and async paths). Verifies the plan, the planning-only contract,
    the published hashes and the JSON Schema — fail-closed on every one."""
    elapsed = time.perf_counter() - start
    draft_dir = prep.manifest_path.parent

    if outcome.timed_out:
        return UpgradeBlueprintResult(
            ok=False, run_id=prep.run_id, draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=str(prep.artifacts_dir),
            plan_path=str(prep.plan_path) if prep.plan_path.exists() else None,
            manifest_path=str(prep.manifest_path) if prep.manifest_path.exists() else None,
            elapsed=elapsed, python_version=_python_version(),
            error=_error(
                "generator-timeout",
                f"planner exceeded the {prep.timeout:g}s subprocess budget",
                outcome.stdout[-2000:] or None,
            ),
            stdout=outcome.stdout, stderr=outcome.stderr,
        )
    if outcome.returncode != 0:
        detail = (outcome.stderr or outcome.stdout or "").strip()[-4000:]
        return UpgradeBlueprintResult(
            ok=False, run_id=prep.run_id, draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=str(prep.artifacts_dir),
            plan_path=str(prep.plan_path) if prep.plan_path.exists() else None,
            manifest_path=str(prep.manifest_path) if prep.manifest_path.exists() else None,
            elapsed=elapsed, python_version=_python_version(),
            error=_error("generator-failed", f"planner exited with code {outcome.returncode}", detail),
            stdout=outcome.stdout, stderr=outcome.stderr,
        )
    if not prep.manifest_path.is_file():
        return UpgradeBlueprintResult(
            ok=False, run_id=prep.run_id, draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=str(prep.artifacts_dir),
            plan_path=None, manifest_path=None,
            elapsed=elapsed, python_version=_python_version(),
            error=_error("manifest-missing", f"Draft manifest not published at {prep.manifest_path}"),
            stdout=outcome.stdout, stderr=outcome.stderr,
        )

    try:
        manifest = _read_json(prep.manifest_path)
    except (OSError, ValueError) as exc:
        return UpgradeBlueprintResult(
            ok=False, run_id=prep.run_id, draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=str(prep.artifacts_dir),
            plan_path=str(prep.plan_path) if prep.plan_path.exists() else None,
            manifest_path=str(prep.manifest_path),
            elapsed=elapsed, python_version=_python_version(),
            error=_error("manifest-invalid", f"cannot read published Draft artifact result.json: {exc}"),
            stdout=outcome.stdout, stderr=outcome.stderr,
        )

    plan, error = _read_plan(prep.plan_path)
    if error is not None:
        return UpgradeBlueprintResult(
            ok=False, run_id=prep.run_id, draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=str(prep.artifacts_dir),
            plan_path=str(prep.plan_path) if prep.plan_path.exists() else None,
            manifest_path=str(prep.manifest_path),
            elapsed=elapsed, python_version=_python_version(),
            error=error, stdout=outcome.stdout, stderr=outcome.stderr,
        )

    contract_error = validate_planning_contract(manifest)
    requested_contract = normalize_decision_contract(prep.policy.intent)
    applied_contract = manifest.get("decisionContract")
    if contract_error is None and applied_contract:
        try:
            if normalize_decision_contract(applied_contract) != requested_contract:
                contract_error = _error("decision-contract-mismatch", "Core applied a different decision contract")
        except ValueError as exc:
            contract_error = _error("decision-contract-invalid", str(exc))
    elif contract_error is None and requires_contract_support(requested_contract):
        contract_error = _error("decision-contract-unsupported", "Core did not confirm the requested objective or mutable scope; upgrade Core before acting")
    if contract_error is not None:
        return UpgradeBlueprintResult(
            ok=False, run_id=prep.run_id, draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=str(prep.artifacts_dir),
            plan_path=str(prep.plan_path), manifest_path=str(prep.manifest_path),
            elapsed=elapsed, python_version=_python_version(),
            error=contract_error, stdout=outcome.stdout, stderr=outcome.stderr,
        )

    declared_hashes = (manifest.get("hashes") or {})
    for kind, path in (
        ("plan", prep.plan_path),
        ("prompt", prep.prompt_path),
    ):
        hash_error = _verify_published_hash(kind, declared_hashes.get(kind), path)
        if hash_error is not None:
            return UpgradeBlueprintResult(
                ok=False, run_id=prep.run_id, draft_status=None, blueprint=None,
                blueprint_path=None, artifacts_dir=str(prep.artifacts_dir),
                plan_path=str(prep.plan_path), manifest_path=str(prep.manifest_path),
                elapsed=elapsed, python_version=_python_version(),
                error=hash_error, stdout=outcome.stdout, stderr=outcome.stderr,
            )

    artifacts = {
        "manifest": str(prep.manifest_path),
        "plan": str(prep.plan_path),
        "prompt": (manifest.get("artifacts") or {}).get("prompt", str(prep.prompt_path)),
        "summary": (manifest.get("artifacts") or {}).get("summary", str(draft_dir / "summary.md")),
        "solverComponents": (manifest.get("artifacts") or {}).get("solverComponents", str(prep.solver_path)),
    }
    blueprint_path = prep.run_root / "upgrade-blueprint.json"
    blueprint = transcode_upgrade_blueprint(
        plan, manifest,
        project_path=str(prep.purl),
        project_name=prep.project_name,
        policy=prep.policy,
        artifacts=artifacts,
        blueprint_path=str(blueprint_path),
    )
    return _complete_blueprint(prep, manifest, artifacts, blueprint_path, blueprint, elapsed, outcome)


def _complete_blueprint(
    prep: _PreparedRun,
    manifest: Dict[str, Any],
    artifacts: Dict[str, str],
    blueprint_path: Path,
    blueprint: Dict[str, Any],
    elapsed: float,
    outcome: ProcessRun,
) -> UpgradeBlueprintResult:
    """Canonical solver-components source, schema validation and final write.

    The manifest's ``solverComponents`` list is the canonical source; when the
    manifest does not carry it, the planner's ``solver-components.json``
    ``{"components": [...]}`` envelope is used. This is planning evidence, never
    a proof of parallel-safe installation (the blueprint still marks
    parallelExecutionSafety=UNPROVEN).
    """
    components = list(manifest.get("solverComponents") or [])
    if not components and prep.solver_path.is_file():
        try:
            solver_envelope = _read_json(prep.solver_path)
        except (OSError, ValueError):
            solver_envelope = {}
        file_components = list(solver_envelope.get("components") or []) if isinstance(solver_envelope, dict) else []
        if file_components:
            components = file_components
    blueprint["solverComponents"] = components
    blueprint["workspaceCoverage"] = workspace_coverage(prep.purl)
    blueprint["runtimeProvenance"] = runtime_provenance(prep.generator)
    reported_options = manifest.get("planningOptions") or {}
    effective_cap = reported_options.get("draftMaxCandidates")
    if not isinstance(effective_cap, int) or isinstance(effective_cap, bool) or effective_cap < 1:
        effective_cap = prep.options.draft_max_candidates
    blueprint["planningOptions"] = {
        **reported_options, "draftMaxCandidates": effective_cap,
        "requestedDraftMaxCandidates": prep.options.draft_max_candidates,
        "capSource": "CORE_MANIFEST" if reported_options.get("draftMaxCandidates") is not None else "REQUEST",
        "deadlineSeconds": prep.options.draft_deadline_seconds,
    }
    enrich_decisions(blueprint, candidate_cap=effective_cap,
                     previous_evidence_digest=prep.options.previous_evidence_digest)

    schema_error = _validate_blueprint_schema(blueprint)
    if schema_error is not None:
        return UpgradeBlueprintResult(
            ok=False, run_id=prep.run_id, draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=str(prep.artifacts_dir),
            plan_path=str(prep.plan_path), manifest_path=str(prep.manifest_path),
            elapsed=elapsed, python_version=_python_version(),
            error=schema_error, stdout=outcome.stdout, stderr=outcome.stderr,
        )
    blueprint_path.write_text(_json_dumps(blueprint) + "\n", encoding="utf-8", newline="\n")

    return UpgradeBlueprintResult(
        ok=True,
        run_id=prep.run_id,
        draft_status=manifest.get("status"),
        blueprint=blueprint,
        blueprint_path=str(blueprint_path),
        artifacts_dir=str(prep.artifacts_dir),
        plan_path=str(prep.plan_path),
        manifest_path=str(prep.manifest_path),
        elapsed=elapsed,
        python_version=_python_version(),
        stdout=outcome.stdout,
        stderr=outcome.stderr,
    )


def generate_upgrade_blueprint(
    project_path: str,
    *,
    policy: Optional[BlueprintPolicy] = None,
    options: Optional[BlueprintOptions] = None,
    project_name: Optional[str] = None,
    run_id: Optional[str] = None,
) -> UpgradeBlueprintResult:
    """Run the planner in Draft mode against `project_path` and return an
    Upgrade Blueprint. The project directory is only ever read, never written.

    Returns a result object instead of raising for recoverable planner/registry
    conditions; a failed backing run yields `ok=False` with a machine-readable
    `error` dict and no fabricated blueprint.
    """
    opts = options if options is not None else BlueprintOptions()
    pol = policy if policy is not None else BlueprintPolicy()
    start = time.perf_counter()

    guard, prep = _prepare_generation(project_path, pol, opts, project_name, run_id)
    if guard is not None:
        return _result_from_prepare(guard, run_id or "", start)
    assert prep is not None

    outcome = run_process(
        [prep.interpreter] + prep.argv,
        cwd=str(prep.run_root),
        env=prep.env,
        timeout=prep.timeout,
    )
    return _finalize(prep, outcome, start)


async def generate_upgrade_blueprint_async(
    project_path: str,
    *,
    policy: Optional[BlueprintPolicy] = None,
    options: Optional[BlueprintOptions] = None,
    project_name: Optional[str] = None,
    run_id: Optional[str] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> UpgradeBlueprintResult:
    """Async counterpart of `generate_upgrade_blueprint`.

    Runs the planner through the async process runner so an MCP server can
    cooperate with cancellation: `cancel_event` (cooperative) and asyncio task
    cancellation (e.g. server shutdown) both kill the whole child process tree
    before returning/raising. All fail-closed validation is identical to the
    sync path.
    """
    opts = options if options is not None else BlueprintOptions()
    pol = policy if policy is not None else BlueprintPolicy()
    start = time.perf_counter()

    guard, prep = _prepare_generation(project_path, pol, opts, project_name, run_id)
    if guard is not None:
        return _result_from_prepare(guard, run_id or "", start)
    assert prep is not None

    outcome = await run_process_async(
        [prep.interpreter] + prep.argv,
        cwd=str(prep.run_root),
        env=prep.env,
        timeout=prep.timeout,
        cancel_event=cancel_event,
    )
    return _finalize(prep, outcome, start)


def _cli_summary(result: UpgradeBlueprintResult) -> str:
    if not result.ok:
        err = result.error or {}
        return (
            f"FAILED run={result.run_id} code={err.get('code')} "
            f"message={err.get('message')} elapsed={result.elapsed:.1f}s"
        )
    bp = result.blueprint or {}
    status = (bp.get("status") or {})
    summary = (bp.get("summary") or {})
    proj = (bp.get("source") or {}).get("project") or {}
    return (
        f"OK run={result.run_id} project={proj.get('name')} "
        f"draftStatus={status.get('draftStatus')} "
        f"verification={status.get('verificationStatus')}/{status.get('authority')} "
        f"total={summary.get('totalDependencies')} proposed={summary.get('proposed')} "
        f"needsReview={summary.get('needsReview')} elapsed={result.elapsed:.1f}s "
        f"python={result.python_version}"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Headless Upgrade Blueprint: run the DepLoom Draft planner as an "
        "isolated subprocess and project plan.json/result.json into a schemaVersion 2 "
        "Upgrade Blueprint. Never writes into the analysed project.",
    )
    parser.add_argument("--project", required=True, help="Path to the project directory (read-only).")
    parser.add_argument("--name", default="", help="Optional project name; default = directory name.")
    parser.add_argument("--run-id", default="", help="Optional stable run id; default = generated.")
    parser.add_argument("--target-level", choices=("yellow", "green"), default="yellow")
    parser.add_argument("--min-lag-ok-pct", type=int, default=80)
    parser.add_argument("--intent", default="", help="Path to JSON file with DEPLOOM_BASELINE_INTENT_JSON (policy).")
    parser.add_argument("--acceptance-policy", default="", help="Path to JSON file with DEPLOOM_ACCEPTANCE_POLICY_JSON.")
    parser.add_argument("--registry", default="", help="npm registry URL.")
    parser.add_argument(
        "--exclude", action="append", default=[],
        help="Package name excluded from planning scope (repeatable; passed to the "
        "planner as --exclude-dependency before planning, never filtered after).",
    )
    parser.add_argument("--include-prerelease", action="store_true")
    parser.add_argument("--deadline-seconds", type=float, default=60.0, help="Draft hard deadline (default 60).")
    parser.add_argument("--max-candidates", type=int, default=3, help="Draft candidate cap (default 3).")
    parser.add_argument("--registry-metadata-cache", default="", help="Persistent planning cache dir (warm path).")
    parser.add_argument("--registry-metadata-ttl", type=float, default=3600.0, help="Cache freshness bound.")
    parser.add_argument("--output-dir", default="", help="Where to keep artifacts + blueprint JSON (default: temp).")
    parser.add_argument("--json-out", default="", help="Blueprint JSON output path (default: <output-dir>/upgrade-blueprint.json).")
    parser.add_argument("--python", default="", help="Python interpreter for the planner subprocess (default: sys.executable).")
    parser.add_argument("--core-dir", default="", help="Pinned planning core directory (default: <repo>/upstream/deploom).")
    parser.add_argument("--timeout", type=float, default=0.0, help="Subprocess timeout seconds (default: auto).")
    parser.add_argument("--workspace-id", default="", help="Stable workspace id for the manifest.")
    args = parser.parse_args(argv)

    policy = BlueprintPolicy(
        target_level=args.target_level,
        min_lag_ok_pct=args.min_lag_ok_pct,
        intent=_json_file(args.intent, "intent") if args.intent else None,
        acceptance_policy=_json_file(args.acceptance_policy, "acceptance-policy") if args.acceptance_policy else None,
        exclusions=tuple(dict.fromkeys(args.exclude or [])),
    )
    out_dir = args.output_dir
    if not out_dir and args.json_out:
        out_dir = str(Path(args.json_out).resolve().parent)
    options = BlueprintOptions(
        registry=args.registry or None,
        include_prerelease=args.include_prerelease,
        draft_deadline_seconds=args.deadline_seconds,
        draft_max_candidates=args.max_candidates,
        registry_metadata_cache=args.registry_metadata_cache or None,
        registry_metadata_ttl=args.registry_metadata_ttl,
        output_dir=out_dir or None,
        python=args.python or None,
        core_dir=args.core_dir or None,
        run_timeout_seconds=args.timeout or None,
        workspace_id=args.workspace_id or None,
    )
    result = generate_upgrade_blueprint(
        args.project,
        policy=policy,
        options=options,
        project_name=args.name or None,
        run_id=args.run_id or None,
    )
    print(_cli_summary(result))
    if result.ok and args.json_out:
        bp_path = Path(args.json_out).resolve()
        bp_path.parent.mkdir(parents=True, exist_ok=True)
        bp_path.write_text(_json_dumps(result.blueprint) + "\n", encoding="utf-8", newline="\n")
        print(f"blueprint: {bp_path}")
    return 0 if result.ok else 2


def _json_file(path: str, label: str) -> Dict[str, Any]:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise SystemExit(f"--{label}: file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"--{label}: invalid JSON in {p}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"--{label}: expected a JSON object in {p}")
    return data


if __name__ == "__main__":
    raise SystemExit(main())
