# Connecting OpenCode to DepLoom MCP

Copy this example into your own OpenCode configuration; this repository never
modifies global OpenCode configuration.

## Prerequisites

```bash
git submodule update --init --recursive
python -m venv .venv
.venv/Scripts/python -m pip install -e .
```

## Add the server to OpenCode's MCP servers

In your OpenCode MCP configuration (`~/.config/opencode/opencode.json`
`"mcp"` section) register DepLoom as a local stdio server. The command uses
the repository's venv python so the `mcp` SDK and the pinned Core are found:

```jsonc
{
  "mcp": {
    "deploom": {
      "type": "local",
      "command": [
        "C:\\Users\\<you>\\Desktop\\DepLoom ecosystem\\mcp\\.venv\\Scripts\\python.exe",
        "-m", "deploom_mcp"
      ],
      "environment": {
        "DEPLOOM_MCP_STATE_DIR": "C:\\Users\\<you>\\.deploom-mcp"
      },
      "enabled": true
    }
  }
}
```

On Linux/macOS use the venv `bin/python` and a POSIX state dir. The MCP server
speaks stdio, so OpenCode launches it as a subprocess; the tool `get_upgrade_plan`
then arrives in the agent's tool list automatically.

## Using the tool

Ask the agent, e.g.: "Propose an upgrade plan for `C:\proj` at yellow level."
The agent calls `get_upgrade_plan`, receives the compact protocol, reads the
artifact paths with its own file tools, and (per the honesty contract) confirms
with you before acting on anything flagged `needsReview`. To skip a package,
pass `exclude: ["<pkg>", ...]`; the boundary forwards it to the planner as
`--exclude-dependency <pkg>|reason`. If the reply contains a `truncation` block,
only up to 100 dependency rows were inlined — open the full blueprint from
`artifacts` for the rest.

