"""DepLoom MCP server: exposes `get_upgrade_plan` over MCP stdio.

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
  `needsReview` items must be confirmed with the project owner first;
* budget: default deadline 120 s (range 5..600), planning metadata cached in a
  managed directory shared between calls (`DEPLOOM_MCP_STATE_DIR`);
* artifacts and output always live outside the analysed project
  (`output-inside-project` guard, enforced again at the write boundary).
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult, TextContent

from deploom_mcp import __version__
from deploom_mcp.blueprint import (
    BlueprintError,
    BlueprintOptions,
    BlueprintPolicy,
    generate_upgrade_blueprint,
    generate_upgrade_blueprint_async,
    resolve_output_outside,
)

SERVER_NAME = "deploom-mcp"
TOOL_NAME = "get_upgrade_plan"
KIND = "upgrade-blueprint"

# Section H budget: the default must leave headroom for a real project -- the
# Stage 3.5 witness run (selfservice.shopping-cart-front, 61 deps) needed ~58 s
# of planning at a 600 s budget, so 20 s would silently truncate every default
# call into DRAFT_PARTIAL. We default to 120 s and clamp to a safe hard range
# so a client cannot hang the server on an absurd budget.
DEFAULT_DEADLINE_SECONDS = 120.0
MIN_DEADLINE_SECONDS = 5.0
MAX_DEADLINE_SECONDS = 600.0

# Compact protocol: cap the number of inlined dependency rows. The complete
# plan is always on disk (artifacts), so this bounds context, never data.
DEFAULT_INLINE_ROWS = 100

# P2 (deterministic response size): display-priority groups and the packages
# inside each group are also bounded, and the whole serialized response must
# fit a byte budget before it is returned. All reductions are pure truncation
# of display lists -- status counts, per-project health and artifact paths stay
# complete -- and are recapped in `truncation.limitsHit`.
DEFAULT_INLINE_GROUPS = 50
DEFAULT_PACKAGES_PER_GROUP = 50
MAX_RESPONSE_BYTES = 512 * 1024  # 512 KiB deterministic cap on the JSON response

HONESTY_NOTE = (
    "Planning-only: authority=PLANNING_ONLY (nothing was installed or verified). "
    "A PROPOSED verdict means 'this upgrade can be proposed', NOT 'already updated'. "
    "Confirm with the project owner before acting on any dependency flagged needsReview."
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
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Validate every input (fail-closed) and build the planner request."""
    if not project_path or not isinstance(project_path, str):
        return _error("invalid-args", "project_path must be a non-empty string"), None
    purl = Path(project_path).expanduser().resolve()

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
        draft_deadline_seconds=deadline,
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
    )
    return None, {"purl": purl, "options": options, "policy": policy}


def _policy_snapshot(requested: BlueprintPolicy) -> Dict[str, Any]:
    """The agent-facing policy knobs exactly as the caller requested them."""
    return {
        "targetLevel": requested.target_level,
        "minLagOkPct": requested.min_lag_ok_pct,
        "exclusions": list(requested.exclusions),
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
    if not mismatched:
        return None
    return {
        "requested": dict(requested),
        "applied": {k: applied.get(k) for k in ("targetLevel", "minLagOkPct", "exclusions", "policyHash")},
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
    return response


def _compact_payload(
    blueprint: Dict[str, Any],
    *,
    max_inline_rows: int,
    max_inline_groups: int,
    max_packages_per_group: int,
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

    all_rows = blueprint.get("dependencies") or []
    total_rows = len(all_rows)
    rows = all_rows[:max_inline_rows]
    dependencies = []
    for row in rows:
        dependencies.append({
            "package": row.get("package"),
            "status": row.get("status"),
            "current": row.get("current"),
            "target": row.get("target"),
            "group": row.get("group"),
            "needsReview": bool(row.get("needsReview")),
        })

    draft_status = status.get("draftStatus")
    is_partial = draft_status == "DRAFT_PARTIAL"
    processed = summary.get("processed")
    processed_total = summary.get("processedTotal")
    policy = blueprint.get("policy") or {}
    payload = {
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
        },
        "summary": {
            "totalDependencies": summary.get("totalDependencies"),
            "proposed": summary.get("proposed"),
            "needsReview": summary.get("needsReview"),
            "statusCounts": summary.get("statusCounts") or {},
        },
        "recommendationGroups": groups,
        "dependencies": dependencies,
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
        )
        limits_hit: List[str] = []
        if metrics["inlineRows"] < metrics["totalRows"]:
            limits_hit.append("rows")
        if metrics["inlineGroups"] < metrics["totalGroups"]:
            limits_hit.append("groups")
        if metrics["inlinePackages"] < metrics["totalPackages"]:
            limits_hit.append("packages")

        payload["truncation"] = {
            "truncated": True,  # finalized below once limitsHit is known
            "inlineDependencies": metrics["inlineRows"],
            "totalDependencies": metrics["totalRows"],
            "inlineGroups": metrics["inlineGroups"],
            "totalGroups": metrics["totalGroups"],
            "inlinePackages": metrics["inlinePackages"],
            "totalPackages": metrics["totalPackages"],
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

        if not (packages_cap or groups_cap or rows_cap):
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
        else:
            rows_cap = max(0, rows_cap // 2)


def get_upgrade_plan(
    *,
    project_path: str,
    target_level: str = "yellow",
    min_lag_ok_pct: int = 80,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    include_prerelease: bool = False,
    registry: Optional[str] = None,
    registry_metadata_ttl: float = 3600.0,
    output_dir: Optional[str] = None,
    exclude: Optional[List[str]] = None,
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
        registry_metadata_ttl=registry_metadata_ttl,
        output_dir=output_dir,
        exclude=exclude,
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
        "updated'; confirm with the project owner before acting on any dependency "
        "flagged needsReview."
    ),
)
async def get_upgrade_plan_tool(
    project_path: str,
    target_level: str = "yellow",
    min_lag_ok_pct: int = 80,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    include_prerelease: bool = False,
    registry: Optional[str] = None,
    registry_metadata_ttl: float = 3600.0,
    output_dir: Optional[str] = None,
    exclude: Optional[List[str]] = None,
    ctx: Context = None,  # type: ignore[assignment]  # injected by MCPServer
) -> CallToolResult:
    error, req = _build_request(
        project_path=project_path,
        target_level=target_level,
        min_lag_ok_pct=min_lag_ok_pct,
        deadline_seconds=deadline_seconds,
        include_prerelease=include_prerelease,
        registry=registry,
        registry_metadata_ttl=registry_metadata_ttl,
        output_dir=output_dir,
        exclude=exclude,
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
