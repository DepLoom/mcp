"""Build a self-contained, hash-bound headless distribution.

Only the explicit release inventory is copied. Local state, Desktop, tests,
Git history and untracked files in the Core checkout cannot enter the wheel.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def inventory_digest(files: dict[str, str]) -> str:
    return digest(json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def checked_source(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"unsafe release resource: {relative}")
    if not path.is_file():
        raise ValueError(f"missing release resource: {relative}")
    return path


def validate_release(root: Path) -> dict:
    manifest = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
    core = root / "upstream" / "deploom"
    files = manifest["core"]["files"]
    if not files or "dependency_live_roadmap_generator.py" not in files:
        raise ValueError("release manifest has no planning Core")
    if inventory_digest(files) != manifest["core"]["contentSha256"]:
        raise ValueError("Core inventory digest mismatch")
    for relative, expected in files.items():
        if digest(checked_source(core, relative).read_bytes()) != expected:
            raise ValueError(f"Core release resource changed: {relative}")
    for name, relative in (("schemaSha256", "contracts/upgrade-blueprint.schema.json"),
                           ("skillManifestSha256", "skills/deploom-upgrade/contract.json")):
        if digest(checked_source(root, relative).read_bytes()) != manifest[name]:
            raise ValueError(f"release resource changed: {relative}; regenerate release-manifest.json")
    skill = root / "skills" / "deploom-upgrade"
    contract = json.loads((skill / "contract.json").read_text(encoding="utf-8"))
    if inventory_digest(contract["files"]) != contract["bundleSha256"]:
        raise ValueError("Skill inventory digest mismatch")
    for relative, expected in contract["files"].items():
        if digest(checked_source(skill, relative).read_bytes()) != expected:
            raise ValueError(f"Skill release resource changed: {relative}")
    import tomllib
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["version"] != manifest["mcpVersion"]:
        raise ValueError("MCP version differs from release manifest")
    return manifest


def source_inventory(root: Path, files: list[str]) -> list[str]:
    """Explicit Core/Skill inventory for an sdist, including rebuilt sdists."""
    manifest = validate_release(root)
    selected = [name for name in files if not name.replace("\\", "/").startswith(("upstream/deploom/", "skills/deploom-upgrade/"))]
    selected.extend(f"upstream/deploom/{name}" for name in manifest["core"]["files"])
    skill = json.loads((root / "skills/deploom-upgrade/contract.json").read_text(encoding="utf-8"))
    selected.extend(f"skills/deploom-upgrade/{name}" for name in ["contract.json", *skill["files"]])
    return sorted(set(selected))


def copy_resources(root: Path, package: Path) -> None:
    manifest = validate_release(root)
    package = package.resolve()
    # These are build outputs owned by this hook. Never reuse stale files from
    # an earlier build, and never clean the canonical source resources.
    if any(package.is_relative_to((root / name).resolve()) for name in ("src", "skills", "upstream")):
        raise ValueError("resource destination must be a build directory, not canonical source")
    for name in ("_core", "resources"):
        target = package / name
        if target.is_symlink():
            raise ValueError(f"build resource directory is a symlink: {name}")
        if target.exists():
            shutil.rmtree(target)
    core = root / "upstream" / "deploom"
    for relative in manifest["core"]["files"]:
        target = package / "_core" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(core / relative, target)
    resources = package / "resources"
    resources.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / "contracts" / "upgrade-blueprint.schema.json", resources / "upgrade-blueprint.schema.json")
    shutil.copyfile(root / "release-manifest.json", resources / "release-manifest.json")
    source_skill = root / "skills" / "deploom-upgrade"
    skill_contract = json.loads((source_skill / "contract.json").read_text(encoding="utf-8"))
    for relative in ["contract.json", *skill_contract["files"]]:
        target = resources / "skills" / "deploom-upgrade" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked_source(source_skill, relative), target)
    for name in ("install_skill.py", "skill_integrity.py", "check_skill.py"):
        target = resources / "scripts" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked_source(root, f"scripts/{name}"), target)
