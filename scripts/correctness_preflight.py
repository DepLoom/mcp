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
from correctness_oracle import VERSIONS, npm_bin, read_json, source_files, verify_candidate
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
from deploom_mcp.distribution import release_manifest_path, resolve_core_dir
from mcp import ClientSession, StdioServerParameters, stdio_client

home=pathlib.Path.cwd().resolve()
assert pathlib.Path(deploom_mcp.__file__).resolve().is_relative_to(home / 'venv'), 'not a packaged wheel'
core=resolve_core_dir()
assert core.is_relative_to(home / 'venv') and (core/'registry_candidate_diagnostics.py').is_file()
release=json.loads(release_manifest_path().read_text(encoding='utf-8'))
assert hashlib.sha256((core/'registry_candidate_diagnostics.py').read_bytes()).hexdigest()==release['core']['files']['registry_candidate_diagnostics.py']
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["selftest", "verify"])
    ap.add_argument("--wheel", type=pathlib.Path)
    ap.add_argument("--baseline", type=pathlib.Path)
    ap.add_argument("--candidate", type=pathlib.Path)
    ap.add_argument("--report", type=pathlib.Path, required=True)
    args = ap.parse_args()
    if args.mode == "selftest" and not args.wheel:
        ap.error("--wheel is required for packaged stdio selftest")
    if args.mode == "verify" and (not args.baseline or not args.candidate):
        ap.error("--baseline and --candidate are required for independent verification")
    output = args.report.resolve()
    sources = [p.resolve() for p in (args.baseline,args.candidate) if p]
    if any(output == source or source in output.parents for source in sources):
        ap.error("--report must live outside baseline and candidate")
    try:
        report = (selftest(args.wheel) if args.mode == "selftest"
                  else verify_external(args.baseline, args.candidate))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report = {"status": "FAIL", "reason": "PREFLIGHT_INFRASTRUCTURE_" + type(exc).__name__}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status":report["status"],"report":output.name,
                      "reason":report.get("reason"),
                      "negativeCases":len(report.get("negativeRejections", {})),
                      "packagedStdio":report.get("packagedMcpStdio",{}).get("status","NOT_RUN")},
                     sort_keys=True))
    return 0 if report.get("status")=="PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
