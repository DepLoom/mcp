"""Independent, fail-closed oracle for the existing synthetic DepLoom E2E fixture.

This module neither invokes an agent nor trusts an agent's report, test script,
build output, install log, or planner PROPOSED verdict. Only package manifests
and lockfiles may change for this small, explicitly frozen task. Run clean
npm ci/build/test and independently inspect the installed runtime in a COPY.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit

# These are task requirements, NOT targets inferred from a planner response.
VERSIONS = {
    "kontur-fixture-a": "1.9.0",
    "kontur-fixture-b": "2.1.0",
    "kontur-fixture-c": "3.0.0",  # deliberately excluded
}
CHANGEABLE = frozenset({"package.json", "package-lock.json"})
IGNORED_DIRS = frozenset({".git", "node_modules", "dist", "__pycache__"})
MAX_FILE_BYTES = 1_048_576


class OracleFailure(Exception):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_files(root: pathlib.Path) -> dict[str, str]:
    """Snapshot the candidate, rejecting symlinks and unexpected source files."""
    if not root.is_dir() or root.is_symlink():
        raise OracleFailure("INVALID_WORKTREE")
    result: dict[str, str] = {}
    for folder, dirs, files in os.walk(root, followlinks=False):
        current = pathlib.Path(folder)
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
        for name in dirs:
            if (current / name).is_symlink():
                raise OracleFailure("SYMLINK_SOURCE")
        for name in files:
            path = current / name
            if path.is_symlink() or not path.is_file():
                raise OracleFailure("SYMLINK_SOURCE")
            relative = path.relative_to(root).as_posix()
            if path.stat().st_size > MAX_FILE_BYTES:
                raise OracleFailure("OVERSIZED_SOURCE")
            result[relative] = digest(path.read_bytes())
    return dict(sorted(result.items()))


def read_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
        raise OracleFailure("MISSING_OR_INVALID_JSON")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError):
        raise OracleFailure("MISSING_OR_INVALID_JSON") from None
    if not isinstance(obj, dict):
        raise OracleFailure("MISSING_OR_INVALID_JSON")
    return obj


def frozen_policy(baseline: pathlib.Path, candidate: pathlib.Path) -> dict[str, str]:
    """Do not consult candidate scripts, evidence JSON or mutable plan metadata."""
    baseline, candidate = baseline.resolve(), candidate.resolve()
    if baseline == candidate or baseline in candidate.parents or candidate in baseline.parents:
        raise OracleFailure("WORKTREE_NOT_ISOLATED")
    original, actual = source_files(baseline), source_files(candidate)
    assert_required = {"src/index.js", "build.js", "verify.js", ".npmrc"}
    if not assert_required.issubset(original):
        raise OracleFailure("BASELINE_INCOMPLETE")
    immutable_baseline = {k: v for k, v in original.items() if k not in CHANGEABLE}
    immutable_candidate = {k: v for k, v in actual.items() if k not in CHANGEABLE}
    if immutable_candidate != immutable_baseline:
        raise OracleFailure("UNAUTHORIZED_SOURCE_CHANGE")
    initial = read_json(baseline / "package.json")
    final = read_json(candidate / "package.json")
    before = dict(initial)
    after = dict(final)
    before.pop("dependencies", None)
    after.pop("dependencies", None)
    if before != after:
        raise OracleFailure("MANIFEST_SCOPE_CHANGE")
    if initial.get("dependencies") != {
        "kontur-fixture-a": "1.2.0",
        "kontur-fixture-b": "2.0.0",
        "kontur-fixture-c": "3.0.0",
    }:
        raise OracleFailure("BASELINE_VERSION_MISMATCH")
    if final.get("dependencies") != VERSIONS:
        raise OracleFailure("POLICY_OR_EXCLUSION_VIOLATED")
    baseline_lock = read_json(baseline / "package-lock.json")
    lock = read_json(candidate / "package-lock.json")
    if lock.get("lockfileVersion") != 3 or baseline_lock.get("lockfileVersion") != 3:
        raise OracleFailure("LOCK_FORMAT_MISMATCH")
    packages = lock.get("packages")
    if not isinstance(packages, dict) or set(packages) != {"", *("node_modules/" + p for p in VERSIONS)}:
        raise OracleFailure("LOCK_PACKAGE_SCOPE_CHANGE")
    root = packages.get("")
    if not isinstance(root, dict) or root.get("dependencies") != VERSIONS:
        raise OracleFailure("MANIFEST_LOCK_MISMATCH")
    npmrc = (baseline / ".npmrc").read_text(encoding="utf-8").strip()
    if not npmrc.startswith("registry=http://127.0.0.1:"):
        raise OracleFailure("NONLOCAL_REGISTRY")
    base = npmrc.split("=", 1)[1].rstrip("/")
    pbase = urlsplit(base)
    if pbase.hostname != "127.0.0.1" or not pbase.port or pbase.username or pbase.password:
        raise OracleFailure("NONLOCAL_REGISTRY")
    for name, wanted in VERSIONS.items():
        entry = packages.get("node_modules/" + name)
        if not isinstance(entry, dict) or entry.get("version") != wanted:
            raise OracleFailure("LOCK_VERSION_MISMATCH")
        resolved, integrity = entry.get("resolved"), entry.get("integrity")
        if not isinstance(resolved, str) or not isinstance(integrity, str) or not integrity.startswith("sha512-"):
            raise OracleFailure("LOCK_ARTIFACT_EVIDENCE_MISSING")
        try:
            parsed = urlsplit(resolved)
            if (parsed.scheme != "http" or parsed.hostname != pbase.hostname
                or parsed.port != pbase.port or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not parsed.path.startswith("/" + name + "/-/")
                or ".." in parsed.path.split("/")):
                raise OracleFailure("FOREIGN_OR_UNSAFE_LOCK_ARTIFACT")
        except ValueError:
            raise OracleFailure("FOREIGN_OR_UNSAFE_LOCK_ARTIFACT") from None
    return immutable_baseline


NODE_ORACLE = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const expected = JSON.parse(process.argv[1]);
const short = {'kontur-fixture-a':'a','kontur-fixture-b':'b','kontur-fixture-c':'c'};
const source = require('./src/index.js');
const built = JSON.parse(fs.readFileSync('dist/result.json', 'utf8'));
assert.equal(built.app, 'fixture-app');
for (const [name, version] of Object.entries(expected)) {
  const manifest = require('./node_modules/' + name + '/package.json');
  const runtime = require(name);
  assert.equal(manifest.version, version);
  assert.equal(runtime.version, version);
  assert.equal(source.versions[short[name]], version);
  assert.equal(built.versions[short[name]], version);
}
assert.equal(Object.keys(source.versions).length, 3);
assert.equal(Object.keys(built.versions).length, 3);
console.log('INDEPENDENT_RUNTIME_ORACLE_PASS');
"""


def npm_bin() -> list[str]:
    if os.name == "nt":
        return ["cmd", "/c", shutil.which("npm.cmd") or "npm"]
    return [shutil.which("npm") or "npm"]


def run_command(cmd: list[str], cwd: pathlib.Path, env: dict[str, str],
                *, timeout: int = 100) -> dict[str, Any]:
    start = time.monotonic()
    try:
        result = subprocess.run(cmd, cwd=cwd, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False)
        return {"exitCode": result.returncode, "wallSeconds": round(time.monotonic()-start, 3)}
    except (OSError, subprocess.TimeoutExpired):
        return {"exitCode": None, "wallSeconds": round(time.monotonic()-start, 3)}


def verify_candidate(baseline: pathlib.Path, candidate: pathlib.Path,
                     *, timeout: int = 100) -> dict[str, Any]:
    """Return outcome and independent command exits, never agent-provided PASS."""
    start = time.monotonic()
    evidence: dict[str, Any] = {"status": "FAIL", "reason": None, "checks": {},
                               "authority": "INDEPENDENT_FIXTURE_ORACLE_NOT_MCP"}
    try:
        frozen = frozen_policy(baseline, candidate)
        evidence["checks"]["policy"] = "PASS"
        evidence["baselineFrozenFileCount"] = len(frozen)
        with tempfile.TemporaryDirectory(prefix="deploom-correctness-oracle-") as tmp:
            root = pathlib.Path(tmp)
            stage = root / "staged-copy"
            shutil.copytree(candidate, stage,
                ignore=shutil.ignore_patterns(".git", "node_modules", "dist", "__pycache__"))
            empty_config = root / "empty.npmrc"
            empty_config.write_text("", encoding="utf-8")
            registry = (baseline / ".npmrc").read_text(encoding="utf-8").strip().split("=", 1)[1]
            env = {k: v for k, v in os.environ.items()
                   if not k.lower().startswith("npm_config_")
                   and k not in {"NODE_OPTIONS", "NPM_TOKEN", "HTTP_PROXY", "HTTPS_PROXY",
                                 "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"}}
            env.update({"npm_config_registry": registry, "npm_config_audit": "false",
                        "npm_config_fund": "false", "npm_config_ignore_scripts": "true",
                        "npm_config_fetch_retries": "0", "npm_config_update_notifier": "false",
                        "npm_config_cache": str(root / "cache"),
                        "npm_config_userconfig": str(empty_config),
                        "npm_config_globalconfig": str(empty_config),
                        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
            stages = (
                ("cleanInstall", npm_bin()+["ci", "--ignore-scripts", "--no-audit", "--no-fund"]),
                ("dependencyTree", npm_bin()+["ls", "--all", "--json"]),
                ("build", npm_bin()+["run", "build", "--ignore-scripts=false"]),
                ("test", npm_bin()+["test", "--ignore-scripts=false"]),
                ("independentRuntime", [shutil.which("node") or "node", "-e",
                                        NODE_ORACLE, json.dumps(VERSIONS, sort_keys=True)]),
            )
            for label, cmd in stages:
                result = run_command(cmd, stage, env, timeout=timeout)
                evidence["checks"][label] = result
                if result["exitCode"] != 0:
                    raise OracleFailure(label.upper() + "_FAILED")
            if source_files(stage) != source_files(candidate):
                raise OracleFailure("BUILD_OR_INSTALL_MUTATED_SOURCE")
            evidence["checks"]["sourceAfterChecks"] = "PASS"
        evidence["status"] = "PASS"
    except OracleFailure as exc:
        evidence["reason"] = str(exc)
    except (OSError, KeyError, UnicodeError, TypeError) as exc:
        evidence["reason"] = "ORACLE_INFRASTRUCTURE_" + type(exc).__name__
    evidence["wallSeconds"] = round(time.monotonic()-start, 3)
    return evidence
