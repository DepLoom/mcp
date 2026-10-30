"""Offline negative controls for public Yellow exact-target demo, no network."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
from d1_yellow_policy_demo import check_policy_receipt, DemoFailure
from d1_mcp_value_preflight import EXPECTED_CORE_SHA, EXPECTED_SOURCE_SHA


def synthetic_receipt():
    scenarios = []
    for name, target, majors, goal, lag, ok in (
        ("ORIGINAL_EXACT_5_3_0", "5.3.0", [5], "blocked", 1, False),
        ("SEPARATE_MAJOR5_5_6_2", "5.6.2", [5], "blocked", 1, False),
        ("SEPARATE_CURRENT_POLICY_MIN_6_0_0", "6.0.0", [6], "feasible", 2, True),
    ):
        scenarios.append({
            "scenarioId": name, "expectedExactTarget": target,
            "approvedMajors": majors, "ok": True, "draftStatus": "DRAFT_READY",
            "authority": "PLANNING_ONLY", "verificationStatus": "NOT_VERIFIED",
            "explicitObjectiveEchoed": True, "chalkStatus": "PROPOSED",
            "chalkTarget": target, "scopeMutable": True,
            "companionStatus": "DEFERRED", "companionTarget": None,
            "coreRegistryReferenceMatch": True,
            "coreReferenceVersions": {"chalk":"6.0.1","is-number":"7.0.0"},
            "appliedHealthPolicy": {"targetLevel":"yellow","minLagOkPct":80},
            "registryLagRows": [
                {"package":"chalk","finalVersion":target,"mutable":True,
                 "healthExcluded":False,"lagOk":ok},
                {"package":"is-number","finalVersion":"7.0.0","mutable":False,
                 "healthExcluded":False,"lagOk":True},
            ],
            "finalTargetHealth": {"postPlanGoal":goal,"postPlanLagOk":lag,
                "postPlanScopeTotal":2,"postPlanPolicyRequired":2,
                "postPlanPolicyShortfall":2-lag,"postPlanSecurityUnknown":0},
            "explicitVersionAssessment": {"status":(
                "MATCHES_PUBLISHED_PLAN" if ok else "BLOCKED_BY_KNOWN_CONSTRAINTS"),
                "compatibility":"UNKNOWN",
                "publishedBlockerProvenance": [] if ok else [
                    {"code":"TARGET_POLICY_BLOCKED","lifecycle":"ACTIVE_FINAL"}]},
            "ordinaryPlanPolicy": {
                "requestedExactTarget":target,"publishedTarget":target,
                "allRequestedTargetsProposed":True,
                "authority":"PLANNING_ONLY","verificationStatus":"NOT_VERIFIED",
                "readoutHasPlanningBoundary":True,
                "perPackageLagEvidence":{
                    "availability":"UNAVAILABLE_IN_PUBLISHED_BLUEPRINT"},
                "totalBlockers":0 if ok else 1,
                "blockers":[] if ok else [
                    {"code":"TARGET_POLICY_BLOCKED","lifecycle":"ACTIVE_FINAL"}],
                "finalHealthPolicy":{
                    "postPlanGoal":goal,"postPlanLagOk":lag,
                    "postPlanScopeTotal":2,"postPlanPolicyRequired":2,
                    "postPlanPolicyShortfall":2-lag,"appliedMinLagOkPct":80}},
        })
    return {
        "modelCalls": 0, "coreRevision": EXPECTED_CORE_SHA,
        "frozenObservedSha256": EXPECTED_SOURCE_SHA,
        "wheelSha256": "1"*64, "sourceUnchanged": True,
        "gate":"GO","gateScope":"EXACT_VERSION_PLANNING_FEASIBILITY_ONLY",
        "registrySnapshots": {
            "chalk": {"status":"CAPTURED",
                "registryAuthority":"https://registry.npmjs.org",
                "metadataTimeDigestSha256":"a"*64,"lagMonths":12,
                "latestDistTag":"6.0.1",
                "minWithinLagWindowMetadata":"6.0.0","eligibleMajor5Count":0},
            "is-number": {"status":"CAPTURED",
                "registryAuthority":"https://registry.npmjs.org",
                "metadataTimeDigestSha256":"b"*64,"lagMonths":12,
                "latestDistTag":"7.0.0","minWithinLagWindowMetadata":"7.0.0"},
        },
        "requests": scenarios,
    }


class YellowPolicyNegativeTests(unittest.TestCase):
    def test_same_exact_chalk5_is_correctly_blocked_while_chalk6_feasible(self):
        report=check_policy_receipt(synthetic_receipt(),"1"*64,EXPECTED_SOURCE_SHA)
        self.assertEqual(
            ["blocked","blocked","feasible"],
            [x["finalPolicyGoal"] for x in report["scenarios"]])
        self.assertFalse(report["scenarios"][0]["requiresAuthorization"])
        self.assertTrue(report["scenarios"][1]["requiresAuthorization"])
        self.assertTrue(report["scenarios"][2]["requiresAuthorization"])
        self.assertEqual("SEPARATE_PUBLIC_EXPERIMENT_NOT_ORIGINAL_USER_TARGET",
                         report["scenarios"][2]["authorizationScope"])

    def test_immutable_is_number_cannot_disappear_from_denominator(self):
        data=synthetic_receipt()
        data["requests"][2]["registryLagRows"][1]["healthExcluded"]=True
        with self.assertRaisesRegex(DemoFailure,"DENOMINATOR_OR_SCOPE_CHANGED"):
            check_policy_receipt(data,"1"*64,EXPECTED_SOURCE_SHA)

    def test_chalk5_cannot_be_relabeled_policy_compliant(self):
        data=synthetic_receipt()
        data["requests"][1]["finalTargetHealth"]["postPlanGoal"]="feasible"
        with self.assertRaisesRegex(DemoFailure,"CORE_POLICY_VERDICT_MISMATCH"):
            check_policy_receipt(data,"1"*64,EXPECTED_SOURCE_SHA)

    def test_unauthorized_major_or_exact_target_is_rejected(self):
        data=synthetic_receipt()
        data["requests"][2]["approvedMajors"]=[5]
        with self.assertRaisesRegex(DemoFailure,"EXACT_TARGET_OR_SCOPE_CHANGED"):
            check_policy_receipt(data,"1"*64,EXPECTED_SOURCE_SHA)

if __name__=="__main__":
    unittest.main()
