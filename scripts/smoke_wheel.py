#!/usr/bin/env python3
"""Check an installed wheel outside the source checkout, without a registry.

Runtime dependencies must already be installed in the invoking interpreter.
The isolated venv shares only those dependencies; the project itself is
installed from the wheel. All fixtures and outputs live in a temporary dir.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import sysconfig
import tempfile
import venv
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, nargs="?", help="wheel file; defaults to the single wheel in dist/")
    args = parser.parse_args()
    if args.wheel is None:
        candidates = sorted(Path("dist").glob("*.whl"))
        if len(candidates) != 1:
            parser.error("pass one wheel explicitly, or leave exactly one wheel in dist/")
        args.wheel = candidates[0]
    wheel = args.wheel.resolve()
    if not wheel.is_file():
        parser.error("wheel does not exist")
    with tempfile.TemporaryDirectory(prefix="deploom-wheel-") as tmp:
        root = Path(tmp).resolve()
        env_path = root / "venv"
        venv.EnvBuilder(with_pip=True).create(env_path)
        python = env_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH" and not k.startswith("DEPLOOM_")}
        env["DEPLOOM_MCP_STATE_DIR"] = str(root / "state")
        # A nested venv's system_site_packages flag refers to the base Python,
        # not necessarily the calling venv that holds our runtime dependencies.
        # Add only explicit installed-library directories, never source paths.
        site = subprocess.check_output([str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                                       cwd=root, env=env, text=True).strip()
        dependency_paths = sorted({sysconfig.get_path("purelib"), sysconfig.get_path("platlib")})
        (Path(site) / "smoke_runtime_dependencies.pth").write_text("\n".join(dependency_paths) + "\n", encoding="utf-8")
        subprocess.run([str(python), "-m", "pip", "install", "--no-deps", "--no-index", "--ignore-installed", str(wheel)],
                       cwd=root, env=env, check=True)
        # No fake Core override: the real bundled generator must import and run
        # its CLI in a directory that has no source checkout or Git metadata.
        code = '''
import json, pathlib, subprocess, sys
import deploom_mcp
from deploom_mcp.distribution import resolve_core_dir, blueprint_schema_path, release_manifest_path, skill_root
from deploom_mcp.blueprint import BlueprintOptions, generate_upgrade_blueprint
root=pathlib.Path.cwd().resolve()
assert pathlib.Path(deploom_mcp.__file__).resolve().is_relative_to(root / "venv"), deploom_mcp.__file__
core=resolve_core_dir()
assert core.name == "_core" and core.is_relative_to(root / "venv"), core
assert blueprint_schema_path().is_file() and release_manifest_path().is_file()
assert (skill_root()/"contract.json").is_file()
# Core repin regression: the installed wheel must carry the newly pinned
# diagnostics module. Import it from the wheel's private _core directory,
# never from the source checkout or a globally installed Core.
release=json.loads(release_manifest_path().read_text(encoding="utf-8"))
import hashlib
registry_module=core/"registry_candidate_diagnostics.py"
assert registry_module.is_file() and "registry_candidate_diagnostics.py" in release["core"]["files"]
assert hashlib.sha256(registry_module.read_bytes()).hexdigest()==release["core"]["files"]["registry_candidate_diagnostics.py"]
sys.path.insert(0, str(core))
import registry_candidate_diagnostics as registry_evidence
assert registry_evidence.no_target_diagnostic_code("no-target", {"allUpgradeCandidatesRejected": True}) is None
complete={"examined":2, "accepted":0, "rejected":2, "upgradeCandidates":1,
          "acceptedUpgradeCandidates":0, "rejectedUpgradeCandidates":1,
          "allUpgradeCandidatesRejected":True, "rejectionCounts":{"BAD_REGISTRY_PATH":2}}
assert registry_evidence.no_target_diagnostic_code("no-target", complete)=="NO_INSTALLABLE_ARTIFACT"
assert registry_evidence.safe_registry_artifact_url("https://nexus.invalid/npm", "https://nexus.invalid/npm/a.tgz")
assert not registry_evidence.safe_registry_artifact_url("https://nexus.invalid/npm", "https://nexus.invalid/npm/../private/a.tgz")
assert not registry_evidence.safe_registry_artifact_url("https://nexus.invalid/npm", "https://user:password@nexus.invalid/npm/a.tgz")
assert not registry_evidence.safe_registry_artifact_url("https://nexus.invalid/npm", "https://nexus.invalid:bad/npm/a.tgz")
assert "lockfile_consistency.py" in release["core"]["files"]
assert hashlib.sha256((core/"lockfile_consistency.py").read_bytes()).hexdigest()==release["core"]["files"]["lockfile_consistency.py"]
import lockfile_consistency as lock_policy
assert lock_policy.registry_artifact_url_allowed("https://nexus.invalid/npm", "git+ssh://git@github.com/org/repo.git")
assert lock_policy.registry_artifact_url_allowed("https://nexus.invalid/npm", "file:///tmp/local-package")
assert not lock_policy.registry_artifact_url_allowed("https://nexus.invalid/npm", "random://evil.invalid/archive")
assert not (core/"desktop").exists() and not (core/".git").exists()
subprocess.run([sys.executable, str(core/"dependency_live_roadmap_generator.py"), "--help"], check=True, capture_output=True)
project=root/"project"
project.mkdir()
manifest=project/"package.json"
original='{"name":"empty-headless-smoke","version":"1.0.0","private":true,"dependencies":{}}'
manifest.write_text(original, encoding="utf-8")
result=generate_upgrade_blueprint(str(project), options=BlueprintOptions(output_dir=str(root/"output"), draft_deadline_seconds=5.0))
assert result.ok, (result.error, result.stderr[-1500:])
assert result.blueprint["status"]["authority"] == "PLANNING_ONLY"
assert manifest.read_text(encoding="utf-8") == original
print("installed wheel: bundled Core, schema, Skill and read-only empty-project planning PASS")
'''
        subprocess.run([str(python), "-I", "-c", code], cwd=root, env=env, check=True)
        install_code = "from deploom_mcp.distribution import install_skill_main; raise SystemExit(install_skill_main())"
        subprocess.run([str(python), "-I", "-c", install_code, "--target", str(root / "installed-skill"), "--verify"],
                       cwd=root, env=env, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
