"""Locate versioned resources in either a checkout or an installed wheel.

The wheel contains the pinned Python Core and contracts. Running it never
requires Electron, a sibling repository, or a Git executable.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _package_root() -> Path:
    return Path(__file__).resolve().parent


def _checkout_root() -> Path:
    return _package_root().parents[1]


def resolve_core_dir() -> Path:
    bundled = _package_root() / "_core"
    if (bundled / "dependency_live_roadmap_generator.py").is_file():
        return bundled
    return _checkout_root() / "upstream" / "deploom"


def blueprint_schema_path() -> Path:
    bundled = _package_root() / "resources" / "upgrade-blueprint.schema.json"
    if bundled.is_file():
        return bundled
    return _checkout_root() / "contracts" / bundled.name


def release_manifest_path() -> Path:
    bundled = _package_root() / "resources" / "release-manifest.json"
    return bundled if bundled.is_file() else _checkout_root() / bundled.name


def skill_root() -> Path:
    bundled = _package_root() / "resources" / "skills" / "deploom-upgrade"
    if (bundled / "SKILL.md").is_file():
        return bundled
    return _checkout_root() / "skills" / "deploom-upgrade"


def install_skill_main() -> int:
    bundled = _package_root() / "resources" / "scripts" / "install_skill.py"
    script = bundled if bundled.is_file() else _checkout_root() / "scripts" / bundled.name
    return subprocess.call([sys.executable, str(script), *sys.argv[1:]])
