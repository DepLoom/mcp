"""Native admission steps for the diagnostic live Yellow workspace."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from deploom_mcp.distribution import resolve_core_dir

sys.path.insert(0, str(resolve_core_dir()))
try:
    from draft_npm_preflight import _npm_command
    from workspace_npm_preflight import WORKSPACE_NPM_POLICY, isolated_workspace_npm_environment
finally:
    sys.path.pop(0)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv, cwd, env=None):
    result = subprocess.run([str(arg) for arg in argv], cwd=cwd, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=110)
    if result.returncode:
        raise RuntimeError(f"{argv[2]} exited {result.returncode}: {result.stdout[-1400:]} {result.stderr[-1400:]}")
    return result.stdout.strip()


def prepare_runtime(root, baseline, registry, selected):
    cache = root / "cache"
    cache.mkdir()
    for name in ("user.npmrc", "global.npmrc"):
        (root / name).touch(exist_ok=False)
    npm = _npm_command()
    env = isolated_workspace_npm_environment(os.environ)
    assert run([npm[0], "--version"], root, env) == "v24.18.0"
    assert run([*npm, "--version"], root, env) == "11.16.0"
    options = ["--registry", registry, "--cache", cache, "--userconfig", root / "user.npmrc",
               "--globalconfig", root / "global.npmrc", "--offline=false", "--prefer-offline=false"]
    policy = [flag for flag in WORKSPACE_NPM_POLICY if not flag.startswith(("--offline=", "--prefer-offline="))]
    run([*npm, "install", "--package-lock-only", *policy, *options], baseline, env)
    run([*npm, "cache", "add", f"is-number@{selected}", "--ignore-scripts", *options], baseline, env)
    (baseline / "packages/app/check.cjs").write_text(
        "const assert = require('node:assert/strict');\n"
        "const isNumber = require('is-number');\n"
        f"assert.equal(require('is-number/package.json').version, {json.dumps(selected)});\n"
        "for (const value of [0, 42, '3.1']) assert.equal(isNumber(value), true);\n"
        "for (const value of ['bad', null, Infinity]) assert.equal(isNumber(value), false);\n",
        encoding="utf-8",
    )
    assert not (baseline / "node_modules").exists()
    return cache


def accept_workspace(root, baseline, plan, cache, registry, selected):
    pm = plan["packageManagerValidation"]
    assert (pm["status"], pm["search"]["attemptCount"]) == ("RESOLUTION_PASSED", 1), pm
    resolver = root / "native" / pm["resolverArtifactId"]
    lock = resolver / "package-lock.json"
    assert digest(lock) == pm["candidateLockSha256"]
    assert json.loads(lock.read_bytes())["packages"]["node_modules/is-number"]["version"] == selected
    trial = root / "trial"
    paths = ("package.json", "package-lock.json", "packages/app/package.json", "packages/app/check.cjs")
    for name in paths:
        (trial / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile((baseline if name.endswith(".cjs") else resolver) / name, trial / name)

    def cli(*args):
        return run([sys.executable, "-m", "deploom_mcp.workspace_acceptance", *args], root)

    evidence = ["--baseline-lock", baseline / "package-lock.json", "--resolver-dir", resolver,
                "--expected-lock-sha256", pm["candidateLockSha256"],
                "--expected-resolver-identity-sha256", pm["resolverIdentitySha256"]]
    checks = ["--expected-source-key", cli("source-key", trial),
              "--check", "npm ls --all", "--check", "node packages/app/check.cjs"]
    receipt = root / "verify.json"
    cli("verify", trial, *evidence, *checks, "--cache", cache, "--output", receipt, "--install", "--registry", registry)
    proof = json.loads(receipt.read_bytes())
    assert [row["status"] for row in proof["commandObservations"]] == ["PASS", "PASS"]
    terminal = json.loads(cli("verify-status", receipt))
    policy = root / "yellow.json"
    policy.write_text(json.dumps({"targetLevel": "yellow", "minLagOkPct": 80, "lagPolicyMonths": 12}),
                      encoding="utf-8")
    audit = json.loads(cli("audit-candidate", trial, *evidence, "--registry", registry,
                           "--output-dir", root / "audit", "--policy-json", policy))
    assert (audit["finalPolicy"], audit["security"]) == ("PASS", "NO_KNOWN_FINDINGS"), audit
    delivery = root / "delivery"
    delivered = json.loads(cli("deliver-policy", trial, *evidence, *checks, "--attempt-receipt", receipt,
        "--expected-receipt-sha256", terminal["receiptSha256"], "--baseline-project", baseline,
        "--expected-baseline-source-key", cli("source-key", baseline), "--destination", delivery,
        "--policy-json", policy, "--audit-report", audit["artifacts"]["report"],
        "--expected-audit-sha256", audit["artifacts"]["sha256"]))
    assert (delivered["status"], delivered["installation"]) == (
        "POLICY_WORKSPACE_SOURCE_DELIVERED", "FRESH_COLD_FROZEN_INSTALL_OBSERVED")
    assert delivered["policyEvidence"]["lagConfirmed"] == delivered["policyEvidence"]["lagDenominator"] == 1
    assert {row["path"] for row in delivered["exactFileDelta"]} == {"package.json", "package-lock.json"}
    applied = root / "applied"
    delivery_sha = digest(delivery / "receipt.json")
    result = json.loads(cli("apply-delivery", baseline, "--delivery", delivery, "--destination", applied,
        "--expected-delivery-sha256", delivery_sha, "--goal", "policy", "--expected-profile-sha256",
        proof["finalVerificationProfileDigest"], "--expected-policy-sha256", delivered["policyEvidence"]["policySha256"]))
    assert (result["state"], result["installation"]) == ("APPLIED", "NOT_RUN_IN_DELIVERED_COPY")
    tree = applied / "project"
    assert not (tree / "node_modules").exists()
    assert all(digest(tree / name) == digest(trial / name) for name in paths)
    return {"mode": "LIVE_WORKSPACE_YELLOW_ACCEPTANCE", "requestId": plan["requestId"],
            "plannedVersion": selected, "candidateId": plan["candidateId"], "candidateLockSha256": digest(lock),
            "verificationReceiptSha256": terminal["receiptSha256"], "auditSha256": audit["artifacts"]["sha256"],
            "deliveryReceiptSha256": delivery_sha, "applyReceiptSha256": digest(applied / "terminal.json"),
            "install": delivered["installation"], "npmLs": "PASS", "runtime": "PASS", "policy": "PASS",
            "policyEvidence": delivered["policyEvidence"], "isolatedApply": result["state"]}
