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
  rows; a larger plan stays fully on disk in the artifact files and the
  response says so explicitly (`truncation`), it never silently drops data;
* exclusions are real planning scope: `exclude` is passed to the planner as
  `--exclude-dependency` BEFORE planning (never filtered out afterwards);
* honest statuses: every response carries `authority=PLANNING_ONLY`, a
  `PROPOSED` verdict means "can be proposed", not "already updated", and
  `needsReview` items must be confirmed with the project owner first;
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
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult, TextContent

from deploom_mcp import __version__
from deploom_mcp.blueprint import (
    BlueprintOptions,
    BlueprintPolicy,
    generate_upgrade_blueprint,
    generate_upgrade_blueprint_async,
    resolve_output_outside,
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
        return Path(env).expanduser().resolve()
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

    state = _ensure_state_dir()

    out: Optional[str] = None
    if output_dir:
        candidate = Path(output_dir).expanduser().resolve()
        if resolve_output_outside(purl, candidate) is None:
            return (
                _error(
                    "output-inside-project",
                    f"output_dir {candidate} must not be inside the analysed project {purl}",
                ),
                None,
            )
        out = str(candidate)
    else:
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
    )
    policy = BlueprintPolicy(
        target_level=target_level,
        min_lag_ok_pct=min_lag_ok_pct,
        exclusions=tuple(exclusions),
    )
    return None, {"purl": purl, "options": options, "policy": policy}


def _result_from_planning(result) -> Dict[str, Any]:
    if not result.ok:
        err = result.error or _error("internal-error", "planner failed without a machine-readable error")
        return {"ok": False, "runId": result.run_id, "error": err}
    return {"ok": True, **compact_blueprint(result.blueprint or {})}


def compact_blueprint(blueprint: Dict[str, Any], max_inline_rows: int = DEFAULT_INLINE_ROWS) -> Dict[str, Any]:
    """Project a full blueprint into the compact agent-facing protocol.

    Dependency rows above ``max_inline_rows`` are NOT inlined; the full plan is
    in the artifact files and the response flags the truncation explicitly
    (``truncation``), so a client never mistakes a clipped answer for the whole
    picture. The package -> verdict/status counts stay complete regardless.
    """
    status = blueprint.get("status") or {}
    summary = blueprint.get("summary") or {}
    source = blueprint.get("source") or {}
    project = source.get("project") or {}
    run = blueprint.get("run") or {}
    groups = []
    for group in blueprint.get("recommendationGroups") or []:
        groups.append({
            "group": group.get("group"),
            "count": group.get("count"),
            "needsReview": bool(group.get("needsReview")),
            "packages": list(group.get("packages") or []),
        })

    all_rows = blueprint.get("dependencies") or []
    total_rows = len(all_rows)
    truncated = total_rows > max_inline_rows
    rows = all_rows[:max_inline_rows] if truncated else all_rows
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
    return {
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
        "summary": {
            "totalDependencies": summary.get("totalDependencies"),
            "proposed": summary.get("proposed"),
            "needsReview": summary.get("needsReview"),
            "statusCounts": summary.get("statusCounts") or {},
        },
        "truncation": {
            "truncated": truncated,
            "inlineDependencies": len(rows),
            "totalDependencies": total_rows,
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
    return _result_from_planning(result)


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
    return _result(_result_from_planning(result))


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
