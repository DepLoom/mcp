"""Ordinary MCP compact answer: source-backed exact target vs final health."""
from __future__ import annotations

import copy
import json
import unittest

from deploom_mcp.blueprint import BlueprintPolicy, transcode_upgrade_blueprint
from deploom_mcp.decisions import enrich_decisions
from deploom_mcp.server import compact_blueprint


def published_exact(*, target: str = "5.3.0", goal: str = "blocked",
                    partial: bool = False, health_missing: bool = False):
    numbers = {
        "postPlanTargetLevel": "yellow", "postPlanScopeTotal": 2,
        "postPlanLagOk": 2 if goal == "feasible" else 1,
        "postPlanPolicyRequired": 2,
        "postPlanPolicyShortfall": 0 if goal == "feasible" else 1,
        "postPlanLagOkPct": 100.0 if goal == "feasible" else 50.0,
        "postPlanSecurityKnown": 2, "postPlanSecurityUnknown": 0,
        "postPlanSecurityTotal": 2, "postPlanCritical": 0,
        "postPlanHigh": 0, "postPlanModerate": 0, "postPlanLow": 0,
        "postPlanGoal": goal,
    }
    if health_missing:
        numbers = {}
    rows = [
        {"project": "app", "package": "chalk", "kind": "runtime",
         "current": "4.1.2", "requestedSpec": "4.1.2",
         "target": target, "latest": "6.0.1", "status": "proposed",
         "group": 1, "reason": "EXPLICIT_USER_TARGET"},
        {"project": "app", "package": "is-number", "kind": "runtime",
         "current": "7.0.0", "requestedSpec": "7.0.0",
         "target": None, "latest": "7.0.0", "status": "deferred",
         "group": 1, "reason": "Immutable companion"},
    ]
    plan = {"proposals": [{"project": "app",
             "rows": rows, "health": {"total": 2, **numbers}}],
            "counts": {"total": 2, "proposed": 1},
            "conflicts": [], "unknowns": [], "unresolved": []}
    metadata = {"status": "DRAFT_PARTIAL" if partial else "DRAFT_READY",
                "generatedAt": None, "runId": "exact-policy-test",
                "verificationStatus": "NOT_VERIFIED",
                "authority": "PLANNING_ONLY", "compatibility": "UNKNOWN",
                "metadata": {"metadataKnown": 2, "metadataTotal": 2,
                             "securityKnown": 2, "securityTotal": 2,
                             **numbers}}
    bp = transcode_upgrade_blueprint(
        plan, metadata, project_path="/synthetic/chalk",
        project_name="app", policy=BlueprintPolicy(),
        artifacts={}, blueprint_path="/artifacts/upgrade-blueprint.json")
    bp["policy"]["targetLevel"] = "yellow"
    bp["policy"]["minLagOkPct"] = 80
    bp["policy"]["decisionContract"] = {
        "version": 2, "targetMode": "pinned",
        "objective": "explicit-version-targets",
        "mutablePackages": ["chalk"],
        "approvedVersionLines": {"chalk": [int(target.split(".")[0])]},
        "exactTargets": {"chalk": target},
    }
    bp["workspaceCoverage"] = {"mode": "SINGLE_MANIFEST_PLANNING",
        "jointAssignment": "NOT_COMPUTED",
        "reconciliationRequired": False, "discoveryComplete": True}
    enrich_decisions(bp)
    return bp


class PolicyAwareExactSummaryTests(unittest.TestCase):
    def test_original_exact_proposed_but_yellow_blocked_preserves_provenance(self):
        bp = published_exact()
        result = compact_blueprint(bp)
        summary = result["summary"]["exactTargetPolicy"]
        self.assertTrue(summary["allRequestedTargetsProposed"])
        self.assertEqual(summary["requestedExactTargets"], {"chalk": "5.3.0"})
        self.assertEqual(summary["publishedAssignments"][0]["publishedTarget"], "5.3.0")
        self.assertEqual(summary["publishedAssignments"][0]["rowStatus"], "PROPOSED")
        self.assertTrue(summary["publishedAssignments"][0]["matchesExactTarget"])
        health = summary["finalHealthPolicy"]
        self.assertEqual(health["postPlanGoal"], "blocked")
        self.assertEqual(health["postPlanLagOk"], 1)
        self.assertEqual(health["postPlanScopeTotal"], 2)
        self.assertEqual(health["postPlanPolicyRequired"], 2)
        self.assertEqual(health["postPlanPolicyShortfall"], 1)
        self.assertEqual(health["appliedMinLagOkPct"], 80)
        self.assertEqual(health["evidenceRef"], "#/targetAssessment/aggregate")
        self.assertEqual(health["postPlanGoal"],
                         result["targetAssessment"]["aggregate"]["postPlanGoal"])
        self.assertEqual(summary["totalBlockers"], 1)
        self.assertEqual(summary["blockers"][0]["code"], "TARGET_POLICY_BLOCKED")
        self.assertEqual(summary["blockers"][0]["lifecycle"], "ACTIVE_FINAL")
        self.assertEqual(summary["blockers"][0]["scope"], "project")
        self.assertEqual(summary["blockers"][0]["project"], "app")
        self.assertEqual(summary["blockers"][0]["evidenceRef"],
                         "#/targetAssessment/projects/app")
        self.assertIsNone(summary["blockers"][0]["package"])
        self.assertEqual(summary["perPackageLagEvidence"]["availability"],
                         "UNAVAILABLE_IN_PUBLISHED_BLUEPRINT")
        self.assertIn("PLANNING_ONLY / NOT_VERIFIED", summary["readout"])
        self.assertIn("blocked", summary["readout"])
        self.assertEqual(result["status"]["verificationStatus"], "NOT_VERIFIED")
        self.assertEqual(result["status"]["compatibility"], "UNKNOWN")
        self.assertNotIn("recommendedAlternative", summary)
        self.assertNotIn("runtimeVerified", summary)

    def test_separate_six_feasible_is_only_planning_and_no_alternative(self):
        result = compact_blueprint(published_exact(target="6.0.0", goal="feasible"))
        summary = result["summary"]["exactTargetPolicy"]
        self.assertTrue(summary["allRequestedTargetsProposed"])
        self.assertEqual(summary["publishedAssignments"][0]["publishedTarget"], "6.0.0")
        self.assertEqual(summary["finalHealthPolicy"]["postPlanGoal"], "feasible")
        self.assertEqual(summary["finalHealthPolicy"]["postPlanLagOk"], 2)
        self.assertEqual(summary["finalHealthPolicy"]["postPlanPolicyShortfall"], 0)
        self.assertEqual(summary["totalBlockers"], 0)
        self.assertEqual(summary["blockers"], [])
        self.assertIn("NOT_VERIFIED", summary["readout"])
        self.assertFalse(any("VERIFIED" == v for v in summary.values()))

    def test_legacy_and_v1_no_new_summary_or_status_change(self):
        bp = published_exact()
        bp["policy"]["decisionContract"].update(
            version=1, objective="minimum-change-to-policy")
        bp["policy"]["decisionContract"].pop("exactTargets", None)
        enrich_decisions(bp)
        result = compact_blueprint(bp)
        self.assertNotIn("exactTargetPolicy", result["summary"])
        self.assertEqual(result["status"]["verificationStatus"], "NOT_VERIFIED")

    def test_partial_or_wrong_target_cannot_claim_published_exact_match(self):
        bp = published_exact(partial=True)
        self.assertFalse(compact_blueprint(bp)["summary"]["exactTargetPolicy"][
                         "allRequestedTargetsProposed"])
        bp = published_exact()
        bp["dependencies"][0]["target"] = "5.6.2"
        enrich_decisions(bp)
        summary = compact_blueprint(bp)["summary"]["exactTargetPolicy"]
        self.assertFalse(summary["allRequestedTargetsProposed"])
        self.assertFalse(summary["publishedAssignments"][0]["matchesExactTarget"])
        self.assertEqual(summary["requestedExactTargets"], {"chalk": "5.3.0"})

    def test_missing_core_health_is_unknown_not_zero_or_pass(self):
        summary = compact_blueprint(published_exact(health_missing=True))[
            "summary"]["exactTargetPolicy"]
        health = summary["finalHealthPolicy"]
        self.assertIsNone(health["postPlanGoal"])
        self.assertIsNone(health["postPlanLagOk"])
        self.assertIsNone(health["postPlanPolicyShortfall"])
        self.assertIn("unknown", summary["readout"])
        self.assertTrue(any(x["code"] == "TARGET_ASSESSMENT_UNKNOWN"
                            for x in compact_blueprint(published_exact(health_missing=True))[
                                "decisionEvidence"]))

    def test_clipped_blocker_never_disappears_as_zero(self):
        bp = published_exact()
        full = compact_blueprint(bp)
        clipped = compact_blueprint(bp, max_inline_evidence=0, max_inline_rows=0)
        summary = clipped["summary"]["exactTargetPolicy"]
        self.assertEqual(summary["totalBlockers"], full["summary"]["exactTargetPolicy"]["totalBlockers"])
        self.assertTrue(summary["blockersClipped"])
        self.assertEqual(summary["blockers"], [])
        self.assertEqual(summary["totalPublishedAssignments"], 1)
        self.assertTrue(summary["assignmentsClipped"])
        self.assertTrue(clipped["decisionCompleteness"]["requiresArtifactRead"])
        self.assertTrue(clipped["truncation"]["truncated"])
        self.assertEqual(clipped["truncation"]["bytes"],
                         len(json.dumps(clipped, ensure_ascii=False, indent=2).encode("utf-8")))

    def test_active_final_peer_blocker_survives_without_reinterpreting_solver(self):
        bp = published_exact(target="6.0.0", goal="feasible")
        bp["conflicts"] = [{
            "active": True, "stage": "final", "package": "chalk",
            "notes": ["PEER_RESOLUTION_DEFERRED: peer candidate excluded"],
        }]
        enrich_decisions(bp)
        summary = compact_blueprint(bp)["summary"]["exactTargetPolicy"]
        self.assertEqual(summary["finalHealthPolicy"]["postPlanGoal"], "feasible")
        self.assertEqual(summary["totalBlockers"], 1)
        self.assertEqual(summary["blockers"][0]["code"], "PEER_CONSTRAINT")
        self.assertEqual(summary["blockers"][0]["evidenceRef"], "#/conflicts/0")
        self.assertEqual(summary["blockers"][0]["lifecycle"], "ACTIVE_FINAL")
        self.assertTrue(summary["allRequestedTargetsProposed"])

if __name__ == "__main__":
    unittest.main()
