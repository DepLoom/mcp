#!/usr/bin/env python3
"""D1 Yellow: original exact target stays BLOCKED; separate Chalk 6 demo.

PUBLIC frozen Chalk only. The standalone command invokes the installed-wheel
D1 stdio preflight when --receipt is not supplied. A distinct evaluator-only
copy upgrades Chalk to the metadata-minimum 6.0.0 and independently runs
clean npm ci/build/test/multi-input runtime. No agent/model, no input rewrites,
no raw network/agent transcripts, no claims about production projects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from correctness_oracle import (
    CHALK_RUNTIME_ORACLE, _chalk_oracle_env, npm_bin, read_json, source_files,
)
from d1_mcp_value_preflight import EXPECTED_CORE_SHA, EXPECTED_SOURCE_SHA, source_sha

REGISTRY = "https://registry.npmjs.org/"
SCENARIOS = (
    ("ORIGINAL_EXACT_5_3_0", "5.3.0", [5], "blocked", 1, False),
    ("SEPARATE_MAJOR5_5_6_2", "5.6.2", [5], "blocked", 1, False),
    ("SEPARATE_CURRENT_POLICY_MIN_6_0_0", "6.0.0", [6], "feasible", 2, True),
)
ALLOW_CHANGE = frozenset(("package.json", "package-lock.json", "src/format.cjs"))


class DemoFailure(Exception):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise DemoFailure(code)


def check_policy_receipt(data: dict, wheel_sha: str, seed_sha: str) -> dict:
    """Fail closed on provenance, mutated scope or a false policy-OK claim."""
    require(data.get("modelCalls") == 0, "MODEL_INVOCATION_NOT_ZERO")
    require(data.get("coreRevision") == EXPECTED_CORE_SHA, "CORE_REVISION_DRIFT")
    require(data.get("frozenObservedSha256") == EXPECTED_SOURCE_SHA == seed_sha,
            "BASELINE_SOURCE_DRIFT")
    require(data.get("wheelSha256") == wheel_sha, "INSTALLED_WHEEL_MISMATCH")
    require(data.get("sourceUnchanged") is True, "PLANNER_CHANGED_FROZEN_SOURCE")
    require(data.get("gate") == "GO" and data.get("gateScope") ==
            "EXACT_VERSION_PLANNING_FEASIBILITY_ONLY", "ORIGINAL_PLANNING_GATE_NO_GO")
    snapshots = data.get("registrySnapshots") or {}
    chalk = snapshots.get("chalk") or {}
    number = snapshots.get("is-number") or {}
    require(all(s.get("status") == "CAPTURED" for s in (chalk, number)),
            "PUBLIC_REGISTRY_SNAPSHOT_MISSING")
    require(all(s.get("registryAuthority") == REGISTRY.rstrip("/") for s in (chalk, number)),
            "UNEXPECTED_REGISTRY_AUTHORITY")
    require(all(re.fullmatch(r"[0-9a-f]{64}", s.get("metadataTimeDigestSha256") or "")
                for s in (chalk, number)), "REGISTRY_SNAPSHOT_DIGEST_MISSING")
    require(chalk.get("minWithinLagWindowMetadata") == "6.0.0" and
            chalk.get("eligibleMajor5Count") == 0, "MIN_YELLOW_TARGET_HAS_DRIFTED")
    require(number.get("minWithinLagWindowMetadata") == "7.0.0",
            "IS_NUMBER_LAG_REFERENCE_DRIFT")
    require(chalk.get("latestDistTag") == "6.0.1" and
            number.get("latestDistTag") == "7.0.0", "REGISTRY_LATEST_CHANGED")
    require(all(s.get("lagMonths") == 12 for s in (chalk, number)),
            "LAG_POLICY_WINDOW_CHANGED")
    scenarios = {r.get("scenarioId"): r for r in data.get("requests") or []
                 if isinstance(r, dict)}
    observed = []
    for sid, target, approved, goal, count, should_pass in SCENARIOS:
        r = scenarios.get(sid) or {}
        health = r.get("finalTargetHealth") or {}
        assessment = r.get("explicitVersionAssessment") or {}
        require(r.get("ok") is True and r.get("draftStatus") == "DRAFT_READY" and
                r.get("authority") == "PLANNING_ONLY" and
                r.get("verificationStatus") == "NOT_VERIFIED" and
                r.get("explicitObjectiveEchoed") is True, "MCP_EXACT_CONTRACT_FAILED")
        require(r.get("expectedExactTarget") == r.get("chalkTarget") == target and
                r.get("chalkStatus") == "PROPOSED" and r.get("approvedMajors") == approved and
                r.get("scopeMutable") is True and r.get("companionStatus") == "DEFERRED" and
                r.get("companionTarget") is None, "EXACT_TARGET_OR_SCOPE_CHANGED")
        require(r.get("appliedHealthPolicy") ==
                {"targetLevel": "yellow", "minLagOkPct": 80}, "YELLOW_POLICY_CHANGED")
        require(r.get("coreRegistryReferenceMatch") is True and
                r.get("coreReferenceVersions") ==
                {"chalk": chalk["latestDistTag"], "is-number": number["latestDistTag"]},
                "PLAN_REGISTRY_REFERENCE_DRIFT")
        rows = {row["package"]: row for row in r.get("registryLagRows") or []
                if isinstance(row, dict) and row.get("package") in ("chalk", "is-number")}
        require(set(rows) == {"chalk", "is-number"} and
                rows["chalk"].get("finalVersion") == target and
                rows["is-number"].get("finalVersion") == "7.0.0" and
                rows["chalk"].get("mutable") is True and
                rows["is-number"].get("mutable") is False and
                rows["chalk"].get("healthExcluded") is False and
                rows["is-number"].get("healthExcluded") is False, "DENOMINATOR_OR_SCOPE_CHANGED")
        require(rows["chalk"].get("lagOk") is should_pass and
                rows["is-number"].get("lagOk") is True, "PER_ROW_LAG_EVIDENCE_MISMATCH")
        require(health.get("postPlanGoal") == goal and
                health.get("postPlanLagOk") == count and
                health.get("postPlanScopeTotal") == 2 and
                health.get("postPlanPolicyRequired") == 2 and
                health.get("postPlanPolicyShortfall") == 2-count and
                health.get("postPlanSecurityUnknown") == 0, "CORE_POLICY_VERDICT_MISMATCH")
        expected_verdict = "MATCHES_PUBLISHED_PLAN" if should_pass else "BLOCKED_BY_KNOWN_CONSTRAINTS"
        require(assessment.get("status") == expected_verdict and
                assessment.get("compatibility") == "UNKNOWN",
                "ASSIGNMENT_CHECK_WRONG_VERDICT")
        # The regular installed-wheel get_upgrade_plan response MUST provide
        # policy and exact-target status itself, without a D1-specific oracle.
        ordinary = r.get("ordinaryPlanPolicy") or {}
        final = ordinary.get("finalHealthPolicy") or {}
        require(ordinary.get("requestedExactTarget") ==
                ordinary.get("publishedTarget") == target and
                ordinary.get("allRequestedTargetsProposed") is True and
                ordinary.get("authority") == "PLANNING_ONLY" and
                ordinary.get("verificationStatus") == "NOT_VERIFIED" and
                ordinary.get("readoutHasPlanningBoundary") is True and
                ordinary.get("perPackageLagEvidence", {}).get("availability") ==
                    "UNAVAILABLE_IN_PUBLISHED_BLUEPRINT",
                "ORDINARY_MCP_PLAN_POLICY_NOT_VISIBLE")
        require(final.get("postPlanGoal") == goal and
                final.get("postPlanLagOk") == count and
                final.get("postPlanScopeTotal") == 2 and
                final.get("postPlanPolicyRequired") == 2 and
                final.get("postPlanPolicyShortfall") == 2-count and
                final.get("appliedMinLagOkPct") == 80,
                "ORDINARY_MCP_POLICY_DISAGREES_WITH_CORE")
        if should_pass:
            require(ordinary.get("totalBlockers") == 0,
                    "COMPLIANT_PLAN_HAS_PUBLISHED_BLOCKER")
        else:
            require(ordinary.get("totalBlockers", 0) > 0 and any(
                b.get("code") == "TARGET_POLICY_BLOCKED" and
                b.get("lifecycle") == "ACTIVE_FINAL"
                for b in ordinary.get("blockers") or []),
                "ORDINARY_MCP_DROPPED_POLICY_BLOCKER")

        if not should_pass:
            require(any(b.get("code") == "TARGET_POLICY_BLOCKED" and
                        b.get("lifecycle") == "ACTIVE_FINAL"
                        for b in assessment.get("publishedBlockerProvenance") or []),
                    "MISSING_REAL_POLICY_BLOCKER")
        observed.append({"id": sid, "exactTarget": target, "approvedMajors": approved,
            "finalPolicyGoal": goal, "lagOk": count, "scopeTotal": 2,
            "assignment": expected_verdict, "chalkLagOk": should_pass,
            "isNumberLagOk": True,
            "requiresAuthorization": sid != "ORIGINAL_EXACT_5_3_0",
            "authorizationScope": (
                "SEPARATE_PUBLIC_EXPERIMENT_NOT_ORIGINAL_USER_TARGET"
                if sid != "ORIGINAL_EXACT_5_3_0" else "ORIGINAL_USER_AUTHORIZED_EXACT"),
            "claim": "PLANNING_ONLY_NOT_VERIFIED"})
    return {"registrySnapshots": snapshots, "scenarios": observed,
            "sourceSha256": seed_sha, "wheelSha256": wheel_sha,
            "coreRevision": EXPECTED_CORE_SHA}


def _command(argv: list[str], cwd: Path, env: dict, timeout: int = 100) -> bool:
    # Never print raw npm output, URLs or user/environment data.
    try:
        r = subprocess.run(argv, cwd=cwd, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=timeout, check=False)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def runtime_oracle(frozen: Path) -> dict:
    """Independent evaluator-only synthetic target. P2 gold remains untouched."""
    result = {"status": "FAIL", "attribution": "EVALUATOR_ONLY_NOT_AGENT",
              "scenario": "SEPARATE_CURRENT_POLICY_MIN_6_0_0",
              "checks": {}, "installed": None, "error": None}
    with tempfile.TemporaryDirectory(prefix="yellow-public-only-") as temp:
        root = Path(temp)
        candidate, verified = root/"candidate", root/"independent-verify"
        shutil.copytree(frozen, candidate)
        start_files = source_files(frozen)
        package = read_json(candidate/"package.json")
        require(package.get("dependencies") ==
                {"chalk": "4.1.2", "is-number": "7.0.0"}, "P2_SEED_DEPENDENCIES_CHANGED")
        package["dependencies"]["chalk"] = "6.0.0"
        (candidate/"package.json").write_text(
            json.dumps(package, indent=2) + "\n", encoding="utf-8")
        (candidate/"src"/"format.cjs").write_text(
            "'use strict';\n"
            "module.exports = async name => {\n"
            "  const {default: chalk} = await import('chalk');\n"
            "  return chalk.yellow('READY ' + name);\n"
            "};\n", encoding="utf-8")
        # Only these three files may differ; do not edit README or P2 scripts.
        env = _chalk_oracle_env(root)
        npm = npm_bin()
        result["checks"]["lockGeneration"] = _command(
            npm + ["install", "--package-lock-only", "--ignore-scripts",
                   "--no-audit", "--no-fund", "--registry", REGISTRY],
            candidate, env)
        require(result["checks"]["lockGeneration"], "PUBLIC_NPM_LOCK_GENERATION_FAILED")
        after = source_files(candidate)
        require(set(start_files) == set(after) and
                all(start_files[k] == after[k] for k in start_files if k not in ALLOW_CHANGE),
                "FROZEN_P2_FILES_MODIFIED")
        manifest = read_json(candidate/"package.json")
        lock = read_json(candidate/"package-lock.json")
        packages = lock.get("packages") or {}
        require(lock.get("lockfileVersion") == 3 and
                packages.get("",{}).get("dependencies") ==
                {"chalk":"6.0.0","is-number":"7.0.0"} and
                set(packages) == {"","node_modules/chalk","node_modules/is-number"},
                "SYNTHETIC_LOCK_SCOPE_INVALID")
        require(packages["node_modules/chalk"].get("version") == "6.0.0" and
                packages["node_modules/is-number"] ==
                read_json(frozen/"package-lock.json")["packages"]["node_modules/is-number"],
                "LOCK_DEPENDENCY_OR_IMMUTABLE_CHANGED")
        for name, version in (("chalk","6.0.0"),("is-number","7.0.0")):
            row=packages["node_modules/"+name]
            require(row.get("resolved") == f"{REGISTRY}{name}/-/{name}-{version}.tgz" and
                    re.fullmatch(r"sha512-[A-Za-z0-9+/]+={0,2}",
                                 row.get("integrity") or "") is not None,
                    "LOCK_TARBALL_OR_INTEGRITY_REJECTED")
        shutil.copytree(candidate, verified, ignore=shutil.ignore_patterns("node_modules","dist"))
        frozen_verified=source_files(verified)
        commands=(
            ("cleanInstall", npm+["ci","--ignore-scripts","--no-audit","--no-fund",
                                  "--registry",REGISTRY]),
            ("build",npm+["run","build","--ignore-scripts=false"]),
            ("test",npm+["test","--ignore-scripts=false"]),
            ("runtimeMultiInput",["node","-e",CHALK_RUNTIME_ORACLE]),
        )
        for label,argv in commands:
            result["checks"][label]=_command(argv,verified,env)
            require(result["checks"][label], "INDEPENDENT_"+label.upper()+"_FAILED")
        require(source_files(verified) == frozen_verified, "INDEPENDENT_TEST_MUTATED_SOURCE")
        # Real positive regression for the NEW A/B scorer, on the SAME
        # disposable evaluator-authored candidate. It reruns P2's independent
        # static/lock provenance and clean npm/build/test/multi-input runtime.
        # Neither the scorer nor this reference source enters the agent seed.
        from d1_chalk6_scorer import verify_chalk6_frozen
        scored=verify_chalk6_frozen(frozen,candidate,EXPECTED_SOURCE_SHA)
        result["chalk6BenchmarkScorer"]={
            "status":scored.get("status"),"reason":scored.get("reason"),
            "authority":"INDEPENDENT_EVALUATOR_NOT_AGENT_CLAIM",
            "target":"6.0.0","isNumberImmutable":"7.0.0"}
        require(scored.get("status")=="PASS",
                "CHALK6_BENCHMARK_SCORER_POSITIVE_FAILED")
        result["installed"]={"chalk":"6.0.0","is-number":"7.0.0"}
        result["status"]="PASS"
    return result


def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frozen",type=Path,required=True)
    ap.add_argument("--wheel",type=Path,required=True)
    ap.add_argument("--receipt",type=Path)
    ap.add_argument("--report",type=Path,required=True)
    args=ap.parse_args()
    result={"status":"FAIL","modelCalls":0,"codexSessions":0,
            "originalPolicyVerdict":"BLOCKED_EXPECTED",
            "compliantAlternative":"NOT_CHECKED",
            "reason":None}
    t=time.monotonic()
    try:
        frozen=args.frozen.resolve(strict=True)
        wheel=args.wheel.resolve(strict=True)
        require(source_sha(frozen)==EXPECTED_SOURCE_SHA, "FROZEN_P2_SOURCE_HASH_CHANGED")
        wheel_sha=hashlib.sha256(wheel.read_bytes()).hexdigest()
        if args.receipt:
            receipt=json.loads(args.receipt.read_text(encoding="utf-8"))
        else:
            with tempfile.TemporaryDirectory(prefix="yellow-mcp-") as out:
                path=Path(out)/"d1-mcp-value.json"
                success=_command([sys.executable,str(Path(__file__).with_name(
                    "d1_mcp_value_preflight.py")), "--frozen",str(frozen),
                    "--wheel",str(wheel), "--report",str(path),"--require-go"],
                    Path(__file__).resolve().parent.parent,
                    os.environ.copy(), timeout=360)
                require(success and path.is_file(), "INSTALLED_WHEEL_D1_PREFLIGHT_FAILED")
                receipt=json.loads(path.read_text(encoding="utf-8"))
        proof=check_policy_receipt(receipt,wheel_sha,source_sha(frozen))
        result.update(proof)
        runtime=runtime_oracle(frozen)
        result["independentRuntime"]=runtime
        require(runtime.get("status")=="PASS", "ALTERNATE_RUNTIME_NOT_VERIFIED")
        require(source_sha(frozen)==EXPECTED_SOURCE_SHA, "FROZEN_SOURCE_CHANGED_AFTER_TEST")
        result.update(status="PASS",compliantAlternative="POLICY_FEASIBLE_AND_RUNTIME_PASS")
    except DemoFailure as exc:
        result["reason"]=str(exc)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        result["reason"]="INFRASTRUCTURE_"+type(exc).__name__
    result["wallSeconds"]=round(time.monotonic()-t,3)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"reason":result["reason"],
                      "modelCalls":0,"compliantAlternative":result["compliantAlternative"]},
                     sort_keys=True))
    return 0 if result["status"]=="PASS" else 2


if __name__=="__main__":
    raise SystemExit(main())
