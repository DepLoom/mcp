# DepLoom MCP user guide

[Русский](USERGUIDE.ru.md) · [Overview](README.md) · [Sharing with colleagues](docs/SHARING_WITH_COLLEAGUES.md)

## Choose your setup

- **Self-hosted MCP (stdio):** each developer runs MCP locally; the coding client starts it.
- **Networked MCP (remote HTTP):** see [HTTP deployment](docs/HTTP_DEPLOYMENT.md) and the security limits below.

The wheel includes the pinned headless Core and canonical Skill. No DepLoom Desktop, Electron, Git clone or separate Core install is required.

## Network access and project data

Local stdio describes the transport between client and server, not an offline-only planner. Planning can request package/version metadata from the configured registry and vulnerability evidence from OSV (the bundled Core defaults to `api.osv.dev`). Setting `registry` does not redirect OSV traffic to that registry. Optional migration-reference requests can contact their documented official sources. Use a non-sensitive first trial and check your organization's data policy before planning private dependencies. Proxy settings change routing, not data permissions.

## Self-hosted MCP (stdio)

### 1. Install

Use Python 3.12 or 3.13 for a first trial. The package declares Python `>=3.12,<3.15`. You also need the project's Node/package manager and access to its approved registry.

Windows PowerShell:

```powershell
$install = Join-Path $env:LOCALAPPDATA 'DepLoom'
py -3.12 -m venv "$install\venv"
$python = Join-Path $install 'venv\Scripts\python.exe'
& $python -m pip install "https://github.com/DepLoom/mcp/releases/download/v0.2.2/deploom_mcp-0.2.2-py3-none-any.whl"
& $python -m pip check
& $python -c "import importlib.metadata; print(importlib.metadata.version('deploom-mcp'))"
```

Or download the wheel, compare its SHA-256 with `SHA256SUMS.txt` from the [same release](https://github.com/DepLoom/mcp/releases/tag/v0.2.2), and install the local file. These are GitHub release assets; a PyPI publication is not assumed.

Linux with Python 3.12:

```bash
python3.12 -m venv "$HOME/.local/share/deploom/venv"
PYTHON="$HOME/.local/share/deploom/venv/bin/python"
"$PYTHON" -m pip install "https://github.com/DepLoom/mcp/releases/download/v0.2.2/deploom_mcp-0.2.2-py3-none-any.whl"
"$PYTHON" -m pip check
```

Use your normal approved Python index, certificates and proxy. The [offline kit](docs/SHARING_WITH_COLLEAGUES.md) avoids repeating Python package downloads.

### 2. Install the Skill

For OpenCode on Windows:

```powershell
& "$install\venv\Scripts\deploom-install-skill.exe" --client opencode --scope user --dry-run
& "$install\venv\Scripts\deploom-install-skill.exe" --client opencode --scope user --verify
```

Linux:

```bash
"$HOME/.local/share/deploom/venv/bin/deploom-install-skill" --client opencode --scope user --verify
```

The folder is `~/.config/opencode/skills/deploom-upgrade/`. It contains `SKILL.md`, `contract.json`, references, examples and scripts. Release 0.2.2 ships Skill contract **2.0.2**.

For a differing older installation, back it up, inspect `--dry-run`, then update with `--force --verify`. Foreign extra files are preserved. Do not translate installed hash-bound Skill files in place.

Other destinations: `--client codex`, `--client claude-code`, or `--scope project --project /absolute/path/to/project`. Skill installation does **not** register MCP or prove that a running client loaded it.

### 3. Connect OpenCode V2

Merge this server into the existing `mcp.servers` in `~/.config/opencode/opencode.jsonc`. Preserve other providers/servers/permissions. Replace `YOUR_USER` with your own directory.

```jsonc
{
  "mcp": {
    "servers": {
      "deploom": {
        "type": "local",
        "command": [
          "C:/Users/YOUR_USER/AppData/Local/DepLoom/venv/Scripts/python.exe",
          "-m",
          "deploom_mcp"
        ],
        "environment": {
          "DEPLOOM_MCP_STATE_DIR": "C:/Users/YOUR_USER/AppData/Local/DepLoom/state"
        },
        "disabled": false
      }
    }
  }
}
```

On Linux use the absolute `.../.local/share/deploom/venv/bin/python` path and a writable state directory. V2 uses `mcp.servers` and `disabled`, not V1's `mcp.<name>` and `enabled`. [Official OpenCode configuration](https://opencode.ai/v2/docs/mcp-servers).

The wheel finds its own bundled Core. Do not retain obsolete `PYTHONPATH` or `DEPLOOM_MCP_GENERATOR_PATH` overrides from an old checkout. Codex/Claude Code use different configuration formats: see [integrations](integrations/).

### 4. Restart and check

Restart OpenCode. In its MCP view (`/mcps`), confirm `deploom` is connected. Ask the agent to load the Skill named exactly **`deploom-upgrade`**.

The standard 0.2.2 server exposes nine tools:

- Planning: `get_upgrade_plan`, `evaluate_assignment`, `explain_conflict`.
- Explicit goals: `get_goal_plan`, `apply_goal_candidate`, `verify_goal_candidate`, `get_goal_checkpoint`, `get_goal_report`.
- Read-only inventory: `observe_workspace_inventory_v1`.

Optional: replace the module with `deploom_mcp.audit_server` to add `audit_dependencies`. See [audit support](docs/DEPENDENCY_AUDIT.md).

The client normally starts the server. Running `python -m deploom_mcp` yourself waits for MCP protocol input; it does not open a web page. Do not paste chat prompts into its stdin.

## First request: plan only

Open a small, non-sensitive project and send:

> Load deploom-upgrade. Use DepLoom MCP to prepare a Yellow plan. PLAN_ONLY: do not change files, install, commit or publish. Use this project's approved registry. Inventory all workspaces, report actual planning coverage, unknowns and blockers. Explain minimal changes and save the real MCP request and Blueprint path.

A direct tool-call example:

```json
{
  "project_path": "C:/projects/example-app",
  "target_level": "yellow",
  "min_lag_ok_pct": 80,
  "target_mode": "advisory",
  "objective": "minimum-change-to-policy",
  "deadline_seconds": 120
}
```

Pass the approved URL in `registry` when needed; never include credentials in prompts/URLs. Read the full Blueprint: `DRAFT_READY` can still have unknown evidence, candidate caps and partial workspace coverage.

## Authorize an actual upgrade separately

> Apply the agreed Yellow plan in an isolated working copy. Preserve my changes, scope, exclusions and peer/engine constraints. Verify clean installation, the physical tree, required project checks and relevant runtime behavior. Run the final policy audit. Report old/new versions, remaining blockers and evidence. Do not commit, push, release or deploy.

Specify exact targets, excluded packages and approved major lines. A health target does not mean “everything to latest”.

`PROPOSED` and `PLANNING_ONLY/NOT_VERIFIED` are proposals, not installed or runtime-verified upgrades. Build/test success proves only the executed checks. Final policy evidence must retain the scope, denominator, thresholds, unknowns and freshness.

For workspaces, `SINGLE_MANIFEST_PLANNING`, `PARTIAL` or `jointAssignment=NOT_COMPUTED` requires client-side reconciliation. Inventory does not authorize whole-workspace execution. Explicit-goal verification/apply needs host authorization and a [supported standalone npm profile](docs/GOAL_EXECUTION_SUPPORT.md). Agent-managed edits are not automatically a Core solver verdict/checkpoint.

## Networked MCP (remote HTTP)

The HTTP transport and container setup are described in [HTTP deployment](docs/HTTP_DEPLOYMENT.md). A shared host still needs authenticated transport, project-path/permission boundaries, isolated state/evidence and concurrency controls. Paths refer to the **server's** filesystem, not a colleague's laptop. Exposing planning remotely without a path boundary can expose host files.

For today's trials distribute the [local installation kit](docs/SHARING_WITH_COLLEAGUES.md). An HTTP server serving wheel files is **file distribution, not Networked MCP**.

## Troubleshooting

- MCP connected, Skill missing: install both parts, restart, explicitly load `deploom-upgrade`.
- Startup failure: inspect stderr, absolute Python path and actual installed version.
- Wrong Core/version: check runtime provenance and stale source overrides.
- Registry/TLS/proxy failure: retain UNKNOWN and repair the approved route; see [proxy](docs/SYSTEM_PROXY.md). Never silently switch a corporate project to public services.
- Capped/partial plan: inspect recovery actions; do not repeat an unchanged request.

[Technical documentation](docs/INDEX.md).
