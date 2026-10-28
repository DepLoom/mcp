"""Opt-in MCP server: existing planning tool plus read-only dependency audit.

Use ``python -m deploom_mcp.audit_server``. The standard experiment entrypoint
is intentionally unchanged until the frozen A/B study has completed.
"""
from __future__ import annotations

import json
from mcp_types import CallToolResult, TextContent, ToolAnnotations

from deploom_mcp.server import server
from deploom_mcp.dependency_audit import audit_dependencies


@server.tool(
    name="audit_dependencies",
    title="Audit dependency lockfile",
    description=(
        "Audit existing npm or Yarn lockfile dependencies without modifying the project. "
        "npm uses npm audit; Yarn Classic uses exact yarn.lock versions through npm audit "
        "without npm install or re-resolution; Yarn Berry uses native yarn npm audit "
        "--all --recursive. Audits the canonical lockfile owner, including its workspaces. "
        "Sends package/version inventory to the configured registry. Requires prepared "
        "npm >=7 or Yarn Berry >=2 on PATH; never downloads a CLI, installs dependencies, "
        "runs project scripts or audit fix. include_lag optionally checks declared publish-date "
        "lag using the pinned Core manual-audit implementation. Findings, unavailable evidence "
        "and command failure are distinct. Complete evidence is not an exploitability, "
        "runtime or Yellow/Green certification. Outputs sanitized artifacts outside the project."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True),
)
async def audit_dependencies_tool(
    project_path: str,
    package_manager: str = "auto",
    registry: str | None = None,
    deadline_seconds: float = 120,
    include_lag: bool = False,
    lag_months: int = 12,
    output_dir: str | None = None,
    use_system_proxy: bool | None = None,
) -> CallToolResult:
    payload = await audit_dependencies(project_path=project_path, package_manager=package_manager,
        registry=registry, deadline_seconds=deadline_seconds, include_lag=include_lag,
        lag_months=lag_months, output_dir=output_dir,
        use_system_proxy=use_system_proxy)
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
                          structuredContent=payload, isError=not payload.get("ok", False))


def main() -> int:
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
