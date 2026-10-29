"""DepLoom MCP server: planning plus read-only decision evidence follow-ups.

Stage 2 boundary, built on the official MCP SDK (`MCPServer`). It does NOT
reimplement or extend planning: it wraps the Stage 1/Stage B edge boundary
(`generate_upgrade_blueprint` / `generate_upgrade_blueprint_async` in
`deploom_mcp.blueprint`) and exposes it to MCP clients as a single
`get_upgrade_plan` tool per the Stage 1 document section H recommendations.

Hardening contract of this boundary:

* fail-closed inputs: every knob is validated before anything runs
  (NaN/Inf deadlines, policy ranges, registry URL form and no embedded
  credentials); misuse returns a machine-readable error, never a crash;
* cancellable planning: the tool is async and runs the planner through the
  async process runner, so asyncio/anyio task cancellation (including server
  shutdown and client disconnect) kills the whole child process tree;
* honestly bounded response: the compact protocol caps inlined dependency
  rows, groups and per-group package lists; the whole serialized payload --
  including the ``truncation`` block -- must fit the byte budget
  (``truncation.bytes`` is the exact final size, 512 KiB default). A larger
  plan stays fully on disk in the artifact files and the response says so
  explicitly, it never silently drops data; if even the minimal payload cannot
  fit, the tool fails closed with ``response-too-large``;
* applied policy is returned: the compact response carries the `policy` that
  was actually applied (targetLevel, minLagOkPct, exclusions, policyHash) from
  the produced blueprint, and a request-vs-applied mismatch is surfaced as
  ``policyDiscrepancy`` instead of being hidden;
* exclusions are real planning scope: `exclude` is passed to the planner as
  `--exclude-dependency` BEFORE planning (never filtered out afterwards);
* honest statuses: every response carries `authority=PLANNING_ONLY`, a
  `PROPOSED` verdict means "can be proposed", not "already updated", and
  `needsReview` items require inspecting evidence within existing authority;
* budget: default deadline 20 s (range 5..600), planning metadata cached in a
  managed directory shared between calls (`DEPLOOM_MCP_STATE_DIR`);
* artifacts and output always live outside the analysed project
  (`output-inside-project` guard, enforced again at the write boundary).
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult, TextContent, ToolAnnotations

from deploom_mcp import __version__
from deploom_mcp.blueprint import (
    BlueprintError,
    BlueprintOptions,
    BlueprintPolicy,
    generate_upgrade_blueprint,
    generate_upgrade_blueprint_async,
    resolve_output_outside,
)
from deploom_mcp.decisions import (
    MAX_DRAFT_CANDIDATES, OBJECTIVES, decision_evidence, normalize_decision_contract,
)
from deploom_mcp.target_assessment import target_assessment, target_decision_evidence
from deploom_mcp.proxy_settings import ProxySettingError, resolve_system_proxy
from deploom_mcp.decision_followups import evaluate_assignment, explain_conflict
from deploom_mcp.registry_projection import (
    compact_registry_candidate, project_no_target_code, project_registry_aggregate,
)

SERVER_NAME = "deploom-mcp"
TOOL_NAME = "get_upgrade_plan"
KIND = "upgrade-blueprint"

# Section H budget: default deadline 15..30 s. We default to 20 s and clamp to
# a safe hard range so a client cannot hang the server on an absurd budget.
DEFAULT_DEADLINE_SECONDS = 20.0
MIN_DEADLINE_SECONDS = 5.0
MAX_DEADLINE_SECONDS = 600.0

# Compact protocol: cap the number of inlined dependency rows. The complete
# plan is always on disk (artifacts), so this bounds context, never data.
DEFAULT_INLINE_ROWS = 100

# P2 (deterministic response size): display-priority groups and the packages
# inside each group are also bounded, and the whole serialized response must
# fit a byte budget before it is returned. All reductions are pure truncation
# of display lists. Aggregate target health, full counts and artifact paths stay
# visible; clipped project health is explicit in `truncation.limitsHit`.
DEFAULT_INLINE_GROUPS = 50
DEFAULT_PACKAGES_PER_GROUP = 50
DEFAULT_INLINE_EVIDENCE = 50
MAX_DETAIL_CHARS = 512
MAX_RESPONSE_BYTES = 512 * 1024  # 512 KiB deterministic cap on the JSON response

HONESTY_NOTE = (
    "Planning-only: authority=PLANNING_ONLY (nothing was installed or verified). "
    "A PROPOSED verdict means 'this upgrade can be proposed', NOT 'already updated'. "
    "Review needsReview evidence within the authority already granted by the user; "
    "request additional approval only when the intended action exceeds it."
)
PLANNING_BOUNDARY_NOTE = (
    "NOT_VERIFIED / PLANNING_ONLY / UNKNOWN / UNPROVEN: statuses are planning "
    "verdicts from the Draft planner; CONSTRAINT_SATISFIED / MATERIALIZED / "
    "PROJECT_VERIFIED are reserved for the verified pipeline and never produced here."
)

server = MCPServer(name=SERVER_NAME, version=__version__)


def _state_dir() -> Path:
    env = os.environ.get("DEPLOOM_MCP_STATE_DIR")
    if env:
        # Lexical absolute path: symlink/junction components are preserved on
        # purpose so the filesystem guard can reject a state route that passes
        # THROUGH the analysed project (see resolve_output_outside).
        return Path(os.path.abspath(os.path.expanduser(env)))
    return Path.home() / ".deploom-mcp"


def _ensure_state_dir() -> Path:
    state = _state_dir()
    try:
        state.mkdir(parents=True, exist_ok=True)
        return state
    except OSError:  # pragma: no cover - permission-dependent fallback
        return Path(tempfile.gettempdir()) / "deploom-mcp"


def _error(code: str, message: str, detail: Optional[Any] = None) -> Dict[str, Any]:
    return {"code": code, "message": message, "detail": detail}


def _build_request(
    *,
    project_path: str,
    target_level: str,
    min_lag_ok_pct: int,
    deadline_seconds: float,
    include_prerelease: bool,
    registry: Optional[str],
    registry_metadata_ttl: float,
    output_dir: Optional[str],
    exclude: Optional[List[str]],
    target_mode: str = "pinned",
    objective: str = OBJECTIVES[0],
    mutable_packages: Optional[List[str]] = None,
    approved_version_lines: Optional[Dict[str, List[int]]] = None,
    draft_max_candidates: int = 3,
    previous_evidence_digest: Optional[str] = None,
    use_system_proxy: Optional[bool] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Validate every input (fail-closed) and build the planner request."""
    try:
        effective_proxy, proxy_source = resolve_system_proxy(use_system_proxy)
    except ProxySettingError as exc:
        return _error("invalid-system-proxy", str(exc)), None
    if not project_path or not isinstance(project_path, str):
        return _error("invalid-args", "project_path must be a non-empty string"), None
    purl = Path(project_path).expanduser().resolve()
    try:
        contract = normalize_decision_contract({
            "targetMode": target_mode, "objective": objective,
            "mutablePackages": mutable_packages, "approvedVersionLines": approved_version_lines,
        })
    except ValueError as exc:
        return _error("invalid-intent", str(exc)), None
    if isinstance(draft_max_candidates, bool) or not isinstance(draft_max_candidates, int) or not (1 <= draft_max_candidates <= MAX_DRAFT_CANDIDATES):
        return _error("invalid-candidate-cap", f"draft_max_candidates must be an integer in [1, {MAX_DRAFT_CANDIDATES}]"), None
    if previous_evidence_digest is not None and (not isinstance(previous_evidence_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", previous_evidence_digest)):
        return _error("invalid-evidence-digest", "previous_evidence_digest must be a lowercase SHA-256 digest"), None

    if target_level not in ("yellow", "green"):
        return _error("invalid-goal", f"target_level must be 'yellow' or 'green', got {target_level!r}"), None

    if not isinstance(include_prerelease, bool):
        return _error("invalid-args", "include_prerelease must be a boolean"), None

    if isinstance(min_lag_ok_pct, bool) or not isinstance(min_lag_ok_pct, int) or not (0 <= min_lag_ok_pct <= 100):
        return _error("invalid-policy", "min_lag_ok_pct must be an integer in [0, 100]"), None

    try:
        deadline = float(deadline_seconds)
    except (TypeError, ValueError):
        return _error("invalid-deadline", "deadline_seconds must be a number"), None
    if not math.isfinite(deadline) or deadline <= 0:
        return _error("invalid-deadline", "deadline_seconds must be a finite positive number"), None
    deadline = max(MIN_DEADLINE_SECONDS, min(MAX_DEADLINE_SECONDS, deadline))

    if registry is not None:
        if not isinstance(registry, str) or not registry.strip():
            return _error("invalid-registry", "registry must be a non-empty URL string"), None
        parsed = urlparse(registry.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return _error("invalid-registry", "registry must be an http(s) URL"), None
        if parsed.username or parsed.password:
            return _error("invalid-registry", "registry URL must not carry embedded credentials"), None

    try:
        ttl = float(registry_metadata_ttl)
    except (TypeError, ValueError):
        return _error("invalid-registry", "registry_metadata_ttl must be a number"), None
    if not math.isfinite(ttl) or ttl < 0:
        return _error("invalid-registry", "registry_metadata_ttl must be a finite non-negative number"), None

    exclusions: List[str] = []
    if exclude is not None:
        if not isinstance(exclude, list):
            return _error("invalid-exclude", "exclude must be an array of package name strings"), None
        for item in exclude:
            if not isinstance(item, str) or not item.strip():
                return _error("invalid-exclude", "exclude entries must be non-empty package name strings"), None
            name = item.strip()
            if name not in exclusions:
                exclusions.append(name)

    # Resolve (but do NOT create) the managed state location. Every write the
    # boundary performs -- run artifacts, registry metadata cache, exclusive
    # run dirs -- lives under here or under the explicit output_dir, so the
    # state root must not overlap the analysed project (which is read-only).
    state = _state_dir()
    if resolve_output_outside(purl, state) is None:
        return (
            _error(
                "state-inside-project",
                f"managed state dir {state} must not overlap the analysed project {purl}",
            ),
            None,
        )

    out: Optional[str] = None
    if output_dir:
        candidate = Path(os.path.abspath(os.path.expanduser(output_dir)))
        if resolve_output_outside(purl, candidate) is None:
            return (
                _error(
                    "output-inside-project",
                    f"output_dir {candidate} must not be inside the analysed project {purl}",
                ),
                None,
            )
        out = str(candidate)

    # All overlap validation passed: only now create managed directories (and
    # resolve the permission fallback), so a rejected request writes nothing.
    state = _ensure_state_dir()

    if out is None:
        out = str(state / "artifacts" / f"run-{os.urandom(6).hex()}")

    cache_dir = state / "registry-metadata-cache"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover - fall back to an uncached cold run
        cache_dir = None

    options = BlueprintOptions(
        registry=registry,
        include_prerelease=include_prerelease,
        use_system_proxy=effective_proxy,
        draft_deadline_seconds=deadline,
        draft_max_candidates=draft_max_candidates,
        previous_evidence_digest=previous_evidence_digest,
        registry_metadata_cache=str(cache_dir) if cache_dir else None,
        registry_metadata_ttl=ttl,
        output_dir=out,
        # Test/ops escape hatches: point the planner subprocess at a different
        # generator (or interpreter) without changing the tool's public
        # signature. Never populated by tool arguments, only by environment.
        generator_path=os.environ.get("DEPLOOM_MCP_GENERATOR_PATH") or None,
        python=os.environ.get("DEPLOOM_MCP_PYTHON") or None,
    )
    policy = BlueprintPolicy(
        target_level=target_level,
        min_lag_ok_pct=min_lag_ok_pct,
        exclusions=tuple(exclusions),
        intent=contract,
    )
    return None, {"purl": purl, "options": options, "policy": policy,
                  "systemProxySource": proxy_source}


def _policy_snapshot(requested: BlueprintPolicy) -> Dict[str, Any]:
    """The agent-facing policy knobs exactly as the caller requested them."""
    return {
        "targetLevel": requested.target_level,
        "minLagOkPct": requested.min_lag_ok_pct,
        "exclusions": list(requested.exclusions),
        "decisionContract": normalize_decision_contract(requested.intent),
    }


def _policy_discrepancy(
    requested: Dict[str, Any], applied: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Detect a mismatch between the REQUESTED policy and the policy the
    blueprint actually carries, instead of silently hiding it.

    Only the fields both sides define are compared; ``policyHash`` is
    planner-applied evidence and therefore excluded from the conflict check.
    """
    mismatched: List[str] = []
    for field in ("targetLevel", "minLagOkPct"):
        req = requested.get(field)
        applied_value = applied.get(field)
        if req is not None and applied_value is not None and req != applied_value:
            mismatched.append(field)
    if set(requested.get("exclusions") or []) != set(applied.get("exclusions") or []):
        mismatched.append("exclusions")
    if applied.get("decisionContract") is not None and requested.get("decisionContract") != applied.get("decisionContract"):
        mismatched.append("decisionContract")
    if not mismatched:
        return None
    return {
        "requested": dict(requested),
        "applied": {k: applied.get(k) for k in ("targetLevel", "minLagOkPct", "exclusions", "policyHash", "decisionContract")},
        "mismatchedFields": sorted(mismatched),
        "message": "the plan was produced under a policy that differs from the requested one "
        "(see policy.applied); do not act on rows expecting the requested policy",
    }


def _result_from_planning(result, requested: Optional[BlueprintPolicy] = None) -> Dict[str, Any]:
    if not result.ok:
        err = result.error or _error("internal-error", "planner failed without a machine-readable error")
        return {"ok": False, "runId": result.run_id, "error": err}
    try:
        payload = compact_blueprint(result.blueprint or {})
    except BlueprintError as exc:
        return {
            "ok": False,
            "runId": result.run_id,
            "error": {"code": exc.code, "message": str(exc), "detail": exc.detail},
        }
    response = {"ok": True, **payload}
    if requested is not None:
        applied = payload.get("policy") or {}
        discrepancy = _policy_discrepancy(_policy_snapshot(requested), applied)
        if discrepancy is not None:
            response["policyDiscrepancy"] = discrepancy
    if _finalize_byte_count(response) > MAX_RESPONSE_BYTES:
        return {"ok": False, "runId": result.run_id,
                "error": _error("response-too-large", "response including policy discrepancy exceeds the byte limit")}
    return response


def _compact_payload(
    blueprint: Dict[str, Any],
    *,
    max_inline_rows: int,
    max_inline_groups: int,
    max_packages_per_group: int,
    max_inline_evidence: int = DEFAULT_INLINE_EVIDENCE,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Build the compact payload for the given display caps.

    Returns ``(payload_without_truncation, metrics)`` where ``metrics`` carries
    the counts the caller needs to describe truncation honestly.
    """
    status = blueprint.get("status") or {}
    summary = blueprint.get("summary") or {}
    source = blueprint.get("source") or {}
    project = source.get("project") or {}
    run = blueprint.get("run") or {}

    all_groups = blueprint.get("recommendationGroups") or []
    total_groups = len(all_groups)
    total_packages = sum(len(list(g.get("packages") or [])) for g in all_groups)

    groups = []
    for group in all_groups[:max_inline_groups]:
        groups.append({
            "group": group.get("group"),
            "count": group.get("count"),
            "needsReview": bool(group.get("needsReview")),
            "packages": list((group.get("packages") or [])[:max_packages_per_group]),
        })
    inline_packages = sum(len(g["packages"]) for g in groups)

    # Re-read final-target facts even for a legacy/pre-enriched blueprint. A
    # stored empty reason list or stale completeness flag cannot hide them.
    assessment = target_assessment(blueprint)
    target_evidence = target_decision_evidence(assessment)
    all_evidence = blueprint.get("decisionEvidence")
    if all_evidence is None:
        all_evidence = decision_evidence(blueprint)
    all_evidence = [item for item in all_evidence
                    if not str(item.get("code") or "").startswith("TARGET_")]
    all_evidence.extend(target_evidence)
    all_rows = []
    for original in blueprint.get("dependencies") or []:
        row = dict(original)
        affected = [item for item in target_evidence
                    if item.get("scope") == "aggregate" or item.get("project") == row.get("project")]
        if affected and row.get("status") != "EXCLUDED":
            if not row.get("needsReview"):
                row["nextAction"] = affected[0]["nextAction"]
            row["needsReview"] = True
        all_rows.append(row)
    for group in groups:
        names = set(group["packages"])
        group["needsReview"] = bool(group["needsReview"] or any(
            row.get("needsReview") for row in all_rows if row.get("package") in names))
    critical_evidence = sorted(
        [e for e in all_evidence if e.get("severity") != "INFO"],
        key=lambda e: (0 if e.get("severity") == "BLOCKER" else 1,
                       str(e.get("package") or ""), str(e.get("evidenceRef") or ""), str(e.get("code") or "")),
    )
    details_clipped = bool((blueprint.get("registryDiagnostics") or {}).get("projectionTruncated"))

    def detail(value):
        nonlocal details_clipped
        if isinstance(value, str) and len(value) > MAX_DETAIL_CHARS:
            details_clipped = True
            return value[:MAX_DETAIL_CHARS] + "… [read evidenceRef]"
        return value

    visible_evidence = [{k: detail(item.get(k)) for k in ("code", "severity", "scope", "project", "package", "message", "nextAction", "evidenceRef", "lifecycle")}
                        for item in critical_evidence[:max_inline_evidence]]
    target_projects = assessment["projects"]
    visible_projects = dict(list(target_projects.items())[:max_inline_evidence])
    target_clipped = len(visible_projects) != len(target_projects)
    compact_assessment = {
        **assessment,
        "aggregate": {key: detail(value) for key, value in assessment["aggregate"].items()},
        "projects": {name: {key: detail(value) for key, value in health.items()}
                     for name, health in visible_projects.items()},
        "totalProjects": len(target_projects),
        "inlineProjects": len(visible_projects),
        "evidenceRef": "#/targetAssessment",
    }
    total_rows = len(all_rows)
    rows = all_rows[:max_inline_rows]
    dependencies = []
    for row in rows:
        dependencies.append({
            "package": row.get("package"),
            "declarationId": row.get("declarationId"),
            "status": row.get("status"),
            "current": row.get("current"),
            "target": row.get("target"),
            "group": row.get("group"),
            "needsReview": bool(row.get("needsReview")),
            "reason": detail(row.get("reason")),
            "evidence": row.get("evidence") or {},
            "nextAction": row.get("nextAction") or "READ_PLANNER_EVIDENCE",
            "evidenceRef": f"#/dependencies/{len(dependencies)}",
        })
        code = project_no_target_code(
            row.get("noTargetDiagnosticCode"), str(row.get("status") or "").lower().replace("_", "-"),
            row.get("registryCandidateDiagnostics"),
        )
        if code is not None:
            dependencies[-1]["noTargetDiagnosticCode"] = code
        diagnostic = compact_registry_candidate(
            row.get("registryCandidateDiagnostics"),
            f"#/dependencies/{len(dependencies) - 1}/registryCandidateDiagnostics",
        )
        if diagnostic is not None:
            dependencies[-1]["registryCandidateDiagnostics"] = diagnostic
            details_clipped = details_clipped or bool(diagnostic.get("projectionTruncated"))

    draft_status = status.get("draftStatus")
    is_partial = draft_status == "DRAFT_PARTIAL"
    processed = summary.get("processed")
    processed_total = summary.get("processedTotal")
    policy = blueprint.get("policy") or {}
    coverage = dict(blueprint.get("workspaceCoverage") or {
        "workspaceGraphCoverage": "UNKNOWN", "reconciliationRequired": True,
        "jointAssignment": "NOT_COMPUTED", "discoveryComplete": False,
        "nextAction": "INSPECT_WORKSPACE_COVERAGE",
    })
    coverage_clipped = False
    for field in ("discoveredManifests", "evaluatedManifests", "unevaluatedManifests"):
        items = coverage.get(field)
        if isinstance(items, list):
            coverage[field + "Count"] = len(items)
            coverage[field] = [detail(x) for x in items[:max_inline_evidence]]
            coverage_clipped = coverage_clipped or len(items) > max_inline_evidence
    blocker_count = sum(x.get("severity") == "BLOCKER" for x in critical_evidence)
    unresolved = any(x.get("code") in {"CANDIDATE_CAP", "METADATA_UNKNOWN", "SECURITY_UNKNOWN", "PEER_UNRESOLVED", "UNRESOLVED_TARGET"}
                     or x.get("lifecycle") == "UNRESOLVED_LEGACY" for x in critical_evidence)
    coverage_known = all(isinstance(summary.get(k), int) and not isinstance(summary.get(k), bool)
                         for k in ("metadataKnown", "metadataTotal", "securityKnown", "securityTotal"))
    evidence_complete = (coverage_known and summary["metadataKnown"] == summary["metadataTotal"]
                         and summary["securityKnown"] == summary["securityTotal"]
                         and not summary.get("insufficientData") and not is_partial and not unresolved
                         and assessment["evidenceComplete"])
    projection_complete = (len(rows) == total_rows and len(visible_evidence) == len(critical_evidence)
                           and not details_clipped and not coverage_clipped and not target_clipped)
    next_action = ("READ_FULL_DECISION_EVIDENCE" if not projection_complete else
                   "RECONCILE_WORKSPACE_PLANS" if coverage.get("reconciliationRequired") else
                   "RESOLVE_PARTIAL_PLAN" if is_partial else
                   "RECONCILE_CONSTRAINTS" if blocker_count else
                   "FETCH_TARGET_SECURITY_EVIDENCE" if any(e["code"] == "TARGET_SECURITY_UNKNOWN" for e in target_evidence) else
                   "INSPECT_TARGET_ASSESSMENT" if target_evidence else
                   "REVIEW_WITHIN_EXISTING_AUTHORITY" if critical_evidence else "VERIFY_PROPOSED_ASSIGNMENT")
    payload = {
        "ok": True,
        "protocol": KIND,
        "schemaVersion": blueprint.get("schemaVersion"),
        "run": {
            "runId": run.get("runId"),
            "projectId": run.get("projectId"),
            "project": project.get("name"),
        },
        "status": {
            "draftStatus": draft_status,
            "verificationStatus": status.get("verificationStatus", "NOT_VERIFIED"),
            "authority": status.get("authority", "PLANNING_ONLY"),
            "compatibility": status.get("compatibility", "UNKNOWN"),
            "parallelExecutionSafety": status.get("parallelExecutionSafety", "UNPROVEN"),
            "partialReason": status.get("partialReason"),
        },
        "partial": {
            "partial": is_partial,
            "partialReason": status.get("partialReason"),
            "processed": processed,
            "processedTotal": processed_total,
            "replanRequired": is_partial,
        },
        "policy": {
            # Literally the policy that was APPLIED, taken from the produced
            # blueprint -- never re-invented from the request arguments. Only
            # the agent-facing knobs are exposed; intent / acceptance policy /
            # registry internals stay out of the compact response.
            "targetLevel": policy.get("targetLevel"),
            "minLagOkPct": policy.get("minLagOkPct"),
            "exclusions": list(policy.get("exclusions") or []),
            "policyHash": policy.get("policyHash"),
            **{k: policy[k] for k in ("decisionContract", "coreContractSupport") if k in policy},
        },
        "summary": {
            "totalDependencies": summary.get("totalDependencies"),
            "proposed": summary.get("proposed"),
            "needsReview": sum(bool(row.get("needsReview")) for row in all_rows),
            "statusCounts": summary.get("statusCounts") or {},
        },
        "recommendationGroups": groups,
        "dependencies": dependencies,
        # Tiny typed aggregate, independently bounded even for very large
        # plans. Never expose registry URL identities in compact responses.
        **({"registryDiagnostics": project_registry_aggregate(blueprint["registryDiagnostics"])}
           if project_registry_aggregate(blueprint.get("registryDiagnostics")) is not None else {}),
        "decisionEvidence": visible_evidence,
        "targetAssessment": compact_assessment,
        "decisionCompleteness": {
            "projectionComplete": projection_complete,
            "requiresArtifactRead": not projection_complete,
            "planningComplete": not is_partial and draft_status == "DRAFT_READY",
            "evidenceComplete": evidence_complete,
            "criticalEvidenceCount": len(critical_evidence),
            "inlineCriticalEvidenceCount": len(visible_evidence),
            "blockerCount": blocker_count,
            "workspaceReconciliationRequired": bool(coverage.get("reconciliationRequired")),
            "nextAction": next_action,
        },
        "workspaceCoverage": coverage,
        "source": {
            "semanticInputHash": source.get("semanticInputHash"),
            "inputCoverage": {k: (source.get("inputCoverage") or {}).get(k)
                              for k in ("scope", "status", "jointSolver", "dependencyGraph")},
        },
        "objectiveEvidence": blueprint.get("objectiveEvidence") or {},
        "runtimeProvenance": blueprint.get("runtimeProvenance"),
        "identities": blueprint.get("identities") or {},
        "recovery": blueprint.get("recovery") or {},
        "planningOptions": blueprint.get("planningOptions") or {},
        "artifacts": {
            "blueprint": blueprint.get("blueprintFile"),
            **{k: v for k, v in (blueprint.get("artifacts") or {}).items()},
        },
        "honesty": HONESTY_NOTE,
        "boundaryNote": PLANNING_BOUNDARY_NOTE,
    }
    metrics = {
        "inlineRows": len(dependencies),
        "totalRows": total_rows,
        "inlineGroups": len(groups),
        "totalGroups": total_groups,
        "inlinePackages": inline_packages,
        "totalPackages": total_packages,
        "inlineEvidence": len(visible_evidence),
        "totalEvidence": len(critical_evidence),
        "detailsClipped": details_clipped,
        "coverageClipped": coverage_clipped,
        "targetAssessmentClipped": target_clipped,
    }
    return payload, metrics


def _serialized_bytes(payload: Dict[str, Any]) -> int:
    # Measure the exact wire form the server emits (indent=2, ensure_ascii=False).
    return len(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))


def _finalize_byte_count(payload: Dict[str, Any]) -> int:
    """Set ``payload.truncation.bytes`` to the exact size of the fully
    serialized payload and return it.

    ``bytes`` participates in the payload it measures, so the value is the
    fixed point of ``size = len(serialize(payload))``; the loop converges
    because only the digit count of ``bytes`` (and ``limitsHit``) influence the
    result once set. Terminates deterministically for realistic sizes (guard
    against pathological non-convergence)."""
    for _ in range(32):
        current = payload["truncation"]["bytes"]
        size = _serialized_bytes(payload)
        payload["truncation"]["bytes"] = size
        if size == current:
            return size
    return _serialized_bytes(payload)


def compact_blueprint(
    blueprint: Dict[str, Any],
    max_inline_rows: int = DEFAULT_INLINE_ROWS,
    max_inline_groups: int = DEFAULT_INLINE_GROUPS,
    max_packages_per_group: int = DEFAULT_PACKAGES_PER_GROUP,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
    max_inline_evidence: int = DEFAULT_INLINE_EVIDENCE,
) -> Dict[str, Any]:
    """Project a full blueprint into the compact agent-facing protocol.

    Dependency rows, recommendation groups and per-group package lists are
    bounded (``max_inline_*``) and the WHOLE serialized response -- including
    the ``truncation`` block -- must fit ``max_response_bytes``: when it does
    not, the display caps are reduced deterministically (packages -> groups ->
    rows) until it does, and ``truncation.bytes`` always equals the exact size
    of the final JSON payload (``ensure_ascii=False, indent=2``), never a
    pre-truncation estimate.

    The cap applies to the serialized **payload** (the text in the tool result
    content / the structuredContent object), not to the full MCP transport
    envelope that wraps it. The complete plan is always in the artifact files
    and the response recaps every reduction in ``truncation``, so a client
    never mistakes a clipped answer for the whole picture; the package ->
    verdict/status counts stay complete regardless.

    Raises ``BlueprintError("response-too-large", ...)`` when even the minimal
    required payload (protocol/status/summary/run/policy/artifacts/partial/
    truncation with zero display rows) physically cannot fit the budget --
    a fail-closed outcome rather than shipping a known-too-large answer.
    """
    rows_cap = max(0, int(max_inline_rows))
    groups_cap = max(0, int(max_inline_groups))
    packages_cap = max(0, int(max_packages_per_group))
    evidence_cap = max(0, int(max_inline_evidence))
    budget = max(1, int(max_response_bytes))

    # True once the byte budget ever forces content off the wire; persists for
    # the whole projection so `truncation.limitsHit` stays truthful even after
    # later shrink rounds bring the payload back under the budget.
    bytes_forced = False
    first_build = True

    while True:
        payload, metrics = _compact_payload(
            blueprint,
            max_inline_rows=rows_cap,
            max_inline_groups=groups_cap,
            max_packages_per_group=packages_cap,
            max_inline_evidence=evidence_cap,
        )
        limits_hit: List[str] = []
        if metrics["inlineRows"] < metrics["totalRows"]:
            limits_hit.append("rows")
        if metrics["inlineGroups"] < metrics["totalGroups"]:
            limits_hit.append("groups")
        if metrics["inlinePackages"] < metrics["totalPackages"]:
            limits_hit.append("packages")
        if metrics["inlineEvidence"] < metrics["totalEvidence"]:
            limits_hit.append("decisionEvidence")
        if metrics["detailsClipped"]:
            limits_hit.append("evidenceDetails")
        if metrics["coverageClipped"]:
            limits_hit.append("workspaceCoverage")
        if metrics["targetAssessmentClipped"]:
            limits_hit.append("targetAssessment")

        payload["truncation"] = {
            "truncated": True,  # finalized below once limitsHit is known
            "inlineDependencies": metrics["inlineRows"],
            "totalDependencies": metrics["totalRows"],
            "inlineGroups": metrics["inlineGroups"],
            "totalGroups": metrics["totalGroups"],
            "inlinePackages": metrics["inlinePackages"],
            "totalPackages": metrics["totalPackages"],
            "inlineDecisionEvidence": metrics["inlineEvidence"],
            "totalDecisionEvidence": metrics["totalEvidence"],
            "byteLimit": budget,
            "bytes": 0,
            "limitsHit": [],
        }
        base_size = _finalize_byte_count(payload)
        if first_build:
            bytes_forced = base_size > budget
            first_build = False
        if base_size > budget:
            bytes_forced = True
        if bytes_forced:
            limits_hit.append("bytes")
        payload["truncation"]["limitsHit"] = sorted(limits_hit)
        payload["truncation"]["truncated"] = bool(limits_hit)
        final_size = _finalize_byte_count(payload)
        if final_size <= budget:
            return payload

        if not (packages_cap or groups_cap or rows_cap or evidence_cap):
            # Nothing left to clip -- the minimal required payload does not fit.
            from deploom_mcp.blueprint import BlueprintError
            raise BlueprintError(
                "response-too-large",
                f"minimal required response ({final_size} bytes) exceeds the "
                f"{budget}-byte budget; cannot return a truthful compact plan",
            )
        if packages_cap > 0:
            packages_cap = max(0, packages_cap // 2)
        elif groups_cap > 0:
            groups_cap = max(0, groups_cap // 2)
        elif rows_cap > 0:
            rows_cap = max(0, rows_cap // 2)
        else:
            evidence_cap = max(0, evidence_cap // 2)


def get_upgrade_plan(
    *,
    project_path: str,
    target_level: str = "yellow",
    min_lag_ok_pct: int = 80,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    include_prerelease: bool = False,
    registry: Optional[str] = None,
    use_system_proxy: Optional[bool] = None,
    registry_metadata_ttl: float = 3600.0,
    output_dir: Optional[str] = None,
    exclude: Optional[List[str]] = None,
    target_mode: str = "pinned",
    objective: str = OBJECTIVES[0],
    mutable_packages: Optional[List[str]] = None,
    approved_version_lines: Optional[Dict[str, List[int]]] = None,
    draft_max_candidates: int = 3,
    previous_evidence_digest: Optional[str] = None,
) -> Dict[str, Any]:
    """Synchronous variant: run the Stage 1 boundary and return the compact
    protocol. Used by embedding/CLI callers that run outside an event loop;
    the MCP tool itself uses the cancellable async path."""
    error, req = _build_request(
        project_path=project_path,
        target_level=target_level,
        min_lag_ok_pct=min_lag_ok_pct,
        deadline_seconds=deadline_seconds,
        include_prerelease=include_prerelease,
        registry=registry,
        use_system_proxy=use_system_proxy,
        registry_metadata_ttl=registry_metadata_ttl,
        output_dir=output_dir,
        exclude=exclude,
        target_mode=target_mode,
        objective=objective,
        mutable_packages=mutable_packages,
        approved_version_lines=approved_version_lines,
        draft_max_candidates=draft_max_candidates,
        previous_evidence_digest=previous_evidence_digest,
    )
    if error is not None:
        return {"ok": False, "error": error}
    assert req is not None
    result = generate_upgrade_blueprint(str(req["purl"]), policy=req["policy"], options=req["options"])
    return _result_from_planning(result, req["policy"])


@server.tool(
    name=TOOL_NAME,
    title="Upgrade plan",
    description=(
        "Produce a planning-only dependency upgrade plan for a Node project. "
        "Runs the pinned DepLoom planning core as an isolated subprocess and "
        "returns a compact Upgrade Blueprint protocol. Never writes to the project; "
        "output and metadata caching happen in a managed directory outside the "
        "project. exclude lists package names removed from planning scope BEFORE "
        "planning (not filtered afterwards). Honesty: authority is always "
        "PLANNING_ONLY; a PROPOSED verdict means 'can be proposed', NOT 'already "
        "updated'. target_mode selects pinned targets or advisory suggestions; "
        "neither relaxes compatibility constraints. mutable_packages limits changes "
        "while retaining the other packages as constraints. draft_max_candidates "
        "widens candidate evidence independently of the deadline. Review critical "
        "evidence and workspaceCoverage before acting within existing user authority."
    ),
)
async def get_upgrade_plan_tool(
    project_path: str,
    target_level: str = "yellow",
    min_lag_ok_pct: int = 80,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    include_prerelease: bool = False,
    registry: Optional[str] = None,
    use_system_proxy: Optional[bool] = None,
    registry_metadata_ttl: float = 3600.0,
    output_dir: Optional[str] = None,
    exclude: Optional[List[str]] = None,
    target_mode: str = "pinned",
    objective: str = OBJECTIVES[0],
    mutable_packages: Optional[List[str]] = None,
    approved_version_lines: Optional[Dict[str, List[int]]] = None,
    draft_max_candidates: int = 3,
    previous_evidence_digest: Optional[str] = None,
    ctx: Context = None,  # type: ignore[assignment]  # injected by MCPServer
) -> CallToolResult:
    error, req = _build_request(
        project_path=project_path,
        target_level=target_level,
        min_lag_ok_pct=min_lag_ok_pct,
        deadline_seconds=deadline_seconds,
        include_prerelease=include_prerelease,
        registry=registry,
        use_system_proxy=use_system_proxy,
        registry_metadata_ttl=registry_metadata_ttl,
        output_dir=output_dir,
        exclude=exclude,
        target_mode=target_mode,
        objective=objective,
        mutable_packages=mutable_packages,
        approved_version_lines=approved_version_lines,
        draft_max_candidates=draft_max_candidates,
        previous_evidence_digest=previous_evidence_digest,
    )
    if error is not None:
        return _result({"ok": False, "error": error})
    assert req is not None
    # Cancellable: server shutdown / client disconnect cancels this await, and
    # generate_upgrade_blueprint_async kills the whole child process tree first.
    result = await generate_upgrade_blueprint_async(
        str(req["purl"]), policy=req["policy"], options=req["options"]
    )
    return _result(_result_from_planning(result, req["policy"]))



@server.tool(
    name="evaluate_assignment",
    title="Evaluate alternative dependency assignment",
    description=(
        "Inspect an exact-version candidate assignment against a previously "
        "published full Upgrade Blueprint. Checks recorded decision authority, "
        "mutable/excluded declarations, approved major lines, source drift, "
        "known plan blockers, and whether pinned targets require replanning. "
        "Keys may be package names or declarationIds; unspecified rows keep the "
        "published selected/current version. This is NOT a fresh Z3 solver, "
        "peer registry check, installed-tree audit or security/policy certification. "
        "An alternative remains UNKNOWN until independently verified. "
        "Pass the absolute path from get_upgrade_plan.artifacts.blueprint."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
)
async def evaluate_assignment_tool(
    blueprint_path: str,
    assignment: Dict[str, str],
    expected_decision_digest: Optional[str] = None,
) -> CallToolResult:
    return _result(evaluate_assignment(
        blueprint_path=blueprint_path,
        assignment=assignment,
        expected_decision_digest=expected_decision_digest,
    ))


@server.tool(
    name="explain_conflict",
    title="Explain a published dependency constraint",
    description=(
        "Explain a known conflict, blocked target or missing evidence in a "
        "full published Upgrade Blueprint. Optionally filter by package; "
        "returns typed original reason, lifecycle, package/project scope, "
        "JSON evidence pointer, next action and recorded solver components. "
        "Does not reconstruct the solver model, invent an alternative, "
        "replan, install or verify runtime/security. No documented conflict "
        "does not imply compatibility. Pass the absolute path from "
        "get_upgrade_plan.artifacts.blueprint."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
)
async def explain_conflict_tool(
    blueprint_path: str,
    package: Optional[str] = None,
    expected_decision_digest: Optional[str] = None,
    max_items: int = 20,
) -> CallToolResult:
    return _result(explain_conflict(
        blueprint_path=blueprint_path,
        package=package,
        expected_decision_digest=expected_decision_digest,
        max_items=max_items,
    ))


def _result(payload: Dict[str, Any]) -> CallToolResult:
    is_error = not bool(payload.get("ok"))
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))],
        isError=is_error,
        structuredContent=payload,
    )


def main() -> int:
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
