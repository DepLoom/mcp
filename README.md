# DepLoom MCP

Headless upgrade-planning boundary and MCP server for the DepLoom ecosystem.

**Difference from DepLoom Desktop.** DepLoom Desktop (`AlexanderLevenskikh/deploom`)
is the stable product: it owns the Python Core, the solver, Draft generation,
the Electron Desktop, FLOW/Autopilot and the migration/checkpoint machinery.
This repository (`DepLoom/mcp`) does **not** fork that logic. It depends on a
**pinned** commit of the upstream Core (Git submodule `upstream/deploom`) and
exposes a single, planning-only tool: `get_upgrade_plan`.

**What MCP does** — produce a correct, structured upgrade plan for a project.

**What MCP does NOT do** — Autopilot, background migration jobs, agent
dispatch, repair loops, REST, dashboards, GitLab/Renovate integration, or any
form of verification/execution. The plan is `PLANNING_ONLY` and never claims
compatibility as proven.

## Architecture

```text
Developer
    │
    ▼
OpenCode / other MCP client
    │ get_upgrade_plan
    ▼
DepLoom MCP Server
    │
    ▼
Upgrade Blueprint Adapter
    │
    ▼
Pinned DepLoom Planning Core   (upstream/deploom submodule)
    │
    ▼
Structured Upgrade Blueprint
    │
    ▼
AI Agent  → updates manifests, adapts code, reports verification
```

Deployment topology and contracts are described in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
The boundary contract is detailed in
[`docs/STAGE_01_UPGRADE_BLUEPRINT.md`](docs/STAGE_01_UPGRADE_BLUEPRINT.md);
the MCP server in [`docs/STAGE_02_MCP_SERVER.md`](docs/STAGE_02_MCP_SERVER.md).

## Repository layout

```text
upstream/deploom/          pinned planning core (Git submodule, commit 9e20107)
src/deploom_mcp/           Python package (blueprint adapter + MCP server)
contracts/                 Upgrade Blueprint JSON Schema (v2)
tests/                     unit, protocol and production-boundary tests
benchmarks/                Stage 1 + Stage 2 performance measurements
docs/                      architecture and stage documentation
examples/opencode/         OpenCode configuration example
```

## Requirements

- Python **>= 3.12, < 3.15** (the pinned Core and the MCP SDK are verified in CI on 3.12).
- Access to `github.com` to fetch the submodule and Python packages.
- Windows and Linux are supported; z3-solver is installed from a platform wheel.

## Installation (from a clean clone)

```bash
git clone https://github.com/DepLoom/mcp.git
cd mcp
git submodule update --init --recursive

python -m venv .venv
.venv/Scripts/python -m pip install -e .      # Windows
# .venv/bin/python -m pip install -e .        # Linux/macOS
```

The pinned Core is fetched as a submodule; no files are copied from a desktop
checkout and no Desktop installation is required.

## Running the MCP server (stdio)

```bash
.venv/Scripts/python -m deploom_mcp           # Windows
# .venv/bin/python -m deploom_mcp             # Linux/macOS
```

List of published tools: exactly one — `get_upgrade_plan`.

### `get_upgrade_plan` (contract)

```jsonc
{
  "project_path": "/abs/path/to/project",   // read-only; a package.json is required
  "target_level": "yellow",                  // "yellow" | "green"  (default "yellow")
  "min_lag_ok_pct": 80,                      // minimum registry-lag % for a clean proposal
  "deadline_seconds": 20,                     // default 20, clamped to [5, 600]
  "include_prerelease": false,               // allow prerelease targets
  "registry": null,                          // optional npm registry; default from the core
  "registry_metadata_ttl": 3600,             // packument TTL seconds for the shared cache
  "output_dir": null                         // optional; MUST be outside the project
}
```

The response is a **compact** representation of the Upgrade Blueprint
(`protocol=upgrade-blueprint`, run, status, summary, recommendation groups,
compact dependency rows, artifact paths) plus planning boundaries. The full
Blueprint and raw planner artifacts are written under a controlled artifact
directory whose paths are returned; an AI agent reads them with its own file
tools.

The response is always `NOT_VERIFIED / PLANNING_ONLY / UNKNOWN / UNPROVEN`: a
`PROPOSED` verdict means "this upgrade **can** be proposed", not "already
updated", and dependencies flagged `needsReview` must be confirmed with the
project owner first. Machine-readable errors use `{"ok": false, "error":
{code, message, ...}}`; see
[`docs/STAGE_02_MCP_SERVER.md`](docs/STAGE_02_MCP_SERVER.md) for the full
contract, error codes and partial-plan handling.

## Connecting OpenCode

See [`examples/opencode/README.md`](examples/opencode/README.md). The example
config is meant to be copied into your own OpenCode configuration; this
repository never modifies global OpenCode configuration.

## Tests

```bash
.venv/Scripts/python -m unittest discover -s tests -v
```

The suite covers unit projection tests, real MCP protocol tests over stdio, and
a production-boundary test that runs the **real** pinned planner against a real
fixture. CI runs the same suite on a clean checkout.

## Updating the Core

The Core is a pinned submodule. Updating it is an explicit, reviewable
dependency change:

```bash
git -C upstream/deploom fetch origin
git -C upstream/deploom checkout <new pinned SHA>
git add upstream/deploom
git commit -m "chore(core): pin deploy core to <SHA>"
```

Then re-run the full test suite. Do **not** edit files inside `upstream/deploom`.

## License / distribution

This repository does **not** re-license the upstream Core. The upstream
repository contains no LICENSE file, so its distribution terms are considered
unresolved; no wheel or bundle that includes the Core is published. This
repository itself adds no license until the ownership and upstream terms are
resolved. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
