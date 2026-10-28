#!/usr/bin/env python3
"""Install the canonical deploom-upgrade skill into a coding client's skill dir.

Python standard library only (Python is already required by DepLoom MCP).
Copies the hash-bound canonical ``skills/deploom-upgrade`` tree, including
references, examples and the evidence validator, into a client skill directory:

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
* any differing or missing canonical file requires ``--force`` for an existing
  install; this includes stale references when SKILL.md is unchanged;
* foreign extra files are preserved and reported, never silently deleted;
* never modifies the original checkout, the pinned Core, or any global client
  config; never stores secrets; never enables permissions;
* canonical integrity and installed byte-identity are checked; the receipt does
  not prove that a running client loaded these files;
* ``--print-mcp-config`` prints (does not write) the client MCP snippet so MCP
  configuration stays a separate, explicit step.

User-scope base can be redirected for tests/CI without touching ``HOME`` with
``--config-home`` (or env ``DEPLOOM_SKILL_HOME``) — it replaces the home
directory, so the client's normal relative path (``.config/opencode/skills``,
``.claude/skills``, ``.agents/skills``) is still appended.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Also works when the wheel entry point invokes the bundled installer.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from skill_integrity import MANIFEST, RECEIPT, canonical_files, verify_contract  # noqa: E402

NAME = "deploom-upgrade"

CLIENT_PROJECT_SUBDIR = {
    "opencode": Path(".opencode") / "skills",
    "claude-code": Path(".claude") / "skills",
    "codex": Path(".agents") / "skills",
}


def _canonical_files(root: Path) -> list[Path]:
    return canonical_files(root, include_manifest=True)


def default_user_base(client: str, config_home: str | None) -> Path:
    explicit = config_home or os.environ.get("DEPLOOM_SKILL_HOME")
    base = Path(explicit) if explicit else Path.home()
    if client == "opencode":
        return base / ".config" / "opencode" / "skills"
    if client == "claude-code":
        return base / ".claude" / "skills"
    if client == "codex":
        return base / ".agents" / "skills"
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
    if target.exists() and any(target.iterdir()):
        return ("conflict" if verify(canonical_root, target, files) else "installed"), files
    return "fresh", files


def install(canonical_root: Path, target: Path, files: list[Path], force: bool, dry_run: bool, status: str) -> None:
    if dry_run:
        for rel in sorted(f.relative_to(canonical_root) for f in files):
            print(f"[dry-run] copy {rel} -> {target / rel}")
        print(f"[dry-run] target: {target}")
        return
    target.mkdir(parents=True, exist_ok=True)
    for src in files:
        rel = src.relative_to(canonical_root)
        dst = target / rel
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            continue
        if dst.exists() and not force:
            raise ValueError(f"refusing to overwrite {dst} without --force")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"copy {rel} -> {dst}")


def verify(canonical_root: Path, target: Path, files: list[Path]) -> list[str]:
    missing = [str(f.relative_to(canonical_root)) for f in files if not (target / f.relative_to(canonical_root)).is_file()]
    mismatched: list[str] = []
    for f in files:
        rel = f.relative_to(canonical_root)
        dst = target / rel
        if dst.is_file() and dst.read_bytes() != f.read_bytes():
            mismatched.append(str(rel))
    expected = {f.relative_to(canonical_root).as_posix() for f in files}
    actual = {p.relative_to(target).as_posix() for p in canonical_files(target, include_manifest=True)} if target.is_dir() else set()
    extra = [f"unexpected file preserved: {name}" for name in sorted(actual - expected)]
    return missing + mismatched + extra


def check_target_safety(canonical_root: Path, target: Path) -> None:
    # Refuse links in parent directories too: copying through one would mutate
    # a different location even if the final filename is a regular file.
    for path in (target, *target.parents):
        if path.is_symlink():
            raise ValueError(f"target path contains a symbolic link: {path}")
    source_resolved, target_resolved = canonical_root.resolve(), target.resolve()
    if (source_resolved == target_resolved or source_resolved in target_resolved.parents
            or target_resolved in source_resolved.parents):
        raise ValueError("source and target must not overlap")
    if target.exists():
        canonical_files(target, include_manifest=True)  # also rejects nested links
        if (target / RECEIPT).is_symlink():
            raise ValueError("installation receipt must not be a symbolic link")


def source_provenance(canonical_root: Path) -> dict:
    source = {"path": str(canonical_root.resolve()), "kind": "content-manifest",
              "gitRevision": None, "gitDirty": None}
    try:
        def git(*args: str) -> str:
            return subprocess.run(["git", "-C", str(canonical_root), *args], check=True,
                                  capture_output=True, text=True, timeout=5).stdout.strip()
        top = Path(git("rev-parse", "--show-toplevel"))
        source.update(kind="git-checkout", gitRevision=git("rev-parse", "HEAD"),
                      gitDirty=bool(git("status", "--porcelain", "--", str(canonical_root.resolve().relative_to(top.resolve())))))
    except (OSError, ValueError, subprocess.SubprocessError):
        # A wheel needs no Git executable. Its hash manifest still identifies
        # every canonical byte, while any release provenance remains separate.
        pass
    return source


def write_receipt(canonical_root: Path, target: Path) -> None:
    contract = json.loads((canonical_root / MANIFEST).read_text(encoding="utf-8"))
    receipt = {"schemaVersion": 1, "name": contract["name"],
               "contractVersion": contract["contractVersion"],
               "bundleSha256": contract["bundleSha256"], "files": contract["files"],
               "source": source_provenance(canonical_root), "target": str(target.resolve()),
               "installedVerification": "BYTE_IDENTICAL", "loadedVerification": "NOT_OBSERVED"}
    path = target / RECEIPT
    data = json.dumps(receipt, indent=2, ensure_ascii=False) + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8") != data:
        path.write_text(data, encoding="utf-8", newline="\n")


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
    parser.add_argument("--force", action="store_true", help="repair/replace differing canonical files; preserve foreign extras")
    parser.add_argument("--verify", action="store_true", help="print explicit byte-identity verification (always checked)")
    parser.add_argument("--print-mcp-config", action="store_true", help="print the client MCP config snippet (no writes)")
    args = parser.parse_args(argv)

    if args.print_mcp_config:
        if not args.client:
            raise SystemExit("ERROR: --client is required with --print-mcp-config")
        print_mcp_config(args.client)
        return 0

    if not args.client and not args.target:
        raise SystemExit("ERROR: --client (with --scope) or --target is required\n"
                         "usage: python scripts/install_skill.py --client opencode --scope project --project /path\n"
                         "       python scripts/install_skill.py --scope project --target /path/to/skill-dir")
    if args.client and not args.scope and not args.target:
        raise SystemExit("ERROR: --scope (project|user) is required when --client is given")
    if args.scope == "project" and not args.target and not args.project:
        raise SystemExit("ERROR: --project is required with --scope project (or pass --target)")

    canonical_root = Path(args.from_dir) if args.from_dir else Path(__file__).resolve().parents[1] / "skills" / args.name
    if not (canonical_root / "SKILL.md").is_file():
        raise SystemExit(f"ERROR: canonical skill not found at {canonical_root}")

    source_problems = verify_contract(canonical_root)
    if source_problems:
        return _verify_fail(source_problems, canonical_root, canonical_root)
    target = compute_target(args).absolute()
    try:
        check_target_safety(canonical_root, target)
        status, files = plan_install(canonical_root, target)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"status: {status}")
    print(f"source: {canonical_root}")
    print(f"target: {target}")

    if args.dry_run:
        if status == "conflict" and not args.force:
            print("[dry-run] an actual repair would require --force")
        install(canonical_root, target, files, force=args.force, dry_run=True, status=status)
        print("[dry-run] source manifest verified; no installation or loaded-client claim")
        return 0
    if status == "conflict" and not args.force:
        print(f"existing skill tree at {target} differs from canonical; "
              "overwrite with --force or use --dry-run first", file=sys.stderr)
        return 2
    try:
        if status == "installed":
            print("already installed with identical canonical content (no content changes)")
        else:
            install(canonical_root, target, files, force=args.force, dry_run=False, status=status)
        problems = verify(canonical_root, target, files)
        if problems:
            return _verify_fail(problems, target, canonical_root)
        write_receipt(canonical_root, target)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.verify:
        print(f"verify: ok ({len(files)} files byte-identical)")
    print("receipt: installed bytes recorded; loaded client content NOT_OBSERVED")
    return 0


def _verify_fail(problems: list[str], target: Path, canonical_root: Path) -> int:
    print(f"verify: FAILED for {target}", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
