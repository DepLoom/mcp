#!/usr/bin/env python3
"""Install the canonical deploom-upgrade skill into a coding client's skill dir.

Python standard library only (Python is already required by DepLoom MCP).
Copies the canonical ``skills/deploom-upgrade`` tree (``SKILL.md`` plus
``references/`` and ``examples/``) into the target directory for one client:

* ``--client opencode``    -> project: ``<proj>/.opencode/skills/<name>``
                              user:    ``~/.config/opencode/skills/<name>``
* ``--client claude-code`` -> project: ``<proj>/.claude/skills/<name>``
                              user:    ``~/.claude/skills/<name>``
* ``--client codex``       -> project: ``<proj>/.agents/skills/<name>``
                              user:    ``~/.agents/skills/<name>``

Guarantees:

* Windows and Linux, no symbolic links (nothing that needs extra privileges);
* ``--dry-run`` prints the plan and writes nothing;
* re-runs are safe and idempotent (byte-identical target is a no-op);
* never overwrites a foreign skill: a differing existing ``SKILL.md`` aborts
  unless ``--force`` is given; only canonical-known files are ever written;
* never modifies the original checkout, the pinned Core, or any global client
  config; never stores secrets; never enables permissions;
* ``--verify`` proves the installed tree is byte-identical to the canonical one;
* ``--print-mcp-config`` prints (does not write) the client MCP snippet so MCP
  configuration stays a separate, explicit step.

User-scope base can be redirected for tests/CI without touching ``HOME`` with
``--config-home`` (or env ``DEPLOOM_SKILL_HOME``).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

NAME = "deploom-upgrade"
CANONICAL_REL = ("SKILL.md", "references", "examples")

CLIENT_PROJECT_SUBDIR = {
    "opencode": Path(".opencode") / "skills",
    "claude-code": Path(".claude") / "skills",
    "codex": Path(".agents") / "skills",
}


def _canonical_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for rel in CANONICAL_REL:
        entry = root / rel
        if entry.is_dir():
            for child in sorted(p for p in entry.rglob("*") if p.is_file()):
                paths.append(child)
        elif entry.is_file():
            paths.append(entry)
    return paths


def default_user_base(client: str, config_home: str | None) -> Path:
    override = config_home or os.environ.get("DEPLOOM_SKILL_HOME")
    if override:
        return Path(override)
    home = Path.home()
    if client == "opencode":
        return home / ".config" / "opencode" / "skills"
    if client == "claude-code":
        return home / ".claude" / "skills"
    if client == "codex":
        return home / ".agents" / "skills"
    raise ValueError(client)


def compute_target(args: argparse.Namespace) -> Path:
    if args.target:
        return Path(args.target)
    if args.scope == "user":
        return default_user_base(args.client, args.config_home) / args.name
    if not args.project:
        raise SystemExit("ERROR: --project is required with --scope project")
    return Path(args.project) / CLIENT_PROJECT_SUBDIR[args.client] / args.name


def plan_install(canonical_root: Path, target: Path) -> tuple[str, list[Path]]:
    files = _canonical_files(canonical_root)
    target_skill = target / "SKILL.md"
    if target_skill.exists():
        if target_skill.read_bytes() == (canonical_root / "SKILL.md").read_bytes():
            return "installed", files
        return "conflict", files
    if target.exists() and any(target.rglob("*")):
        return "partial-conflict", files
    return "fresh", files


def install(canonical_root: Path, target: Path, files: list[Path], force: bool, dry_run: bool, status: str) -> None:
    overwrite = force and status != "installed"
    if dry_run:
        action = "copy" if force else "WRITE-PROTECTED-copy"
        for rel in sorted(f.relative_to(canonical_root) for f in files):
            print(f"[dry-run] {action} {rel} -> {target / rel}")
        print(f"[dry-run] target: {target}")
        return
    target.mkdir(parents=True, exist_ok=True)
    for src in files:
        rel = src.relative_to(canonical_root)
        dst = target / rel
        if dst.exists() and not overwrite:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"copy {rel} -> {dst}")


def verify(canonical_root: Path, target: Path, files: list[Path]) -> list[str]:
    missing = [str(f.relative_to(canonical_root)) for f in files if not (target / f.relative_to(canonical_root)).exists()]
    mismatched: list[str] = []
    for f in files:
        rel = f.relative_to(canonical_root)
        dst = target / rel
        if dst.exists() and dst.read_bytes() != f.read_bytes():
            mismatched.append(str(rel))
    return missing + mismatched


def print_mcp_config(client: str) -> None:
    if client == "opencode":
        print(json_dumps_indent({
            "mcp": {
                "deploom": {
                    "type": "local",
                    "command": ["<abs path to python of the deploom-mcp venv>", "-m", "deploom_mcp"],
                    "environment": {"DEPLOOM_MCP_STATE_DIR": "<abs path, outside any project>"},
                    "enabled": True,
                }
            }
        }))
    elif client == "claude-code":
        print("claude mcp add --scope project --transport stdio deploom -- \"<abs python>\" -m deploom_mcp")
    elif client == "codex":
        print("codex mcp add deploom -- \"<abs python>\" -m deploom_mcp")
        print("# or in config.toml: [mcp_servers.deploom] command = \"<abs python>\" args = [\"-m\", \"deploom_mcp\"]")


def json_dumps_indent(obj) -> str:
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install_skill.py",
        description="Install the canonical deploom-upgrade skill into an OpenCode/Claude Code/Codex skill directory.",
    )
    parser.add_argument("--client", choices=sorted(CLIENT_PROJECT_SUBDIR), help="target coding client")
    parser.add_argument("--scope", choices=["project", "user"], help="install scope")
    parser.add_argument("--project", help="project root for --scope project")
    parser.add_argument("--target", help="explicit target skill directory (overrides --client/--scope-derived path)")
    parser.add_argument("--name", default=NAME, help=f"skill id (default: {NAME})")
    parser.add_argument("--from-dir", dest="from_dir", help="canonical skill source dir (default: this repo skills/<name>)")
    parser.add_argument("--config-home", help="override user-scope base dir (also env DEPLOOM_SKILL_HOME)")
    parser.add_argument("--dry-run", action="store_true", help="print planned actions, write nothing")
    parser.add_argument("--force", action="store_true", help="overwrite an existing non-identical SKILL.md")
    parser.add_argument("--verify", action="store_true", help="verify byte-identity of the install after copy")
    parser.add_argument("--print-mcp-config", action="store_true", help="print the client MCP config snippet (no writes)")
    args = parser.parse_args(argv)

    if args.print_mcp_config:
        if not args.client:
            raise SystemExit("ERROR: --client is required with --print-mcp-config")
        print_mcp_config(args.client)
        return 0

    if not args.client or not args.scope:
        raise SystemExit("ERROR: --client and --scope are required (or --target with --scope project/user)\n"
                         "usage: python scripts/install_skill.py --client opencode --scope project --project /path")
    if args.scope == "project" and not args.target and not args.project:
        raise SystemExit("ERROR: --project is required with --scope project (or pass --target)")

    canonical_root = Path(args.from_dir) if args.from_dir else Path(__file__).resolve().parents[1] / "skills" / args.name
    if not (canonical_root / "SKILL.md").is_file():
        raise SystemExit(f"ERROR: canonical skill not found at {canonical_root}")

    target = compute_target(args)
    status, files = plan_install(canonical_root, target)
    print(f"status: {status}")
    print(f"source: {canonical_root}")
    print(f"target: {target}")

    if status == "installed":
        print("already installed with identical content (no changes)")
        if args.verify:
            return 0 if not verify(canonical_root, target, files) else _verify_fail(files, target)
        return 0
    if status in ("conflict", "partial-conflict") and not args.force:
        print(f"existing skill at {target / 'SKILL.md'} differs from canonical; "
              "overwrite with --force or use --dry-run first", file=sys.stderr)
        return 2

    install(canonical_root, target, files, force=args.force, dry_run=args.dry_run, status=status)

    if args.verify:
        problems = verify(canonical_root, target, files)
        if problems:
            return _verify_fail(problems, target, canonical_root)
        print(f"verify: ok ({len(files)} files byte-identical)")
    return 0


def _verify_fail(problems: list[str], target: Path, canonical_root: Path) -> int:
    print(f"verify: FAILED for {target}", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
