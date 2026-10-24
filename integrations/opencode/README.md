# deploom-upgrade · OpenCode integration

How to use the **same canonical skill** (`skills/deploom-upgrade`) inside
OpenCode. This file covers installation and configuration **only** — the
workflow, safety gates and reports are defined once in the canonical `SKILL.md`
and its references. There is no separate `deploom-upgrade-opencode`.

## Verified version

Checked on **OpenCode 1.17.14** (Windows). The config shapes below match the
current (v2) schema used by that version. OpenCode rejects invalid config at
startup, so do not guess: if a field below is missing from your version, read
the published schema (`https://opencode.ai/config.json`) and the built-in
`customize-opencode` skill rather than copying v1 snippets.

## 1. Install the skill

Target paths (canonical skill copied as-is, no symlinks needed):

- project scope: `<project>/.opencode/skills/deploom-upgrade/`
- user scope: `~/.config/opencode/skills/deploom-upgrade/`

Use the installer:

```bash
python scripts/install_skill.py --client opencode --scope project --project /path/to/project
python scripts/install_skill.py --client opencode --scope user        # dry-run first
```

or copy the `skills/deploom-upgrade` folder yourself — the skill is fully
self-contained (`SKILL.md` + `references/` + `examples/`).

OpenCode's skill loader scans skill directories for `**/SKILL.md`; the folder
name must equal the `name` in frontmatter (`deploom-upgrade`). Skill
discovery/lifecycle:

- after installing, the skill appears in the project's skill list and its
  `description` is surfaced to the agent automatically;
- restart OpenCode after adding the skill or editing MCP config (config is
  loaded at startup, not hot-reloaded);
- `references/` and `examples/` are on disk next to `SKILL.md`; the agent opens
  them on demand (`references/…`), so the heavy detail is not auto-loaded.

## 2. Connect the MCP server

DepLoom MCP is a local stdio server; register it under `mcp` in the project's
`opencode.json` (or the global `~/.config/opencode/opencode.json`). The
`command` must point at the Python environment where `deploom_mcp` is installed
(usually the repo venv):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "deploom": {
      "type": "local",
      "command": [
        "C:/path/to/mcp/.venv/Scripts/python.exe",
        "-m", "deploom_mcp"
      ],
      "environment": {
        "DEPLOOM_MCP_STATE_DIR": "C:/Users/<you>/.deploom-mcp"
      },
      "enabled": true
    }
  }
}
```

- `type: "local"` is required; `command` is an **array** of strings.
- `DEPLOOM_MCP_STATE_DIR` keeps run artifacts outside the analysed project.
- After saving, restart OpenCode. The single tool `get_upgrade_plan` then
  appears in the agent's tool list.
- The repository never edits your global config; the block above is meant to be
  copied into your own config.

## 3. Permissions the skill relies on

The skill only uses the client's standard tools — no special runtime:

- `read` / `edit`/`write` — reading the project, editing the worktree;
- `bash` — `git`, `node`, `npm`/`yarn`/`pnpm`, the project's check commands;
- `mcp` — calling `get_upgrade_plan`.
- `external_directory` — the worktree path is normally within the project repo;
  if the client asks about an out-of-tree worktree, permit it explicitly
  (everything the skill writes lives in the worktree, never the original
  checkout).

Nothing in the skill enables or requires dangerous permissions. If OpenCode
denies an edit/shell permission, the skill reports `BLOCKED` and asks — it
never bypasses the permission model.

## 4. Running the pilot / smoke

Headless end-to-end runs are supported:

```bash
opencode run --dir <project> -m <provider/model> --auto "Upgrade dependencies to yellow, don't touch react, verify build and tests"
```

`--auto` auto-approves non-denied permissions and is dangerous in general; use
it only in a disposable fixture. For interactive use, drop `--auto` and approve
each worktree/edit action as usual. Plan-only flows need no write permissions at
all.

## 5. What the skill will not do in this client

- It will not create commits/PRs or merge — it stops at the evidence report and
  shows the diff.
- It will not add second MCP tools or invent a resolver — exactly one tool
  (`get_upgrade_plan`) is used.
