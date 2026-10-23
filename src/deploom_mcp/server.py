"""DepLoom MCP server: exposes `get_upgrade_plan` over MCP stdio.

Stage 2 boundary, built on the official MCP SDK (`MCPServer`). It does NOT
reimplement or extend planning: it wraps the Stage 1/Stage B edge boundary
(`generate_upgrade_blueprint` in `deploom_mcp.blueprint`) and exposes it to
MCP clients as a single `get_upgrade_plan` tool per the Stage 1 document
section H recommendations.

* compact protocol back to the agent (verdicts, status counts, recommendation
  groups, artifact paths) -- NOT the whole plan.json in the context;
* honest statuses: every response carries `authority=PLANNING_ONLY`, a
  `PROPOSED` verdict means "can be proposed", not "already updated", and
  `needsReview` items must be confirmed with the project owner first;
* budget: default deadline 20 s (range 5..600), planning metadata cached in a
  managed directory shared between calls (`DEPLOOM_MCP_STATE_DIR`);
* artifacts and output always live outside the analysed project
  (`output-inside-project` guard).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult, TextContent

from deploom_mcp import __version__
from deploom_mcp.blueprint import (
    BlueprintOptions,
    BlueprintPolicy,
    generate_upgrade_blueprint,
)

SERVER_NAME = "deploom-mcp"
TOOL_NAME = "get_upgrade_plan"
KIND = "upgrade-blueprint"

# Section H budget: default deadline 15..30 s. We default to 20 s and clamp to
# a safe hard range so a client cannot hang the server on an absurd budget.
DEFAULT_DEADLINE_SECONDS = 20.0
MIN_DEADLINE_SECONDS = 5.0
MAX_DEADLINE_SECONDS = 600.0

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


def _resolve_outside(project: Path, out: Path) -> Optional[Path]:
    """Return the resolved output dir if it is NOT inside/above the project."""
    try:
        project.relative_to(out)
        return None  # output would be an ancestor/parent of the project: unsafe
    except ValueError:
        pass
    try:
        out.relative_to(project)
        return None  # output lives inside the analysed project: refuse
    except ValueError:
        return out


def _error(code: str, message: str, detail: Optional[Any] = None) -> Dict[str, Any]:
    return {"code": code, "message": message, "detail": detail}


def compact_blueprint(blueprint: Dict[str, Any]) -> Dict[str, Any]:
    """Project a full blueprint into the compact agent-facing protocol."""
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
    dependencies = []
    for row in blueprint.get("dependencies") or []:
        dependencies.append({
            "package": row.get("package"),
            "status": row.get("status"),
            "current": row.get("current"),
            "target": row.get("target"),
            "group": row.get("group"),
            "needsReview": bool(row.get("needsReview")),
        })

    return {
        "protocol": KIND,
        "schemaVersion": blueprint.get("schemaVersion"),
        "run": {
            "runId": run.get("runId"),
            "projectId": run.get("projectId"),
            "project": project.get("name"),
        },
        "status": {
            "draftStatus": status.get("draftStatus"),
            "verificationStatus": status.get("verificationStatus", "NOT_VERIFIED"),
            "authority": status.get("authority", "PLANNING_ONLY"),
            "compatibility": status.get("compatibility", "UNKNOWN"),
            "parallelExecutionSafety": status.get("parallelExecutionSafety", "UNPROVEN"),
            "partialReason": status.get("partialReason"),
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
) -> Dict[str, Any]:
    """Run the Stage 1 boundary against `project_path`; return compact protocol.

    Always JSON-serializable. On misuse returns `ok=False` with a
    machine-readable `error` (invalid-goal / invalid-deadline /
    output-inside-project / invalid-args / project-not-found /
    invalid-project / generator-* / manifest-*).
    """
    if not project_path or not isinstance(project_path, str):
        return {"ok": False, "error": _error("invalid-args", "project_path must be a non-empty string")}
    purl = Path(project_path).expanduser().resolve()

    if target_level not in ("yellow", "green"):
        return {"ok": False, "error": _error("invalid-goal", f"target_level must be 'yellow' or 'green', got {target_level!r}")}

    try:
        deadline = float(deadline_seconds)
    except (TypeError, ValueError):
        return {"ok": False, "error": _error("invalid-deadline", "deadline_seconds must be a number")}
    if not deadline or deadline != deadline or deadline <= 0:
        return {"ok": False, "error": _error("invalid-deadline", "deadline_seconds must be a positive number")}
    deadline = max(MIN_DEADLINE_SECONDS, min(MAX_DEADLINE_SECONDS, deadline))

    state = _ensure_state_dir()

    out = None
    if output_dir:
        candidate = Path(output_dir).expanduser().resolve()
        resolved = _resolve_outside(purl, candidate)
        if resolved is None:
            return {
                "ok": False,
                "error": _error(
                    "output-inside-project",
                    f"output_dir {candidate} must not be inside the analysed project {purl}",
                ),
            }
        out = str(resolved)
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
        registry_metadata_ttl=registry_metadata_ttl,
        output_dir=out,
    )
    policy = BlueprintPolicy(target_level=target_level, min_lag_ok_pct=int(min_lag_ok_pct))

    result = generate_upgrade_blueprint(str(purl), policy=policy, options=options)

    if not result.ok:
        err = result.error or _error("internal-error", "planner failed without a machine-readable error")
        return {"ok": False, "runId": result.run_id, "error": err}

    return {"ok": True, **compact_blueprint(result.blueprint or {})}


@server.tool(
    name=TOOL_NAME,
    title="Upgrade plan",
    description=(
        "Produce a planning-only dependency upgrade plan for a Node project. "
        "Runs the pinned DepLoom planning core as an isolated subprocess and "
        "returns a compact Upgrade Blueprint protocol. Never writes to the project; "
        "output and metadata caching happen in a managed directory outside the "
        "project. Honesty: authority is always PLANNING_ONLY; a PROPOSED verdict "
        "means 'can be proposed', NOT 'already updated'; confirm with the project "
        "owner before acting on any dependency flagged needsReview."
    ),
)
def get_upgrade_plan_tool(
    project_path: str,
    target_level: str = "yellow",
    min_lag_ok_pct: int = 80,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    include_prerelease: bool = False,
    registry: Optional[str] = None,
    registry_metadata_ttl: float = 3600.0,
    output_dir: Optional[str] = None,
    ctx: Context = None,  # type: ignore[assignment]  # injected by MCPServer
) -> CallToolResult:
    if ctx is not None and getattr(ctx, "cancel_requested", None) is not None:
        if ctx.cancel_requested.is_set():
            return _result({"ok": False, "error": _error("cancelled", "request was cancelled before planning started")})
    return _result(get_upgrade_plan(
        project_path=project_path,
        target_level=target_level,
        min_lag_ok_pct=min_lag_ok_pct,
        deadline_seconds=deadline_seconds,
        include_prerelease=include_prerelease,
        registry=registry,
        registry_metadata_ttl=registry_metadata_ttl,
        output_dir=output_dir,
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
