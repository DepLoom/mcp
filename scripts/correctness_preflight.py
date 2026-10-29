"""No-model correctness preflight using the EXISTING DepLoom E2E seed/registry.

selftest: build a disposable fixture, prove an independent clean-install oracle
accepts the known-good deterministic migration and rejects unsafe variants,
then initialize/call the REAL packaged-wheel MCP over stdio (no fake Core).
verify: score an agent's worktree against a separate frozen baseline; only
the oracle's output is authoritative for this synthetic exercise.

NEVER executes an agent, migrates a user project or queries an external registry.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import venv
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from correctness_oracle import (
    VERSIONS, npm_bin, read_json, source_files, verify_candidate,
    CHALK_BASELINE, CHALK_TARGET, CHALK_REGISTRY, _chalk_oracle_env,
    verify_chalk_candidate,
)
from e2e_fixture import make_fixture
from e2e_registry import Handler, PackageStore


def load_seed() -> dict:
    return json.loads((HERE / "e2e_seed.json").read_text(encoding="utf-8-sig"))


@contextlib.contextmanager
def seeded_registry(port: int = 0):
    """In-process loopback registry. No external network and no raw URL logs."""
    class IsolatedHandler(Handler):
        pass
    server = ThreadingHTTPServer(("127.0.0.1", port), IsolatedHandler)
    url = f"http://127.0.0.1:{server.server_port}"
    IsolatedHandler.store = PackageStore(load_seed(), url)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@contextlib.contextmanager
def clean_npm_environment(root: pathlib.Path, registry: str):
    user_config = root / "empty-user.npmrc"
    global_config = root / "empty-global.npmrc"
    user_config.write_text("", encoding="utf-8")
    global_config.write_text("", encoding="utf-8")
    overrides = {
        "npm_config_registry": registry, "npm_config_audit": "false",
        "npm_config_fund": "false", "npm_config_ignore_scripts": "true",
        "npm_config_fetch_retries": "0", "npm_config_update_notifier": "false",
        "npm_config_cache": str(root / "npm-cache"),
        "npm_config_userconfig": str(user_config),
        "npm_config_globalconfig": str(global_config),
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    old = dict(os.environ)
    try:
        for k in list(os.environ):
            if k.lower().startswith("npm_config_") or k in (
                "NODE_OPTIONS", "NPM_TOKEN", "HTTP_PROXY", "HTTPS_PROXY",
                "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy",
            ):
                os.environ.pop(k, None)
        os.environ.update(overrides)
        yield
    finally:
        os.environ.clear()
        os.environ.update(old)


def copy_source(source: pathlib.Path, destination: pathlib.Path) -> None:
    shutil.copytree(source, destination,
                    ignore=shutil.ignore_patterns(".git", "node_modules", "dist", "__pycache__"))


def make_positive_candidate(baseline: pathlib.Path, candidate: pathlib.Path,
                            registry: str) -> None:
    copy_source(baseline, candidate)
    manifest_path = candidate / "package.json"
    manifest = read_json(manifest_path)
    manifest["dependencies"] = dict(VERSIONS)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    command = npm_bin() + [
        "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund",
        "--registry", registry,
    ]
    result = subprocess.run(command, cwd=candidate, capture_output=True, text=True, timeout=100)
    if result.returncode:
        raise RuntimeError("synthetic candidate lock regeneration failed (nonzero exit)")


PACKAGED_STDIO_PROGRAM = r"""
import asyncio, hashlib, json, os, pathlib, sys
import deploom_mcp
from deploom_mcp.distribution import release_manifest_path, resolve_core_dir, skill_root
from mcp import ClientSession, StdioServerParameters, stdio_client

home=pathlib.Path.cwd().resolve()
assert pathlib.Path(deploom_mcp.__file__).resolve().is_relative_to(home / 'venv'), 'not a packaged wheel'
core=resolve_core_dir()
assert core.is_relative_to(home / 'venv') and (core/'registry_candidate_diagnostics.py').is_file()
release=json.loads(release_manifest_path().read_text(encoding='utf-8'))
assert hashlib.sha256((core/'registry_candidate_diagnostics.py').read_bytes()).hexdigest()==release['core']['files']['registry_candidate_diagnostics.py']
skill=skill_root()
contract_file=skill/'contract.json'
contract=json.loads(contract_file.read_text(encoding='utf-8'))
assert hashlib.sha256(contract_file.read_bytes()).hexdigest()==release['skillManifestSha256']
assert contract['bundleSha256']==release['skill']['bundleSha256']
for rel,expected in contract['files'].items():
    assert hashlib.sha256((skill/rel).read_bytes()).hexdigest()==expected
project=home/'empty-project'
project.mkdir()
manifest=project/'package.json'
manifest.write_text('{"name":"isolated-oracle-empty","version":"1.0.0","private":true,"dependencies":{}}')
original=hashlib.sha256(manifest.read_bytes()).hexdigest()

def decoded(response):
    return response.structured_content or json.loads(response.content[0].text)

async def check():
    env={k:v for k,v in os.environ.items() if k!='PYTHONPATH' and not k.startswith('DEPLOOM_')}
    env['DEPLOOM_MCP_STATE_DIR']=str(home/'mcp-state')
    params=StdioServerParameters(command=sys.executable,args=['-m','deploom_mcp'],cwd=str(home),env=env)
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as client:
            await client.initialize()
            tools=await client.list_tools()
            assert 'get_upgrade_plan' in {t.name for t in tools.tools}
            wrong=decoded(await client.call_tool('get_upgrade_plan',{
                'project_path':str(project),'registry':'https://user:do-not-expose@example.invalid',
            }))
            assert wrong.get('ok') is False and wrong.get('error',{}).get('code')=='invalid-registry', wrong
            assert 'do-not-expose' not in json.dumps(wrong)
            blocked=decoded(await client.call_tool('get_upgrade_plan',{
                'project_path':str(project),'output_dir':str(project/'artifacts'),
            }))
            assert blocked.get('ok') is False and blocked.get('error',{}).get('code')=='output-inside-project',blocked
            good=decoded(await client.call_tool('get_upgrade_plan',{
                'project_path':str(project),'registry':sys.argv[1],
                'target_level':'yellow','deadline_seconds':10.0,
                'output_dir':str(home/'output'),'use_system_proxy':False,
            }))
            assert good.get('ok') is True, (good.get('error'),good.get('status'))
            state=good.get('status') or {}
            assert state.get('authority')=='PLANNING_ONLY',state
            assert state.get('verificationStatus')=='NOT_VERIFIED',state
            assert state.get('compatibility')=='UNKNOWN',state
    return good
result=asyncio.run(check())
assert hashlib.sha256(manifest.read_bytes()).hexdigest()==original, 'MCP edited source'
print(json.dumps({'status':'PASS','transport':'REAL_PACKAGED_MCP_STDIO',
                  'coreRevision':release['core']['revision'],
                  'skillBundleSha256':contract['bundleSha256'],
                  'planningAuthority':'PLANNING_ONLY','verificationStatus':'NOT_VERIFIED',
                  'baselineUnchanged':True},sort_keys=True))
"""


def packaged_stdio(wheel: pathlib.Path, directory: pathlib.Path, registry: str) -> dict:
    """Reuse the isolated wheel smoke setup, but CALL tools/get_upgrade_plan."""
    wheel = wheel.resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        return {"status": "FAIL", "reason": "WHEEL_NOT_FOUND"}
    home = directory / "wheel-runtime"
    home.mkdir()
    venv.EnvBuilder(with_pip=True).create(home / "venv")
    executable = home / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    clean_env = {k: v for k, v in os.environ.items()
                 if k != "PYTHONPATH" and not k.startswith("DEPLOOM_")}
    clean_env["DEPLOOM_MCP_STATE_DIR"] = str(home / "state")
    site = subprocess.check_output(
        [str(executable), "-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
        cwd=home, env=clean_env, text=True).strip()
    import sysconfig
    dependencies = sorted({sysconfig.get_path("purelib"), sysconfig.get_path("platlib")})
    (pathlib.Path(site) / "preflight_runtime_libraries.pth").write_text(
        "\n".join(dependencies) + "\n", encoding="utf-8")
    subprocess.run([str(executable), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
                   cwd=home, env=clean_env, check=True, capture_output=True, timeout=100)
    try:
        result = subprocess.run([str(executable), "-I", "-c", PACKAGED_STDIO_PROGRAM, registry],
                                cwd=home, env=clean_env, capture_output=True, text=True, timeout=100)
        if result.returncode:
            return {"status": "FAIL", "reason": "PACKAGED_STDIO_ASSERTION_FAILED",
                    "exitCode": result.returncode}
        parsed = json.loads(result.stdout.strip().splitlines()[-1])
        return parsed if parsed.get("status") == "PASS" else {"status": "FAIL", "reason": "STDIO_NOT_CONFIRMED"}
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return {"status": "FAIL", "reason": "PACKAGED_STDIO_INFRASTRUCTURE"}


def selftest(wheel: pathlib.Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="deploom-p1-selftest-") as temp:
        root = pathlib.Path(temp)
        with seeded_registry() as registry:
            with clean_npm_environment(root, registry):
                baseline = root / "baseline"
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        make_fixture(str(baseline), registry)
                except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                    import re
                    found = re.search(r"(?i)(?:npm\\s+(?:ERR!|error)\\s+code\\s+)([A-Z0-9_-]+)", str(exc))
                    return {"status": "FAIL", "reason": "BASELINE_NPM_PREP_FAILED",
                            "npmErrorCode": found.group(1) if found else "UNKNOWN"}
                positive = root / "positive"
                try:
                    make_positive_candidate(baseline, positive, registry)
                except (RuntimeError, OSError, subprocess.SubprocessError):
                    return {"status": "FAIL", "reason": "POSITIVE_LOCK_PREP_FAILED"}
                result = verify_candidate(baseline, positive)
                negative: dict[str, str] = {}
                cases = ("excluded-upgraded", "stale-lock", "tampered-checks", "foreign-tarball",
                         "unauthorized-file", "fake-source")
                for case in cases:
                    candidate = root / case
                    if case == "stale-lock":
                        copy_source(baseline, candidate)
                        pkg = read_json(candidate / "package.json")
                        pkg["dependencies"] = dict(VERSIONS)
                        (candidate / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
                    else:
                        copy_source(positive, candidate)
                        if case == "excluded-upgraded":
                            pkg = read_json(candidate / "package.json")
                            pkg["dependencies"]["kontur-fixture-c"] = "4.0.0"
                            (candidate / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
                        elif case == "tampered-checks":
                            (candidate / "verify.js").write_text("console.log('SMOKE_test_OK');\n", encoding="utf-8")
                            (candidate / "build.js").write_text("console.log('BUILD_OK');\n", encoding="utf-8")
                        elif case == "foreign-tarball":
                            lock = read_json(candidate / "package-lock.json")
                            lock["packages"]["node_modules/kontur-fixture-a"]["resolved"] = "https://outside.invalid/a.tgz"
                            (candidate / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
                        elif case == "unauthorized-file":
                            (candidate / "extra-script.js").write_text("process.exit(0);\n", encoding="utf-8")
                        elif case == "fake-source":
                            (candidate / "src/index.js").write_text("module.exports={versions:{a:'1.9.0',b:'2.1.0',c:'3.0.0'}};",encoding="utf-8")
                    check = verify_candidate(baseline, candidate)
                    negative[case] = check.get("reason") or "UNEXPECTED_PASS"
                stdio = packaged_stdio(wheel, root, registry)
                all_rejected = all(x != "UNEXPECTED_PASS" for x in negative.values())
                success = result.get("status")=="PASS" and all_rejected and stdio.get("status")=="PASS"
                return {"status": "PASS" if success else "FAIL",
                        "kind": "DETERMINISTIC_SYNTHETIC_PREFLIGHT_NO_AGENT",
                        "positiveOracle": result,
                        "negativeRejections": negative,
                        "packagedMcpStdio": stdio,
                        "fixtureSeedSha256": hashlib.sha256((HERE/"e2e_seed.json").read_bytes()).hexdigest(),
                        "claims": {"agentMigration": "NOT_RUN", "modelBenchmark": "NOT_RUN",
                                   "liveRegistrySecurity": "NOT_RUN",
                                   "installationOfUserProject": "NOT_RUN"}}


def verify_external(baseline: pathlib.Path, candidate: pathlib.Path) -> dict:
    original = (baseline / ".npmrc").read_text(encoding="utf-8").strip()
    if not original.startswith("registry=http://127.0.0.1:"):
        raise RuntimeError("baseline must specify a loopback registry")
    parsed = urlsplit(original.split("=", 1)[1])
    if not parsed.port or parsed.hostname!="127.0.0.1":
        raise RuntimeError("invalid frozen registry")
    with seeded_registry(parsed.port):
        return verify_candidate(baseline, candidate)



# A *real public* breaking-API case, not a fake package. Chalk 4.1.2
# exposes .keyword() in CJS; Chalk 5.3.0 is ESM-only and removes .keyword().
# The gold patch is evaluator-only and is never exported to the agent seed.
_CHALK_FORMAT_V4 = """\
'use strict';
const chalk = require('chalk');
module.exports = name => chalk.keyword('orange')('READY ' + name);
"""
_CHALK_GOLD_PATCH = """\
'use strict';
module.exports = async name => {
  const {default: chalk} = await import('chalk');
  return chalk.yellow('READY ' + name);
};
"""
_CHALK_BUILD = """\
'use strict';
const fs = require('node:fs');
const render = require('./src/format.cjs');
Promise.resolve().then(() => render('Ada')).then(message => {
  if (message !== 'READY Ada') throw new Error('BREAKING_API_BUILD_FAILED');
  fs.mkdirSync('dist', {recursive: true});
  fs.writeFileSync('dist/result.json',
    JSON.stringify({message, excluded: '7.0.0'}) + '\\n');
}).catch(error => {console.error(error.name);process.exitCode=1;});
"""
_CHALK_TEST = """\
'use strict';
const assert=require('node:assert/strict');
const render=require('./src/format.cjs');
Promise.resolve().then(() => render('Ada')).then(message => {
  assert.equal(message, 'READY Ada');
  assert.equal(require('is-number')('42'), true);
}).catch(error => {console.error(error.name);process.exitCode=1;});
"""
_CHALK_INDEX = """\
'use strict';
module.exports = require('./format.cjs');
"""
_CHALK_README = """# Chalk 4 -> 5 public breaking-API exercise

Change only chalk from 4.1.2 to 5.3.0 and adapt src/format.cjs while preserving
the existing output READY Ada. Keep is-number at 7.0.0. Do not edit test.cjs,
build.cjs, src/index.cjs or their package scripts. The independent evaluator
is NOT in this workspace. No test bypass, unrelated file changes, or release.
"""


def _chalk_write_baseline(root: pathlib.Path, env: dict[str, str]) -> None:
    root.mkdir()
    (root / "src").mkdir()
    manifest = {
        "name": "deploom-public-chalk-breaking-demo", "version": "1.0.0",
        "private": True, "scripts": {
            "build": "node build.cjs", "test": "node test.cjs",
        }, "dependencies": dict(CHALK_BASELINE),
    }
    (root / "package.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for name, content in {
        ".npmrc": "registry=" + CHALK_REGISTRY + "\n",
        "src/format.cjs": _CHALK_FORMAT_V4,
        "src/index.cjs": _CHALK_INDEX,
        "build.cjs": _CHALK_BUILD,
        "test.cjs": _CHALK_TEST,
        "README.md": _CHALK_README,
    }.items():
        (root / name).write_text(content, encoding="utf-8")
    _chalk_make_lock(root, env)


def _chalk_make_lock(project: pathlib.Path, env: dict[str, str]) -> None:
    result = subprocess.run(
        npm_bin() + ["install", "--package-lock-only", "--ignore-scripts",
                     "--no-audit", "--no-fund", "--registry", CHALK_REGISTRY],
        cwd=project, env=env, capture_output=True, text=True, timeout=100)
    if result.returncode:
        # Cannot call this a product failure when public registry is offline.
        raise RuntimeError("PUBLIC_REGISTRY_BOOTSTRAP_UNAVAILABLE")


def _chalk_version_only(baseline: pathlib.Path, candidate: pathlib.Path,
                        env: dict[str, str]) -> None:
    copy_source(baseline, candidate)
    path = candidate / "package.json"
    manifest = read_json(path)
    manifest["dependencies"] = dict(CHALK_TARGET)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _chalk_make_lock(candidate, env)


def _public_source_digest(source: pathlib.Path) -> str:
    files = source_files(source)
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def breaking_demo(wheel: pathlib.Path, snapshot_dir: pathlib.Path | None = None) -> dict:
    import time
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="deploom-p2-chalk-") as td:
        root = pathlib.Path(td)
        env = _chalk_oracle_env(root)
        baseline = root / "baseline"
        _chalk_write_baseline(baseline, env)
        target = root / "version-only"
        _chalk_version_only(baseline, target, env)
        reference = root / "reference"
        copy_source(target, reference)
        (reference / "src" / "format.cjs").write_text(
            _CHALK_GOLD_PATCH, encoding="utf-8")
        setup = round(time.monotonic() - start, 3)
        if snapshot_dir is not None:
            if snapshot_dir.exists():
                raise RuntimeError("SNAPSHOT_DESTINATION_EXISTS")
            # Deliberately copy only the unsolved baseline. No gold reference,
            # agent logs, credentials, or independent evaluation code.
            copy_source(baseline, snapshot_dir)
        # The baseline oracle always receives a distinct disposable copy.
        seed_copy = root / "baseline-control"
        copy_source(baseline, seed_copy)
        baseline_result = verify_chalk_candidate(baseline, seed_copy, baseline_mode=True)
        version_only = verify_chalk_candidate(baseline, target)
        patched = verify_chalk_candidate(baseline, reference)
        negative = {}
        for case in ("foreign-lock", "excluded-upgraded", "deleted-tests",
                     "forged-build-script", "fake-evidence"):
            work = root / case
            copy_source(reference, work)
            if case == "foreign-lock":
                lock = read_json(work / "package-lock.json")
                lock["packages"]["node_modules/chalk"]["resolved"] = (
                    "https://example.invalid/chalk-5.3.0.tgz")
                (work / "package-lock.json").write_text(json.dumps(lock))
            elif case == "excluded-upgraded":
                manifest = read_json(work / "package.json")
                manifest["dependencies"]["is-number"] = "7.0.1"
                (work / "package.json").write_text(json.dumps(manifest))
            elif case == "deleted-tests":
                (work / "test.cjs").unlink()
            elif case == "forged-build-script":
                manifest = read_json(work / "package.json")
                manifest["scripts"]["build"] = "node -e 'console.log(1)'"
                (work / "package.json").write_text(json.dumps(manifest))
            elif case == "fake-evidence":
                (work / "fake-evidence.json").write_text('{"outcome":"PASSED"}')
            outcome = verify_chalk_candidate(baseline, work)
            negative[case] = {
                "status": outcome["status"],
                "reason": outcome["reason"],
                "npmNotInvoked": "install" not in outcome["checks"],
            }
        stdio = packaged_stdio(wheel, root, CHALK_REGISTRY)
        release = read_json(ROOT / "release-manifest.json")
        skill = read_json(ROOT / "skills" / "deploom-upgrade" / "contract.json")
        versions = {}
        for key, argv in (("node", ["node", "--version"]),
                          ("npm", npm_bin() + ["--version"]),
                          ("python", [sys.executable, "--version"])):
            ver = subprocess.run(argv, capture_output=True, text=True, timeout=10)
            versions[key] = (ver.stdout or ver.stderr).strip().splitlines()[0] if ver.returncode == 0 else "UNAVAILABLE"
        checks = {
            "baselinePASS": baseline_result["status"] == "PASS",
            "versionOnlyInstallPASS": version_only["checks"].get("install", {}).get("exitCode") == 0,
            "versionOnlyRuntimeFAIL": version_only["status"] == "FAIL" and all(
                version_only["checks"].get(x, {}).get("exitCode") not in (None, 0)
                for x in ("build", "test", "runtime")),
            "referenceCodePatchPASS": patched["status"] == "PASS",
            "unsafeControlsFAIL": all(x["status"] == "FAIL" and x["npmNotInvoked"]
                                       for x in negative.values()),
            "realPackagedStdioPASS": (
                stdio.get("status") == "PASS"
                and stdio.get("coreRevision") == release["core"]["revision"]
                and stdio.get("skillBundleSha256") == skill["bundleSha256"]
            ),
        }
        return {
            "status": "PASS" if all(checks.values()) else "FAIL",
            "case": "PUBLIC_CHALK_4_1_2_TO_5_3_0_BREAKING_API",
            "checks": checks, "controls": {
                "baseline": baseline_result,
                "versionOnly": version_only,
                "referencePatch": patched,
                "unsafe": negative,
            },
            "setupWallSeconds": setup,
            "totalWallSeconds": round(time.monotonic() - start, 3),
            "provenance": {
                "mcpWheelSha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "coreRevision": release["core"]["revision"],
                "coreInventorySha256": release["core"]["contentSha256"],
                "skillBundleSha256": skill["bundleSha256"],
                "skillContractSha256": hashlib.sha256(
                    (ROOT / "skills/deploom-upgrade/contract.json").read_bytes()).hexdigest(),
                "baselineLockSha256": hashlib.sha256(
                    (baseline / "package-lock.json").read_bytes()).hexdigest(),
                "targetLockSha256": hashlib.sha256(
                    (target / "package-lock.json").read_bytes()).hexdigest(),
                "baselineSourceSha256": _public_source_digest(baseline),
                "packageVersions": {
                    "before": dict(CHALK_BASELINE), "after": dict(CHALK_TARGET),
                },
                "registryAuthority": "PUBLIC_NPMJS_LOCKED_INTEGRITY",
                "networkMode": "LIVE_PUBLIC_NPM_WITH_LOCKFILE_INTEGRITY",
                "runtimeVersions": versions,
            },
            "mcp": {
                "status": stdio.get("status"),
                "authority": stdio.get("planningAuthority"),
                "verificationStatus": stdio.get("verificationStatus"),
                "coreRevisionObserved": stdio.get("coreRevision"),
                "skillBundleSha256Observed": stdio.get("skillBundleSha256"),
            },
            "claims": {
                "agentMigration": "NOT_RUN", "benchmark": "NOT_RUN",
                "mcpMigrationExecution": "NOT_SUPPORTED",
                "securityAudit": "NOT_RUN",
                "manualReferencePatch": "EVALUATOR_ONLY",
            },
        }


def verify_chalk_frozen(baseline: pathlib.Path, candidate: pathlib.Path,
                        expected_baseline_sha256: str) -> dict:
    """Future pilot scorer; reject changed frozen seed before touching npm."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_baseline_sha256):
        return {"status": "FAIL", "reason": "MISSING_OR_BAD_BASELINE_SHA"}
    observed = _public_source_digest(baseline)
    if observed != expected_baseline_sha256:
        return {"status": "FAIL", "reason": "FROZEN_INPUT_DIGEST_MISMATCH",
                "baselineVerified": False}
    report = verify_chalk_candidate(baseline, candidate)
    report["baselineVerified"] = True
    report["baselineSourceSha256"] = observed
    report["agentClaimedOutcomeIgnored"] = True
    report["mcpExecutionAuthority"] = "PLANNING_ONLY"
    return report

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["selftest", "verify", "breaking-demo", "breaking-verify"])
    ap.add_argument("--wheel", type=pathlib.Path)
    ap.add_argument("--baseline", type=pathlib.Path)
    ap.add_argument("--candidate", type=pathlib.Path)
    ap.add_argument("--report", type=pathlib.Path, required=True)
    ap.add_argument("--snapshot-dir", type=pathlib.Path, help="write only the unsolved public baseline for future pilot")
    ap.add_argument("--expected-baseline-sha256", help="required frozen source digest for breaking-verify")
    args = ap.parse_args()
    if args.mode in ("selftest", "breaking-demo") and not args.wheel:
        ap.error("--wheel is required for packaged stdio selftest")
    if args.mode in ("verify", "breaking-verify") and (not args.baseline or not args.candidate):
        ap.error("--baseline and --candidate are required for independent verification")
    if args.mode == "breaking-verify" and not args.expected_baseline_sha256:
        ap.error("--expected-baseline-sha256 is required for breaking-verify")
    output = args.report.resolve()
    sources = [p.resolve() for p in (args.baseline,args.candidate) if p]
    if any(output == source or source in output.parents for source in sources):
        ap.error("--report must live outside baseline and candidate")
    try:
        report = (selftest(args.wheel) if args.mode == "selftest"
                  else breaking_demo(args.wheel.resolve(), args.snapshot_dir.resolve() if args.snapshot_dir else None)
                  if args.mode == "breaking-demo"
                  else verify_chalk_frozen(args.baseline, args.candidate, args.expected_baseline_sha256)
                  if args.mode == "breaking-verify"
                  else verify_external(args.baseline, args.candidate))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report = {"status": "FAIL", "reason": "PREFLIGHT_INFRASTRUCTURE_" + type(exc).__name__}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {"status": report["status"], "report": output.name,
               "reason": report.get("reason")}
    if args.mode == "breaking-demo":
        control = report.get("controls", {})
        summary.update({
            "negativeCases": len(control.get("unsafe", {})),
            "packagedStdio": (report.get("mcp") or {}).get("status", "NOT_RUN"),
            "baseline": "PASS" if report.get("checks", {}).get("baselinePASS") else "NOT_PASS",
            "versionOnlyInstall": "PASS" if report.get("checks", {}).get("versionOnlyInstallPASS") else "NOT_PASS",
            "versionOnlyBehavior": "FAIL_AS_EXPECTED" if report.get("checks", {}).get("versionOnlyRuntimeFAIL") else "NOT_CONFIRMED",
            "referencePatch": "PASS" if report.get("checks", {}).get("referenceCodePatchPASS") else "NOT_PASS",
            "setupWallSeconds": report.get("setupWallSeconds"),
        })
    else:
        summary.update({
            "negativeCases": len(report.get("negativeRejections", {})),
            "packagedStdio": report.get("packagedMcpStdio", {}).get("status", "NOT_RUN"),
        })
    print(json.dumps(summary, sort_keys=True))
    return 0 if report.get("status")=="PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
