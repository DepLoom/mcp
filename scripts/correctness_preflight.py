#!/usr/bin/env python3
"""No-agent DepLoom correctness preflight using the existing local E2E fixture.

Planning and migration are deliberately separate. The packaged MCP is called
over actual stdio and must NOT edit the input. The deterministic reference edit
is made by this harness only, in a disposable copy. Acceptance uses fixture
constants, npm ci/build/test, the installed dependency tree and a build artifact
-- never the planner's self-reported success or an agent's evidence JSON.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters, stdio_client
import jsonschema

from e2e_fixture import make_fixture
from e2e_registry import Handler, PackageStore

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "scripts" / "e2e_seed.json"
SOURCE_FILES = ("package.json", "package-lock.json", ".npmrc",
                "src/index.js", "verify.js", "build.js")
BASELINE = {"kontur-fixture-a": "1.2.0",
            "kontur-fixture-b": "2.0.0",
            "kontur-fixture-c": "3.0.0"}
FINAL = {"kontur-fixture-a": "1.9.0",
         "kontur-fixture-b": "2.1.0",
         "kontur-fixture-c": "3.0.0"}
EXCLUDED = "kontur-fixture-c"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprints(path: Path) -> dict[str, str]:
    return {name: digest(path / name) for name in SOURCE_FILES}


def isolated_env(root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if key != "PYTHONPATH" and not key.startswith("DEPLOOM_")
           and not key.lower().startswith(("npm_config_", "node_options"))}
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    env["NPM_CONFIG_CACHE"] = str(root / "npm-cache")
    env["NPM_CONFIG_AUDIT"] = "false"
    env["NPM_CONFIG_FUND"] = "false"
    env["NPM_CONFIG_IGNORE_SCRIPTS"] = "true"
    env["NPM_CONFIG_UPDATE_NOTIFIER"] = "false"
    env["CI"] = "true"
    return env


def npm_argv(*args: str) -> list[str]:
    return (["cmd", "/c", "npm"] if os.name == "nt" else ["npm"]) + list(args)


def invoke(argv: list[str], cwd: Path, env: dict[str, str],
           timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, check=False)


class LocalRegistry:
    """Reuse the existing synthetic npm store, binding only to loopback."""

    def __init__(self, *, foreign_upgrade: bool = False):
        self.foreign_upgrade = foreign_upgrade

    def __enter__(self):
        seed = json.loads(SEED.read_text(encoding="utf-8-sig"))
        parent = PackageStore

        class SeedStore(parent):
            def metadata(self, name):
                meta = super().metadata(name)
                if (meta is not None and self_foreign
                        and name == "kontur-fixture-a"):
                    meta = copy.deepcopy(meta)
                    meta["versions"]["1.9.0"]["dist"]["tarball"] = (
                        "http://127.0.0.1:9/not-an-allowed-registry/a.tgz")
                return meta

        self_foreign = self.foreign_upgrade
        store = SeedStore(seed, "http://127.0.0.1")
        handler = type("FixtureHandler", (Handler,), {"store": store})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        store.base_url = self.base
        self.store = store
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)


def copy_input(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
        "node_modules", "dist", ".git", ".DS_Store"))


def static_oracle(project: Path, registry: str) -> str | None:
    """Independent fixture contract, never derived from a DepLoom target."""
    try:
        manifest = json.loads((project / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((project / "package-lock.json").read_text(encoding="utf-8"))
        npmrc = (project / ".npmrc").read_text(encoding="utf-8")
    except (OSError, ValueError, TypeError):
        return "INVALID_INPUT"
    if npmrc != f"registry={registry}\n":
        return "UNAPPROVED_REGISTRY"
    actual = manifest.get("dependencies")
    if not isinstance(actual, dict) or actual.get(EXCLUDED) != FINAL[EXCLUDED]:
        return "EXCLUDED_DEPENDENCY_CHANGED"
    if actual != FINAL:
        return "TARGET_ASSIGNMENT_MISMATCH"
    if lock.get("lockfileVersion") != 3:
        return "LOCKFILE_FORMAT_MISMATCH"
    packages = lock.get("packages")
    if not isinstance(packages, dict) or (packages.get("") or {}).get("dependencies") != FINAL:
        return "ROOT_LOCK_MISMATCH"
    for name, version in FINAL.items():
        item = packages.get("node_modules/" + name)
        if not isinstance(item, dict) or item.get("version") != version:
            return "LOCK_VERSION_MISMATCH"
        resolved = item.get("resolved")
        if not isinstance(resolved, str) or not resolved.startswith(registry + "/"):
            return "LOCK_REGISTRY_MISMATCH"
        if not isinstance(item.get("integrity"), str) or not item["integrity"].startswith("sha512-"):
            return "LOCK_INTEGRITY_MISSING"
    return None


def final_oracle(project: Path, registry: str, env: dict[str, str]) -> str:
    """Replay a new install from final manifest+lock, not existing node_modules."""
    issue = static_oracle(project, registry)
    if issue is not None:
        return issue
    steps = (
        ("NPM_CI_FAILED", npm_argv("ci", "--ignore-scripts", "--no-audit", "--no-fund",
                                   "--registry", registry)),
        ("BUILD_FAILED", npm_argv("run", "build")),
        ("TEST_FAILED", npm_argv("test")),
        ("TREE_FAILED", npm_argv("ls", "--all", "--json")),
    )
    for code, argv in steps:
        result = invoke(argv, project, env)
        if result.returncode != 0:
            return code
        if code == "TREE_FAILED":
            try:
                tree = json.loads(result.stdout)
                actual = {name: tree["dependencies"][name]["version"] for name in FINAL}
            except (ValueError, KeyError, TypeError):
                return "TREE_INCOMPLETE"
            if actual != FINAL:
                return "TREE_VERSION_MISMATCH"
    try:
        built = json.loads((project / "dist" / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "BUILD_ARTIFACT_MISSING"
    if built.get("versions") != {"a": FINAL["kontur-fixture-a"],
                                "b": FINAL["kontur-fixture-b"],
                                "c": FINAL["kontur-fixture-c"]}:
        return "BUILD_ARTIFACT_INCORRECT"
    return "PASS"


def reference_edit(project: Path, registry: str, env: dict[str, str]) -> None:
    manifest = json.loads((project / "package.json").read_text(encoding="utf-8"))
    manifest["dependencies"] = dict(FINAL)
    (project / "package.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    result = invoke(npm_argv("install", "--package-lock-only",
                             "--ignore-scripts", "--no-audit", "--no-fund",
                             "--registry", registry), project, env)
    if result.returncode:
        raise RuntimeError("reference lockfile update failed against synthetic local registry")


def installed_provenance(python: Path, root: Path, env: dict[str, str]) -> dict:
    code = (
        "import json,pathlib,sys,hashlib,deploom_mcp;"
        "from deploom_mcp.distribution import resolve_core_dir,release_manifest_path;"
        "p=pathlib.Path(deploom_mcp.__file__).resolve();"
        "core=resolve_core_dir();"
        "m=json.loads(release_manifest_path().read_text(encoding='utf-8'));"
        "print(json.dumps({'coreRevision':m['core']['revision'],"
        "'mcpVersion':m['mcpVersion'],'inventoryCount':len(m['core']['files']),"
        "'wheelPackage':p.is_relative_to(pathlib.Path(sys.prefix).resolve()),"
        "'wheelCore':core.is_relative_to(pathlib.Path(sys.prefix).resolve()),"
        "'coreHashMatches':hashlib.sha256((core/'registry_candidate_diagnostics.py').read_bytes()).hexdigest()"
        "==m['core']['files']['registry_candidate_diagnostics.py']}))"
    )
    proc = invoke([str(python), "-I", "-c", code], root, env)
    if proc.returncode:
        # Do not echo raw subprocess stderr (may contain private environment).
        kind = ("MISSING_MODULE" if "ModuleNotFoundError" in proc.stderr else
                "IMPORT_FAILURE" if "ImportError" in proc.stderr else
                "OTHER_RUNTIME_FAILURE")
        raise RuntimeError("installed-wheel provenance subprocess: " + kind)
    provenance = json.loads(proc.stdout)
    flags = ("wheelPackage", "wheelCore", "coreHashMatches")
    mismatches = [name for name in flags if provenance.pop(name, None) is not True]
    if mismatches:
        raise RuntimeError("installed-wheel provenance mismatch: " + ",".join(mismatches))
    return provenance


async def packaged_plan(python: Path, project: Path, out: Path,
                        registry: str, env: dict[str, str]) -> dict:
    params = StdioServerParameters(command=str(python),
                                   args=["-I", "-m", "deploom_mcp"],
                                   cwd=str(out.parent), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            listing = await client.list_tools()
            if "get_upgrade_plan" not in {item.name for item in listing.tools}:
                raise RuntimeError("installed MCP tool missing")
            result = await client.call_tool("get_upgrade_plan", {
                "project_path": str(project),
                "registry": registry,
                "target_level": "yellow",
                "min_lag_ok_pct": 80,
                "exclude": [EXCLUDED],
                "target_mode": "pinned",
                "deadline_seconds": 35,
                "draft_max_candidates": 8,
                "registry_metadata_ttl": 0,
                "use_system_proxy": False,
                "output_dir": str(out),
            })
            return result.structured_content or json.loads(result.content[0].text)


def plan_oracle(result: dict, expect_foreign: bool = False) -> str:
    if result.get("ok") is not True:
        return "MCP_PLAN_ERROR"
    status = result.get("status") or {}
    if (status.get("authority") != "PLANNING_ONLY"
        or status.get("verificationStatus") != "NOT_VERIFIED"
        or status.get("compatibility") != "UNKNOWN"):
        return "MCP_FALSE_VERIFICATION"
    if result.get("policyDiscrepancy"):
        return "MCP_POLICY_MISMATCH"
    rows = {row.get("package"): row for row in result.get("dependencies") or []}
    if set(rows) != set(BASELINE):
        return "MCP_ROW_MISMATCH"
    if rows[EXCLUDED].get("status") != "EXCLUDED":
        return "MCP_EXCLUSION_MISSING"
    if expect_foreign:
        row = rows["kontur-fixture-a"]
        if row.get("status") != "NO_TARGET" or row.get("target") is not None:
            return "MCP_FOREIGN_ARTIFACT_ALLOWED"
        if row.get("noTargetDiagnosticCode") != "NO_INSTALLABLE_ARTIFACT":
            return "MCP_FOREIGN_ARTIFACT_DIAGNOSTIC_MISSING"
    else:
        for name in ("kontur-fixture-a", "kontur-fixture-b"):
            row = rows[name]
            if row.get("status") != "PROPOSED" or row.get("target") != FINAL[name]:
                return "MCP_EXPECTED_PROPOSAL_MISSING"
    return "PASS"


def verify_blueprint(result: dict, schema: dict) -> str:
    ref = (result.get("artifacts") or {}).get("blueprint")
    if not isinstance(ref, str) or not Path(ref).is_file():
        return "FULL_BLUEPRINT_MISSING"
    try:
        bp = json.loads(Path(ref).read_text(encoding="utf-8"))
        jsonschema.validate(bp, schema)
    except (ValueError, OSError, jsonschema.ValidationError):
        return "FULL_BLUEPRINT_SCHEMA_INVALID"
    return "PASS" if bp.get("status", {}).get("authority") == "PLANNING_ONLY" else "FALSE_BLUEPRINT_AUTHORITY"


def run_preflight(python: Path, wheel: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="deploom-p1-correctness-") as td:
        root = Path(td).resolve()
        env = isolated_env(root)
        provenance = installed_provenance(python, root, env)
        if provenance.get("inventoryCount") != 134:
            raise RuntimeError("the expected P2.6 Core pin was not installed")
        release = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8"))
        if (release["core"]["revision"] != provenance["coreRevision"]
                or release["mcpVersion"] != provenance["mcpVersion"]):
            raise RuntimeError("installed wheel differs from reviewable pinned release")
        schema = json.loads((ROOT / "contracts" / "upgrade-blueprint.schema.json").read_text(encoding="utf-8"))
        checks: dict[str, str] = {}
        with LocalRegistry() as reg:
            baseline = root / "baseline"
            with patch.dict(os.environ, env, clear=True):
                make_fixture(str(baseline), reg.base)
            baseline_before = fingerprints(baseline)
            checks["baseline_fixture_input"] = (
                "PASS" if json.loads((baseline / "package.json").read_text())["dependencies"] == BASELINE
                else "BASELINE_WRONG")
            planned = asyncio.run(packaged_plan(python, baseline, root / "plan-good",
                                                reg.base, env))
            checks["packaged_stdio_plan"] = plan_oracle(planned)
            checks["full_blueprint_schema"] = verify_blueprint(planned, schema)
            checks["planning_read_only"] = ("PASS" if fingerprints(baseline) == baseline_before
                                            else "SOURCE_MUTATED_BY_PLANNER")
            unedited = root / "unchanged"
            copy_input(baseline, unedited)
            checks["unchanged_baseline_rejected"] = static_oracle(unedited, reg.base) or "UNEXPECTED_ACCEPT"
            good = root / "reference-final"
            copy_input(baseline, good)
            reference_edit(good, reg.base, env)
            checks["independent_install_build_test"] = final_oracle(good, reg.base, env)
            bad_scope = root / "excluded-changed"
            copy_input(good, bad_scope)
            pkg = json.loads((bad_scope / "package.json").read_text(encoding="utf-8"))
            pkg["dependencies"][EXCLUDED] = "4.0.0"
            (bad_scope / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
            checks["excluded_mutation_rejected"] = static_oracle(bad_scope, reg.base) or "UNEXPECTED_ACCEPT"
            bad_lock = root / "lock-tampered"
            copy_input(good, bad_lock)
            lock_path = bad_lock / "package-lock.json"
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock["packages"]["node_modules/kontur-fixture-a"]["version"] = "1.2.0"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            checks["tampered_lock_rejected"] = static_oracle(bad_lock, reg.base) or "UNEXPECTED_ACCEPT"
            broken = root / "runtime-broken"
            copy_input(good, broken)
            (broken / "src" / "index.js").write_text(
                "'use strict';\nmodule.exports = { versions: {a:'broken',b:'broken',c:'broken'} };\n",
                encoding="utf-8")
            checks["runtime_regression_rejected"] = final_oracle(broken, reg.base, env)
            checks["source_checkout_unchanged"] = (
                "PASS" if fingerprints(baseline) == baseline_before else "SOURCE_MUTATED")
        with LocalRegistry(foreign_upgrade=True) as reg:
            blocked = root / "foreign-fixture"
            with patch.dict(os.environ, env, clear=True):
                make_fixture(str(blocked), reg.base)
            before = fingerprints(blocked)
            plan = asyncio.run(packaged_plan(python, blocked, root / "plan-foreign",
                                             reg.base, env))
            checks["foreign_tarball_blocked"] = plan_oracle(plan, expect_foreign=True)
            checks["foreign_blueprint_schema"] = verify_blueprint(plan, schema)
            checks["blocked_checkout_unchanged"] = (
                "PASS" if fingerprints(blocked) == before else "SOURCE_MUTATED_BY_PLANNER")
        expected = {
            "baseline_fixture_input": "PASS",
            "packaged_stdio_plan": "PASS",
            "full_blueprint_schema": "PASS",
            "planning_read_only": "PASS",
            "unchanged_baseline_rejected": "TARGET_ASSIGNMENT_MISMATCH",
            "independent_install_build_test": "PASS",
            "excluded_mutation_rejected": "EXCLUDED_DEPENDENCY_CHANGED",
            "tampered_lock_rejected": "LOCK_VERSION_MISMATCH",
            "runtime_regression_rejected": "TEST_FAILED",
            "source_checkout_unchanged": "PASS",
            "foreign_tarball_blocked": "PASS",
            "foreign_blueprint_schema": "PASS",
            "blocked_checkout_unchanged": "PASS",
        }
        return {
            "status": "PASS" if checks == expected else "FAIL",
            "authority": "PLANNING_ONLY",
            "agentMigrationCorrectness": "NOT_TESTED",
            "agentSessionCount": 0,
            "coreRevision": provenance["coreRevision"],
            "mcpVersion": provenance["mcpVersion"],
            "wheelSha256": digest(wheel),
            "fixtureSeedSha256": digest(SEED),
            "checks": checks,
            "expected": expected,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-python", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args()
    report = run_preflight(args.wheel_python.resolve(), args.wheel.resolve())
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    print(json.dumps({"status": report["status"],
                      "checks": report["checks"],
                      "agentMigrationCorrectness": report["agentMigrationCorrectness"]},
                     sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
