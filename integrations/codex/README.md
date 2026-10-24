# deploom-upgrade · OpenAI Codex integration

How to use the **same canonical skill** (`skills/deploom-upgrade`) inside the
OpenAI Codex CLI. This file covers installation and configuration **only**; the
workflow and safety gates live once in `SKILL.md` + `references/*`. There is no
`deploom-upgrade-codex` variant.

## Verified version

**codex-cli 0.144.4** installed on this host (Windows), authenticated with a
ChatGPT account. Use `codex --version` and `codex exec --help` to confirm your
local version; flags and paths below were checked against this one.

## 1. Install the skill

Codex discovers skills from the standard Agent Skills directories. Target paths
(canonical skill copied as-is, no symlinks):

- project scope: `<project>/.agents/skills/deploom-upgrade/SKILL.md`
- user scope: `~/.agents/skills/deploom-upgrade/SKILL.md`

```bash
python scripts/install_skill.py --client codex --scope project --project /path/to/project
python scripts/install_skill.py --client codex --scope user            # dry-run first
```

Discovery: with the `SKILL.md` in place, codex loads the skill (folder name must
equal the frontmatter `name`). Re-run `codex` (or start a new session) after
installing. Invoke explicitly in a prompt, e.g. "Use the deploom-upgrade skill,
plan only, yellow, exclude react".

## 2. Connect the MCP server (STDIO)

Add DepLoom as a local stdio MCP server:

```bash
codex mcp add deploom -- python -m deploom_mcp
```

Check `codex mcp --help` for your version's scoping (`--scope user|project` or
`--global`) so the server lands where you intend. Ensure the interpreter on
PATH is the environment where `deploom_mcp` is installed; otherwise use an
absolute interpreter:

```bash
codex mcp add deploom -- "C:/path/to/mcp/.venv/Scripts/python.exe" -m deploom_mcp
```

### Alternative: project `config.toml`

If you prefer a checked-in config, add to `~/.codex/config.toml` or a project
config (keep the project path in the trusted set):

```toml
[mcp_servers.deploom]
command = "C:/path/to/mcp/.venv/Scripts/python.exe"
args = ["-m", "deploom_mcp"]
```

Trusted projects and sandbox: codex only grants file/shell access to trusted
project paths and within its sandbox permissions. If codex denies file access,
execution of install/build/test, or worktree creation, the skill reports
`BLOCKED` — it never disables the sandbox or elevates permissions on its own.
For a real migration you must decide to extend trust; the skill will ask, not
silently bypass.

## 3. Permissions & sandbox

The skill uses only standard tools (read/edit/bash/mcp). It requires:

- file access to the **worktree** (and read access to the original checkout);
- shell access to `git`, `node`, `npm`/`yarn`/`pnpm`, and the project's check
  commands;
- the MCP tool `get_upgrade_plan`.

If any permission is denied → `BLOCKED` with the exact missing capability. No
global Codex configuration is modified by the skill or installer.

## 4. No big `AGENTS.md`

Do not add a large `AGENTS.md` repeating the skill. If a standing project note
is needed, keep it to a short pointer to the skill; all logic is in the
canonical skill.

## 5. Smoke checklist (headless)

```bash
codex exec --skip-git-repo-check --sandbox read-only \
   "Use the deploom-upgrade skill: plan-only for <project>, yellow, exclude react. Report the blueprint; do not modify anything."
```

- [ ] skill is discovered (agent confirms it is using `deploom-upgrade`);
- [ ] `get_upgrade_plan` is visible and returns a real blueprint;
- [ ] plan-only: original checkout unchanged;
- [ ] for `EXECUTE`: worktree created, install/build/test run under sandbox,
      denial handled as `BLOCKED`.
