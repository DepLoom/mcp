#!/usr/bin/env python3
"""Bind the public package, schema, Skill and exact clean Core gitlink.

Run after updating the submodule and canonical Skill contract. --check is a
read-only release gate. No network calls or version bumps are performed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_support import digest, inventory_digest  # noqa: E402


def git(directory: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()


def release_manifest(root: Path) -> dict:
    core = root / "upstream" / "deploom"
    pin_entry = git(root, "ls-files", "--stage", "upstream/deploom").split()
    if len(pin_entry) < 3 or pin_entry[0] != "160000":
        raise ValueError("upstream/deploom must be a staged, pinned Git submodule")
    revision = git(core, "rev-parse", "HEAD")
    if revision != pin_entry[1]:
        raise ValueError("Core HEAD differs from the staged gitlink; stage the intended pin first")
    if git(core, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Core has tracked changes; commit and validate the intended source before binding it")
    files: dict[str, str] = {}
    for relative in git(core, "ls-files").splitlines():
        path = Path(relative)
        selected = ((len(path.parts) == 1 and relative != "setup.py" and (path.suffix == ".py" or relative.endswith(".schema.json") or relative == "VERSION"))
                    or (path.parts[0] == "document_locales" and path.suffix == ".json"))
        if selected:
            source = core / relative
            if source.is_symlink():
                raise ValueError(f"Core resource is a symlink: {relative}")
            files[relative] = digest(source.read_bytes())
    skill_path = root / "skills" / "deploom-upgrade" / "contract.json"
    skill = json.loads(skill_path.read_text(encoding="utf-8"))
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return {
        "schemaVersion": 1,
        "mcpVersion": project["version"],
        "blueprintSchemaVersion": 2,
        "core": {"repository": "https://github.com/DepLoom/core", "revision": revision,
                 "version": (core / "VERSION").read_text(encoding="utf-8").strip(),
                 "contentSha256": inventory_digest(files), "files": files},
        "schemaSha256": digest((root / "contracts" / "upgrade-blueprint.schema.json").read_bytes()),
        "skillManifestSha256": digest(skill_path.read_bytes()),
        "skill": {"contractVersion": skill["contractVersion"], "bundleSha256": skill["bundleSha256"]},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        expected = release_manifest(ROOT)
        path = ROOT / "release-manifest.json"
        if args.check:
            if not path.is_file() or json.loads(path.read_text(encoding="utf-8")) != expected:
                raise ValueError("release-manifest.json is stale; run scripts/update_release_manifest.py")
            print("release manifest: matched")
        else:
            path.write_text(json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print("release-manifest.json updated")
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"release manifest: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
