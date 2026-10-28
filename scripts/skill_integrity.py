"""Canonical Skill inventory, shared by release checks and the installer.

Standard library only. The manifest hashes content, not timestamps or absolute
paths. It identifies the distributed files; it cannot attest a client's loaded
context. The manifest itself and the installation receipt are not self-hashed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

MANIFEST = "contract.json"
RECEIPT = ".deploom-skill-install.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inventory_digest(files: dict[str, str]) -> str:
    return sha256(json.dumps(files, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8"))


def canonical_files(root: Path, *, include_manifest: bool = False) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Skill source must be a real directory: {root}")
    result = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ValueError(f"Skill tree contains a symbolic link: {relative}")
        if relative.as_posix() == RECEIPT:
            continue
        if not include_manifest and relative.as_posix() == MANIFEST:
            continue
        if path.is_file():
            result.append(path)
    return result


def expected_contract(root: Path) -> dict:
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    name = re.search(r"^name: ([a-z0-9-]+)\s*$", skill, re.MULTILINE)
    version = re.search(r"\*\*Contract version: (\d+\.\d+\.\d+)\.\*\*", skill)
    if not name or not version:
        raise ValueError("SKILL.md needs a portable name and an explicit Contract version marker")
    files = {p.relative_to(root).as_posix(): sha256(p.read_bytes())
             for p in canonical_files(root)}
    return {
        "schemaVersion": 1,
        "name": name.group(1),
        "contractVersion": version.group(1),
        "files": files,
        "bundleSha256": inventory_digest(files),
    }


def verify_contract(root: Path) -> list[str]:
    """Compare every shipped file, including references, with the manifest."""
    try:
        actual = expected_contract(root)
        stored = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [str(exc)]
    if not isinstance(stored, dict):
        return ["contract.json must contain an object"]
    problems = []
    for key in ("schemaVersion", "name", "contractVersion", "bundleSha256"):
        if stored.get(key) != actual[key]:
            problems.append(f"contract.json {key} differs from the canonical content")
    files = stored.get("files")
    if not isinstance(files, dict):
        return [*problems, "contract.json files must be an object"]
    for relative in sorted(set(files) | set(actual["files"])):
        if relative not in actual["files"]:
            problems.append(f"manifest lists a missing file: {relative}")
        elif relative not in files:
            problems.append(f"unlisted canonical file: {relative}")
        elif files[relative] != actual["files"][relative]:
            problems.append(f"content hash mismatch: {relative}")
    return problems


def write_contract(root: Path) -> dict:
    contract = expected_contract(root)
    (root / MANIFEST).write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8", newline="\n")
    return contract
