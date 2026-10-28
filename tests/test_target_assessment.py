"""Final-target policy facts must survive the public compact boundary."""
from __future__ import annotations

import copy
import json
import unittest

from deploom_mcp.blueprint import BlueprintPolicy, _validate_blueprint_schema, transcode_upgrade_blueprint
from deploom_mcp.decisions import enrich_decisions
from deploom_mcp.server import compact_blueprint


def health(**changes):
    value = {"postPlanTargetLevel": "yellow", "postPlanSecurityKnown": 1,
             "postPlanSecurityUnknown": 0, "postPlanSecurityTotal": 1,
             "postPlanCritical": 0, "postPlanHigh": 0, "postPlanModerate": 0,
             "postPlanLow": 0, "postPlanGoal": "feasible",
             "postPlanPolicyMaxCritical": 0, "postPlanPolicyMaxHigh": 1,
             "postPlanPolicyShortfall": 0}
    value.update(changes)
    return value


def blueprint(projects=None, aggregate=None):
    projects = projects if projects is not None else {"app": health()}
    aggregate = aggregate if aggregate is not None else health()
    proposals = [{"project": project, "health": {"total": 1, **value}, "rows": [
        {"project": project, "package": "sample-" + str(index), "kind": "runtime",
         "current": "1.0.0", "target": "1.5.0", "status": "proposed",
         "reason": "Latest candidate in the approved major", "group": 1}
    ]} for index, (project, value) in enumerate(projects.items())]
    manifest = {"status": "DRAFT_READY", "runId": "target-fixture", "generatedAt": None,
                "verificationStatus": "NOT_VERIFIED", "authority": "PLANNING_ONLY",
                "compatibility": "UNKNOWN", "metadata": {
                    "metadataKnown": len(projects), "metadataTotal": len(projects),
                    "securityKnown": len(projects), "securityTotal": len(projects),
                    "insufficientData": False, **aggregate}}
    result = transcode_upgrade_blueprint(
        {"proposals": proposals, "counts": {"total": len(projects), "proposed": len(projects)},
         "conflicts": [], "unknowns": [], "unresolved": []},
        manifest, project_path="/synthetic/app", project_name="app", policy=BlueprintPolicy(),
        artifacts={"plan": "/artifacts/plan.json"}, blueprint_path="/artifacts/blueprint.json")
    result["workspaceCoverage"] = {"mode": "SINGLE_MANIFEST_PLANNING",
        "jointAssignment": "NOT_COMPUTED", "workspaceGraphCoverage": "SINGLE_PROJECT",
        "discoveryComplete": True, "reconciliationRequired": False}
    enrich_decisions(result)
    return result


class TargetAssessmentTests(unittest.TestCase):
    def test_known_current_unknown_target_is_review_and_not_policy_failure(self):
        unknown = health(postPlanSecurityKnown=0, postPlanSecurityUnknown=1, postPlanGoal="unknown")
        result = compact_blueprint(blueprint({"app": unknown}, unknown))
        self.assertFalse(result["decisionCompleteness"]["evidenceComplete"])
        self.assertTrue(result["decisionCompleteness"]["projectionComplete"])
        self.assertFalse(result["decisionCompleteness"]["requiresArtifactRead"])
        self.assertEqual(result["decisionCompleteness"]["blockerCount"], 0)
        reason = next(e for e in result["decisionEvidence"] if e["code"] == "TARGET_SECURITY_UNKNOWN")
        self.assertEqual(reason["severity"], "REVIEW")
        self.assertIsNone(reason["package"])
        self.assertTrue(result["dependencies"][0]["needsReview"])

    def test_known_policy_failure_retains_complete_evidence(self):
        blocked = health(postPlanCritical=1, postPlanGoal="blocked")
        result = compact_blueprint(blueprint({"app": blocked}, blocked))
        self.assertTrue(result["decisionCompleteness"]["evidenceComplete"])
        self.assertGreaterEqual(result["decisionCompleteness"]["blockerCount"], 1)
        self.assertTrue(result["targetAssessment"]["needsReview"])
        self.assertEqual(result["targetAssessment"]["aggregate"]["postPlanGoal"], "blocked")

    def test_custom_policy_allowance_does_not_invent_a_critical_blocker(self):
        allowed = health(postPlanCritical=1, postPlanPolicyMaxCritical=1)
        result = compact_blueprint(blueprint({"app": allowed}, allowed))
        self.assertTrue(result["decisionCompleteness"]["evidenceComplete"])
        self.assertEqual(result["decisionCompleteness"]["blockerCount"], 0)
        self.assertFalse(result["targetAssessment"]["needsReview"])

    def test_missing_legacy_target_evidence_cannot_reuse_current_coverage(self):
        value = blueprint({"app": {}}, {})
        # Even stale derived data must not override the raw published facts.
        value["targetAssessment"] = {"evidenceComplete": True, "needsReview": False}
        value["decisionEvidence"] = []
        result = compact_blueprint(value)
        self.assertFalse(result["decisionCompleteness"]["evidenceComplete"])
        self.assertTrue(any(e["code"] == "TARGET_ASSESSMENT_UNKNOWN" for e in result["decisionEvidence"]))
        self.assertTrue(result["dependencies"][0]["needsReview"])

    def test_one_project_unknown_cannot_hide_behind_aggregate_coverage(self):
        unknown = health(postPlanSecurityKnown=0, postPlanSecurityUnknown=1, postPlanGoal="unknown")
        value = blueprint({"clean": health(), "child/with~name": unknown},
                          health(postPlanSecurityKnown=2, postPlanSecurityTotal=2))
        result = compact_blueprint(value)
        self.assertFalse(result["decisionCompleteness"]["evidenceComplete"])
        reason = next(e for e in result["decisionEvidence"] if e["code"] == "TARGET_SECURITY_UNKNOWN")
        self.assertEqual(reason["scope"], "project")
        self.assertEqual(reason["project"], "child/with~name")
        self.assertIsNone(reason["package"])
        self.assertIn("child~1with~0name", reason["evidenceRef"])
        # Contradictory aggregate data requires review of the whole assignment.
        self.assertTrue(any(e["code"] == "TARGET_ASSESSMENT_INVALID" for e in result["decisionEvidence"]))
        self.assertTrue(result["dependencies"][0]["needsReview"])
        self.assertTrue(result["dependencies"][1]["needsReview"])
        # The JSON pointer must resolve against the full published document.
        current = value
        for token in reason["evidenceRef"][2:].split("/"):
            current = current[token.replace("~1", "/").replace("~0", "~")]
        self.assertIsInstance(current, dict)

    def test_consistent_project_evidence_keeps_unaffected_rows_separate(self):
        unknown = health(postPlanSecurityKnown=0, postPlanSecurityUnknown=1, postPlanGoal="unknown")
        aggregate = health(postPlanSecurityKnown=1, postPlanSecurityUnknown=1,
                           postPlanSecurityTotal=2, postPlanGoal="unknown")
        result = compact_blueprint(blueprint({"clean": health(), "unknown": unknown}, aggregate))
        self.assertFalse(result["decisionCompleteness"]["evidenceComplete"])
        self.assertFalse(result["dependencies"][0]["needsReview"])
        self.assertTrue(result["dependencies"][1]["needsReview"])
        self.assertTrue(all(e["project"] == "unknown" for e in result["decisionEvidence"]))

    def test_known_failure_and_unknown_evidence_are_both_visible(self):
        mixed = health(postPlanSecurityKnown=1, postPlanSecurityUnknown=1,
                       postPlanSecurityTotal=2, postPlanCritical=1, postPlanGoal="blocked")
        result = compact_blueprint(blueprint({"app": mixed}, mixed))
        codes = {e["code"] for e in result["decisionEvidence"]}
        self.assertTrue({"TARGET_POLICY_BLOCKED", "TARGET_SECURITY_UNKNOWN"}.issubset(codes))
        self.assertFalse(result["decisionCompleteness"]["evidenceComplete"])
        self.assertGreaterEqual(result["decisionCompleteness"]["blockerCount"], 1)

    def test_published_threshold_failure_survives_unknown_goal_without_rewriting_it(self):
        mixed = health(postPlanTargetLevel="green", postPlanSecurityKnown=1,
                       postPlanSecurityUnknown=1, postPlanSecurityTotal=2,
                       postPlanCritical=1, postPlanGoal="unknown")
        result = compact_blueprint(blueprint({"app": mixed}, mixed))
        self.assertEqual(result["targetAssessment"]["aggregate"]["postPlanGoal"], "unknown")
        self.assertGreaterEqual(result["decisionCompleteness"]["blockerCount"], 1)
        self.assertFalse(result["decisionCompleteness"]["evidenceComplete"])

    def test_known_violation_does_not_hide_separate_policy_uncertainty(self):
        mixed = health(postPlanTargetLevel="green", postPlanCritical=1, postPlanGoal="unknown")
        result = compact_blueprint(blueprint({"app": mixed}, mixed))
        codes = {e["code"] for e in result["decisionEvidence"]}
        self.assertTrue({"TARGET_POLICY_BLOCKED", "TARGET_POLICY_UNKNOWN"}.issubset(codes))
        self.assertFalse(result["decisionCompleteness"]["evidenceComplete"])
        self.assertEqual(result["targetAssessment"]["aggregate"]["postPlanGoal"], "unknown")

    def test_target_evidence_clipping_retains_counts_and_requires_artifact(self):
        blocked = health(postPlanCritical=1, postPlanGoal="blocked")
        value = blueprint({"app": blocked}, blocked)
        full = compact_blueprint(value)
        clipped = compact_blueprint(value, max_inline_evidence=0)
        self.assertEqual(clipped["decisionCompleteness"]["blockerCount"], full["decisionCompleteness"]["blockerCount"])
        self.assertEqual(clipped["targetAssessment"]["aggregate"], full["targetAssessment"]["aggregate"])
        self.assertEqual(clipped["targetAssessment"]["totalProjects"], 1)
        self.assertEqual(clipped["targetAssessment"]["inlineProjects"], 0)
        self.assertTrue(clipped["decisionCompleteness"]["requiresArtifactRead"])
        self.assertFalse(clipped["decisionCompleteness"]["projectionComplete"])
        self.assertTrue(clipped["decisionCompleteness"]["evidenceComplete"])
        self.assertIn("targetAssessment", clipped["truncation"]["limitsHit"])
        self.assertEqual(clipped["truncation"]["bytes"], len(json.dumps(clipped, ensure_ascii=False, indent=2).encode()))

    def test_security_changes_invalidate_digest_even_if_target_and_goal_stay_the_same(self):
        first = blueprint()
        changed = copy.deepcopy(first)
        changed["projections"]["postPlanHigh"] = 1
        changed["perProject"]["app"]["postPlanHigh"] = 1
        enrich_decisions(changed, previous_evidence_digest=first["identities"]["evidenceDigest"])
        self.assertNotEqual(first["identities"]["evidenceDigest"], changed["identities"]["evidenceDigest"])
        self.assertEqual(changed["recovery"]["previousEvidenceComparison"], "EVIDENCE_CHANGED")
        aggregate_only = copy.deepcopy(first)
        aggregate_only["projections"]["postPlanHigh"] = 1
        enrich_decisions(aggregate_only)
        self.assertNotEqual(first["identities"]["evidenceDigest"], aggregate_only["identities"]["evidenceDigest"])

    def test_invalid_or_inconsistent_coverage_never_becomes_complete(self):
        for changes in ({"postPlanSecurityKnown": True}, {"postPlanSecurityTotal": -1},
                        {"postPlanSecurityKnown": 2}, {"postPlanSecurityUnknown": None}):
            with self.subTest(changes=changes):
                broken = health(**changes)
                result = compact_blueprint(blueprint({"app": broken}, broken))
                self.assertFalse(result["decisionCompleteness"]["evidenceComplete"])
                self.assertTrue(result["targetAssessment"]["needsReview"])

    def test_exclusion_stays_resolved_without_erasing_global_target_failure(self):
        blocked = health(postPlanCritical=1, postPlanGoal="blocked")
        value = blueprint({"app": blocked}, blocked)
        value["dependencies"][0].update(status="EXCLUDED", needsReview=False, target="1.0.0")
        enrich_decisions(value)
        result = compact_blueprint(value)
        self.assertFalse(result["dependencies"][0]["needsReview"])
        self.assertGreaterEqual(result["decisionCompleteness"]["blockerCount"], 1)

    def test_target_assessment_is_schema_valid_and_stays_planning_only(self):
        value = blueprint()
        self.assertIsNone(_validate_blueprint_schema(value))
        result = compact_blueprint(value)
        self.assertTrue(result["decisionCompleteness"]["evidenceComplete"])
        self.assertFalse(result["targetAssessment"]["needsReview"])
        self.assertEqual(result["status"]["authority"], "PLANNING_ONLY")
        self.assertEqual(result["status"]["verificationStatus"], "NOT_VERIFIED")
        self.assertEqual(result["status"]["compatibility"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
