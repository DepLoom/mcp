"""No-agent installed-wheel gate on the public React npm-workspaces fixture."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from deploom_mcp.distribution import release_manifest_path, resolve_core_dir
from deploom_mcp.workspace_inventory import observe_npm_workspace


def dump(value):
    return (json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_pinned_artifacts(packages, artifacts, registry):
    public = "https://registry.npmjs.org"
    for slot, row in packages.items():
        if "node_modules/" not in slot or row.get("link"):
            continue
        name = slot.rsplit("node_modules/", 1)[-1]
        pinned = artifacts[f'{name}@{row["version"]}']
        if (row["integrity"] != pinned["integrity"]
                or row["resolved"] != registry + pinned["url"][len(public):]):
            raise RuntimeError("candidate artifact differs from fixture")


def place(project, files):
    project.mkdir()
    for name, raw in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def apply_verified_fixture(out, baseline, trial, resolver, receipt, cli):
    proof = json.loads(receipt.read_bytes())
    terminal = json.loads(cli("verify-status", "verify-status", receipt))
    name = "packages/app/main.cjs"
    repairs = out / "approved-source-repairs.json"
    repairs.write_bytes(dump([{"path": name, "beforeSha256": sha(baseline / name),
                              "afterSha256": sha(trial / name)}]))
    native = json.loads((resolver / "receipt.json").read_bytes())["receipt"]
    delivery = out / "delivery"
    cli("deliver", "deliver-targeted", trial, "--expected-source-key", proof["sourceSnapshotKey"],
        "--expected-lock-sha256", native["candidateLockSha256"],
        "--expected-resolver-identity-sha256", native["resolverIdentitySha256"],
        "--baseline-lock", baseline / "package-lock.json", "--resolver-dir", resolver,
        "--check", "npm ls --all", "--check", "node packages/app/main.cjs", "--check", "node packages/legacy/main.cjs",
        "--attempt-receipt", receipt, "--expected-receipt-sha256", terminal["receiptSha256"],
        "--baseline-project", baseline, "--expected-baseline-source-key", cli("baseline-key", "source-key", baseline),
        "--destination", delivery, "--source-repairs-json", repairs)
    artifact = delivery / "receipt.json"
    target = out / "applied"
    result = json.loads(cli("apply", "apply-delivery", baseline, "--delivery", delivery, "--destination", target,
                            "--expected-delivery-sha256", sha(artifact), "--goal", "targeted",
                            "--expected-profile-sha256", proof["finalVerificationProfileDigest"]))
    if (result["state"] != "APPLIED"
            or result["installation"] != "NOT_RUN_IN_DELIVERED_COPY"
            or (target / "project/node_modules").exists()
            or sha(target / "project" / name) != sha(trial / name)):
        raise RuntimeError("applied source differs from the verified fixture")
    return sha(target / "terminal.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--registry", default="https://registry.npmjs.org")
    args = parser.parse_args()
    from deploom_mcp.dependency_audit import checked_registry

    registry = checked_registry(args.registry)
    if registry is None or not registry.startswith("https://"):
        parser.error("use an HTTPS npm registry")
    out = args.out.resolve()
    out.mkdir()
    source = Path(__file__).resolve().parents[1] / "tests/fixtures/react_workspace.json"
    fixture = json.loads(source.read_bytes())
    core = resolve_core_dir()
    if not (core.name == "_core" and core.is_dir()):
        raise RuntimeError("run with the installed MCP wheel")
    sys.path.insert(0, str(core))
    from draft_npm_preflight import _npm_command
    from workspace_peer_model import WorkspaceTopology, declaration_id, plan_workspace_upgrade
    from workspace_npm_preflight import WORKSPACE_NPM_POLICY, isolated_workspace_npm_environment, run_workspace_npm_preflight

    # The manifest and tarball bytes are pinned independently of any test host.
    # A local mirror can provide the same bytes without altering global npm.
    public = "https://registry.npmjs.org"
    lock = fixture["baselineLock"]
    for row in lock["packages"].values():
        if not row.get("integrity"):
            continue
        if not row["resolved"].startswith(public + "/"):
            raise RuntimeError("nonpublic fixture lock")
        row["resolved"] = registry + row["resolved"][len(public):]
    files = {name: text.encode() for name, text in fixture["source"].items()}
    baseline, trial, split, cache = (out / name for name in ("baseline", "trial", "split", "cache"))
    place(baseline, {**files, "package-lock.json": dump(lock)})
    cache.mkdir()
    baseline_hashes = {name: sha(baseline / name) for name in (*files, "package-lock.json")}
    os.environ["GIT_CEILING_DIRECTORIES"] = str(out)
    npm = _npm_command()
    env = isolated_workspace_npm_environment(os.environ)
    configs = [out / name for name in ("user.npmrc", "global.npmrc")]
    for config in configs:
        config.touch(exist_ok=False)
    npm_options = ["--registry", registry, "--cache", str(cache),
                   "--userconfig", str(configs[0]), "--globalconfig", str(configs[1])]

    def run(name, argv, *, cwd=out, environment=None, expected=None):
        result = subprocess.run(argv, cwd=cwd, env=environment, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=120)
        for stream in ("stdout", "stderr"):
            (out / f"{name}.{stream}.txt").write_text(getattr(result, stream).replace(registry, "<npm-registry>"), encoding="utf-8")
        if expected is None and result.returncode != 0:
            raise RuntimeError(f"{name} exited {result.returncode}; see saved logs")
        if expected is not None and (result.returncode == 0 or expected not in result.stderr):
            raise RuntimeError(f"{name} did not produce {expected}; see saved logs")
        return result.stdout.strip()

    def cli(name, *argv):
        return run(name, [sys.executable, "-m", "deploom_mcp.workspace_acceptance", *map(str, argv)])

    node_version = run("node-version", [npm[0], "--version"]).lstrip("v")
    npm_version = run("npm-version", [*npm, "--version"])
    if (node_version, npm_version) != ("24.18.0", "11.16.0"):
        raise RuntimeError("expected Node 24.18.0 and npm 11.16.0")
    # Confirm the pinned public SRI and seed a private cache. The later
    # strict offline npm ci verifies the installed tarball bytes.
    for number, artifact in enumerate(fixture["artifacts"].values()):
        spec = f'{artifact["name"]}@{artifact["version"]}'
        actual = run(f"integrity-{number}", [*npm, "view", spec, "dist.integrity", "--json", *npm_options,
                                             "--offline=false", "--prefer-offline=false"], environment=env)
        if json.loads(actual) != artifact["integrity"]:
            raise RuntimeError("published tarball integrity differs from fixture")
        run(f"cache-{number}", [*npm, "cache", "add", spec, *npm_options, "--ignore-scripts",
                               "--offline=false", "--prefer-offline=false"], environment=env)
    inventory = observe_npm_workspace(baseline)
    declarations = {(row["owner"], row["section"], row["name"]): row["declaredSpec"]
                    for row in inventory["declarations"] if row["section"] != "peerDependencies"}
    root, app, legacy, ui = ".", "packages/app", "packages/legacy", "packages/ui"
    rroot, rapp, rlegacy = ((owner, "dependencies", "react") for owner in (root, app, legacy))
    dapp, dlegacy = ((owner, "dependencies", "react-dom") for owner in (app, legacy))
    uapp = (app, "dependencies", "deploom-control-ui")
    fixed = {rroot: "19.0.0", rapp: "19.0.0"}
    topology = WorkspaceTopology(
        {(dapp, "react"): rapp, (dlegacy, "react"): rlegacy, (uapp, "react"): rroot},
        ((rroot, rapp), *((key,) for key in declarations if key not in fixed)),
    )
    domains = {rroot: ("19.0.0",), rapp: ("19.0.0",), dapp: ("18.3.1", "19.0.0")}
    plan = plan_workspace_upgrade(lock["packages"], declarations, domains, fixed, fixture["packuments"],
                                  environment={"node": node_version, "npm": npm_version}, topology=topology)
    if plan["status"] != "SAT_IN_MODELED_DOMAIN" or plan["assignment"][declaration_id(dapp)] != "19.0.0":
        raise RuntimeError("joint plan failed")
    if plan["assignment"][declaration_id(dlegacy)] != "18.3.1":
        raise RuntimeError("held legacy renderer changed")

    manifests = {owner: files[(Path(owner) / "package.json").as_posix()] for owner in (root, app, legacy, ui)}
    split_files = dict(files)
    for owner in fixed:
        path = (Path(owner[0]) / "package.json").as_posix()
        document = json.loads(split_files[path])
        document["dependencies"]["react"] = fixed[owner]
        split_files[path] = dump(document)
    place(split, split_files)
    run("independent-vector", [*npm, "install", "--package-lock-only", *WORKSPACE_NPM_POLICY, *npm_options],
        cwd=split, environment=env, expected="ERESOLVE")
    assignments = {tuple(json.loads(key)): value for key, value in plan["assignment"].items()}
    result = run_workspace_npm_preflight(manifests, (baseline / "package-lock.json").read_bytes(), assignments,
                                        frozenset(domains), artifacts=out / "native", cache=cache, registry=registry,
                                        reauthor=dlegacy)
    if result["status"] != "RESOLUTION_PASSED":
        raise RuntimeError(f'native preflight failed: {result["code"]}')
    resolver = Path(result["workspace"])
    check_pinned_artifacts(json.loads((resolver / "package-lock.json").read_bytes())["packages"],
                           fixture["artifacts"], registry)
    candidate = dict(files)
    for name in (*[path for path in files if path.endswith("package.json")], "package-lock.json"):
        candidate[name] = (resolver / name).read_bytes()
    old_app = candidate["packages/app/main.cjs"]
    if old_app.count(b"18.3.1") != 2:
        raise RuntimeError("unexpected source witness")
    candidate["packages/app/main.cjs"] = old_app.replace(b"18.3.1", b"19.0.0")
    changed = {name for name in baseline_hashes if hashlib.sha256(candidate[name]).hexdigest() != baseline_hashes[name]}
    if changed != {"package-lock.json", "package.json", "packages/app/package.json", "packages/app/main.cjs"}:
        raise RuntimeError("out-of-scope delta")
    place(trial, candidate)
    receipt = out / "verification.json"
    cli("verify", "verify", trial, "--expected-source-key", cli("source-key", "source-key", trial),
        "--expected-lock-sha256", result["candidateLockSha256"], "--expected-resolver-identity-sha256", result["resolverIdentitySha256"],
        "--baseline-lock", baseline / "package-lock.json", "--resolver-dir", resolver, "--cache", cache,
        "--output", receipt, "--check", "npm ls --all", "--check", "node packages/app/main.cjs",
        "--check", "node packages/legacy/main.cjs", "--install", "--registry", registry)
    proof = json.loads(receipt.read_bytes())
    if (proof["installation"]["status"] != "FRESH_COLD_FROZEN_INSTALL_OBSERVED"
            or proof["materializedDependencyObligations"]["workspaceLinkCount"] != 3
            or [row["status"] for row in proof["commandObservations"]] != ["PASS"] * 3
            or baseline_hashes != {name: sha(baseline / name) for name in baseline_hashes}):
        raise RuntimeError("installed verification differs from required profile")
    applied_sha256 = apply_verified_fixture(out, baseline, trial, resolver, receipt, cli)
    if baseline_hashes != {name: sha(baseline / name) for name in baseline_hashes}:
        raise RuntimeError("baseline changed during isolated application")
    report = {"coreRevision": json.loads(release_manifest_path().read_bytes())["core"]["revision"],
              "fixtureSha256": sha(source), "node": node_version, "npm": npm_version, "independentVector": "ERESOLVE",
              "jointPlan": plan["status"], "nativeLock": result["status"], "install": proof["installation"]["status"],
              "physicalLinks": 3, "npmLs": "PASS", "appSsr": "PASS", "legacySsr": "PASS",
              "candidateLockSha256": result["candidateLockSha256"], "verificationSha256": sha(receipt),
              "isolatedApply": "APPLIED", "applyReceiptSha256": applied_sha256,
              "browser": "NOT_RUN", "finalPolicy": "NOT_ASSESSED"}
    (out / "report.json").write_bytes(dump(report))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
