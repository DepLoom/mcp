# deploom-upgrade · Claude Code integration

How to use the **same canonical skill** (`skills/deploom-upgrade`) inside
Claude Code. This file covers installation and configuration **only** — the
workflow and safety contract live once in the canonical `SKILL.md` +
`references/*`. There is no `deploom-upgrade-claude`.

## Verification status

**NOT_VERIFIED on this host (2026-10-24):** the `claude` CLI is installed but its
launcher fails to start (`claude.exe` not found behind the `claude.ps1`
wrapper), so no live Claude Code session could be run for this stage. The
instructions below follow Claude Code's documented Skills/MCP support; Discovery
and MCP connectivity are **prepared but not yet exercised end-to-end** here.
Treat any positive claim about Claude Code as unverified until a real smoke is
run. The canonical skill is fully portable and needs no Claude-specific fork.

## 1. Install the skill

Target paths (canonical skill copied as-is; no symlinks):

- project scope: `<project>/.claude/skills/deploom-upgrade/SKILL.md`
- user scope: `~/.claude/skills/deploom-upgrade/SKILL.md`

```bash
python scripts/install_skill.py --client claude-code --scope project --project /path/to/project
python scripts/install_skill.py --client claude-code --scope user          # dry-run first
```

## 2. Connect the MCP server (STDIO)

Add DepLoom as a **project-scoped**, stdio server so nothing global is touched:

```bash
claude mcp add --scope project --transport stdio deploom -- python -m deploom_mcp
```

Alternatives (match your Claude Code version — verify the actual syntax with
`claude mcp --help` before running):

- Explicit interpreter so the environment containing `deploom_mcp` is used
  (e.g. the DepLoom/mcp venv):
  `claude mcp add --scope project --transport stdio deploom -- "C:/path/mcp/.venv/Scripts/python.exe" -m deploom_mcp`
- Project-level `.mcp.json` (checked into the repo is discouraged if it embeds
  machine-specific paths — prefer the user/project `claude mcp add` form):

```json
{
  "mcpServers": {
    "deploom": {
      "command": "python",
      "args": ["-m", "deploom_mcp"]
    }
  }
}
```

## 3. Permissions

The skill needs the standard Bash/Edit/Read permissions plus the MCP tool. It
requires no permission that enables anything beyond editing the worktree and
running the project's package-manager/build/test commands. If Claude Code denies
any of them, the skill reports `BLOCKED` — it never bypasses the permission
system. Nothing in the skill changes global Claude Code configuration.

## 4. NO `CLAUDE.md` duplication

Do not create a `CLAUDE.md` that repeats the skill. At most add a one-line note
referring to the skill (e.g. "Rely on the `deploom-upgrade` skill for dependency
upgrade plans; see `.claude/skills/deploom-upgrade/`"). All logic stays in the
canonical skill.

## 5. Smoke checklist (when a working CLI is available)

- [ ] `claude` starts and shows the `deploom-upgrade` skill in the project.
- [ ] Explicit trigger: "Use the deploom-upgrade skill — plan only — yellow, exclude react".
- [ ] `get_upgrade_plan` visible and returns a real blueprint (`plan-only`).
- [ ] Bash/Git/worktree permissions work for an `EXECUTE` run.
- [ ] Evidence report produced; no global config changed.
