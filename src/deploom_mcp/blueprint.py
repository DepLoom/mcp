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
* Determinism: same project state + same options reproduce the same plan
  content; the plan and prompt SHA-256 hashes published by the planner are
  carried into the blueprint as `hashes`.
* Isolation: nothing is imported from the generator; the planner runs as a
  child OS process, so this module cannot mutate planner global state and
  planner failures degrade into machine-readable errors, not exceptions.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

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


def _default_core_dir() -> Path:
    """Standalone repository layout: the pinned planning core lives at
    ``<repo root>/upstream/deploom``, while this module lives at
    ``<repo root>/src/deploom_mcp/blueprint.py``."""
    return Path(__file__).resolve().parents[2] / "upstream" / "deploom"


@dataclass(frozen=True)
class BlueprintPolicy:
    """The policy the planner was asked to satisfy (yellow/green closure)."""

    target_level: str = "yellow"
    min_lag_ok_pct: int = 80
    intent: Optional[Dict[str, Any]] = None
    acceptance_policy: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class BlueprintOptions:
    """Tuning knobs for the headless Draft run that backs a blueprint."""

    registry: Optional[str] = None
    include_prerelease: bool = False
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
        "postPlanLagOk", "postPlanLagOkPct", "postPlanScopeTotal",
        "postPlanTargetLevel", "postPlanPolicyRequired", "postPlanReserveRequired",
        "postPlanPolicyShortfall", "postPlanReserveShortfall", "postPlanShortfall",
        "postPlanCritical", "postPlanHigh", "postPlanModerate", "postPlanLow",
        "postPlanSecurityKnown", "postPlanSecurityUnknown", "postPlanSecurityTotal",
        "postPlanGoal",
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
            "postPlanTargetLevel": health.get("postPlanTargetLevel"),
            "postPlanLagOkPct": health.get("postPlanLagOkPct"),
            "postPlanGoal": health.get("postPlanGoal"),
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

    return {
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
            "project": {"name": project_name, "path": str(Path(project_path).resolve())},
            "inputFiles": [str(Path(p).resolve()) for p in source_inputs],
            "inputHashes": manifest.get("inputHashes") or {},
        },
        "policy": {
            "targetLevel": policy.target_level,
            "minLagOkPct": policy.min_lag_ok_pct,
            "policyHash": manifest.get("policyHash"),
            "intent": policy.intent,
            "acceptancePolicy": policy.acceptance_policy,
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
    if options.include_prerelease:
        argv += ["--include-prerelease"]
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

    repo_root = Path(__file__).resolve().parent
    if opts.generator_path:
        generator = Path(opts.generator_path).resolve()
    elif opts.core_dir:
        generator = Path(opts.core_dir).resolve() / GENERATOR_BASENAME
    else:
        generator = _default_core_dir() / GENERATOR_BASENAME
    if not generator.is_file():
        return UpgradeBlueprintResult(
            ok=False, run_id=run_id or "", draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=None, plan_path=None, manifest_path=None,
            elapsed=time.perf_counter() - start, python_version=_python_version(),
            error=_error(
                "core-not-found",
                f"pinned planning core not found at {generator}; "
                "ensure the submodule is initialized (git submodule update --init --recursive)",
            ),
        )
    interpreter = str(Path(opts.python).resolve()) if opts.python else sys.executable

    purl = Path(project_path).expanduser().resolve().absolute()
    if not purl.exists() or not purl.is_dir():
        return UpgradeBlueprintResult(
            ok=False, run_id=run_id or "", draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=None, plan_path=None, manifest_path=None,
            elapsed=0.0, python_version=_python_version(),
            error=_error("project-not-found", f"project directory does not exist: {purl}"),
        )
    if not (purl / "package.json").is_file():
        return UpgradeBlueprintResult(
            ok=False, run_id=run_id or "", draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=None, plan_path=None, manifest_path=None,
            elapsed=0.0, python_version=_python_version(),
            error=_error("invalid-project", f"no package.json found in {purl}"),
        )

    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    project_name = project_name or purl.name
    workspace_id = opts.workspace_id or f"ws-{project_name}-{run_id[4:]}"
    project_id = project_name

    if opts.output_dir:
        out_root = Path(opts.output_dir).expanduser().resolve()
    else:
        out_root = Path(tempfile.gettempdir()) / f"deploom-blueprint-{run_id}"
    out_root.mkdir(parents=True, exist_ok=True)
    control = out_root / "exec"
    control.mkdir(parents=True, exist_ok=True)
    artifacts_dir = out_root / "artifacts"
    state_dir = out_root / "state"
    history_dir = out_root / "history"

    projects_file = control / "projects.txt"
    projects_file.write_text(str(purl) + "\n", encoding="utf-8", newline="\n")

    argv = _prepare_argv_full(
        generator, projects_file, artifacts_dir, state_dir, history_dir,
        run_id, workspace_id, project_id, pol, opts,
    )
    env = _exec_env(pol, run_id, workspace_id, project_id, opts)

    timeout = opts.run_timeout_seconds
    if timeout is None:
        timeout = max(opts.draft_deadline_seconds * 2 + 30, 120.0)

    try:
        process = subprocess.run(
            [interpreter] + argv,
            cwd=str(control),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return UpgradeBlueprintResult(
            ok=False, run_id=run_id, draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=str(artifacts_dir),
            plan_path=None, manifest_path=None,
            elapsed=time.perf_counter() - start, python_version=_python_version(),
            error=_error(
                "generator-timeout",
                f"planner exceeded the {timeout:g}s subprocess budget",
                (exc.stdout or "")[-2000:] if exc.stdout else None,
            ),
        )
    elapsed = time.perf_counter() - start

    draft_dir = artifacts_dir / "runs" / run_id / "draft"
    manifest_path = draft_dir / "result.json"
    plan_path = draft_dir / "plan.json"

    if process.returncode != 0:
        detail = (process.stderr or process.stdout or "").strip()[-4000:]
        return UpgradeBlueprintResult(
            ok=False, run_id=run_id, draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=str(artifacts_dir),
            plan_path=str(plan_path) if plan_path.exists() else None,
            manifest_path=str(manifest_path) if manifest_path.exists() else None,
            elapsed=elapsed, python_version=_python_version(),
            error=_error("generator-failed", f"planner exited with code {process.returncode}", detail),
            stdout=process.stdout, stderr=process.stderr,
        )
    if not manifest_path.is_file():
        return UpgradeBlueprintResult(
            ok=False, run_id=run_id, draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=str(artifacts_dir),
            plan_path=None, manifest_path=None,
            elapsed=elapsed, python_version=_python_version(),
            error=_error("manifest-missing", f"Draft manifest not published at {manifest_path}"),
            stdout=process.stdout, stderr=process.stderr,
        )

    try:
        manifest = _read_json(manifest_path)
        plan = _read_json(plan_path) if plan_path.is_file() else {"proposals": [], "counts": {}, "unknowns": [], "conflicts": [], "unresolved": []}
        solver_path = draft_dir / "solver-components.json"
        solver_components = _read_json(solver_path) if solver_path.is_file() else {}
    except (OSError, ValueError) as exc:
        return UpgradeBlueprintResult(
            ok=False, run_id=run_id, draft_status=None, blueprint=None,
            blueprint_path=None, artifacts_dir=str(artifacts_dir),
            plan_path=str(plan_path) if plan_path.exists() else None,
            manifest_path=str(manifest_path),
            elapsed=elapsed, python_version=_python_version(),
            error=_error("manifest-invalid", f"cannot read published Draft artifacts: {exc}"),
            stdout=process.stdout, stderr=process.stderr,
        )

    artifacts = {
        "manifest": str(manifest_path),
        "plan": str(plan_path),
        "prompt": (manifest.get("artifacts") or {}).get("prompt", str(draft_dir / "prompt.md")),
        "summary": (manifest.get("artifacts") or {}).get("summary", str(draft_dir / "summary.md")),
        "solverComponents": (manifest.get("artifacts") or {}).get("solverComponents", str(draft_dir / "solver-components.json")),
    }
    blueprint_path = out_root / "upgrade-blueprint.json"
    blueprint = transcode_upgrade_blueprint(
        plan, manifest,
        project_path=str(purl),
        project_name=project_name,
        policy=pol,
        artifacts=artifacts,
        blueprint_path=str(blueprint_path),
    )
    if isinstance(solver_components, dict) and solver_components:
        blueprint.setdefault("solverComponents", [])
    blueprint_path.write_text(_json_dumps(blueprint) + "\n", encoding="utf-8", newline="\n")

    return UpgradeBlueprintResult(
        ok=True,
        run_id=run_id,
        draft_status=manifest.get("status"),
        blueprint=blueprint,
        blueprint_path=str(blueprint_path),
        artifacts_dir=str(artifacts_dir),
        plan_path=str(plan_path),
        manifest_path=str(manifest_path),
        elapsed=elapsed,
        python_version=_python_version(),
        stdout=process.stdout,
        stderr=process.stderr,
    )


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
