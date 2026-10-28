"""Real full-Blueprint evidence -> follow-up tools: offline and read-only.

The tool may reject authority violations and stale inputs; it must never claim
a newly constructed Z3 model or security attestation for alternative versions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from deploom_mcp.blueprint import BlueprintPolicy, transcode_upgrade_blueprint
from deploom_mcp.decisions import enrich_decisions
from deploom_mcp.decision_followups import (
    evaluate_assignment, explain_conflict, load_blueprint,
)


class FollowupContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.manifest = self.project / "package.json"
        self.manifest.write_text(json.dumps({
            "name": "fixture", "version": "1.0.0",
            "dependencies": {"router": "1.0.0", "adapter": "1.0.0"}
        }), encoding="utf-8")
        self.blueprint_file = self.root / "artifacts" / "upgrade-blueprint.json"
        self.blueprint_file.parent.mkdir()
        rows = [
            {"project": "fixture", "package": "router", "kind": "runtime",
             "current": "1.0.0", "target": "2.0.0", "latest": "3.0.0",
             "status": "proposed", "group": 1, "reason": "Upgrade router major",
             "requestedSpec": "^1.0.0"},
            {"project": "fixture", "package": "adapter", "kind": "runtime",
             "current": "1.0.0", "target": "1.0.0", "latest": "1.1.0",
             "status": "ok", "group": 1, "reason": "Remains current",
             "requestedSpec": "^1.0.0"},
        ]
        health = {
            "total": 2, "known": 2, "lag_ok_pct": 100.0,
            "critical": 0, "high": 0, "moderate": 0, "low": 0, "unknown": 0,
            "postPlanSecurityKnown": 2, "postPlanSecurityUnknown": 0,
            "postPlanSecurityTotal": 2, "postPlanCritical": 0,
            "postPlanHigh": 0, "postPlanModerate": 0, "postPlanLow": 0,
            "postPlanGoal": "feasible",
        }
        plan = {"proposals": [{"project": "fixture", "rows": rows, "health": health}],
                "unknowns": [], "unresolved": [], "conflicts": [], "counts": {"total": 2}}
        manifest = {
            "status": "DRAFT_READY", "runId": "run-test", "workspaceId": "ws-test",
            "projectId": "fixture", "mode": "draft", "generatedAt": None,
            "verificationStatus": "NOT_VERIFIED", "authority": "PLANNING_ONLY",
            "compatibility": "UNKNOWN", "metadata": {
                "metadataKnown": 2, "metadataTotal": 2,
                "securityKnown": 2, "securityTotal": 2,
                **health
            },
        }
        self.bp = transcode_upgrade_blueprint(
            plan, manifest, project_path=str(self.project), project_name="fixture",
            policy=BlueprintPolicy(), artifacts={}, blueprint_path=str(self.blueprint_file),
        )
        self.bp["policy"]["decisionContract"] = {
            "version": 1, "targetMode": "advisory", "objective": "latest-compatible-in-approved-lines",
            "mutablePackages": ["router", "adapter"],
            "approvedVersionLines": {"router": [1, 2], "adapter": [1]},
        }
        self.bp["source"]["semanticInputHash"] = "synthetic-semantic-input-hash"
        self.bp["source"]["semanticInputsByProject"] = {
            "fixture": {"files": [{
                "name": "package.json", "path": str(self.manifest),
                "status": "PRESENT", "required": True,
                "contentHash": hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
            }]}
        }
        self.bp["workspaceCoverage"] = {
            "mode": "SINGLE_MANIFEST_PLANNING", "jointAssignment": "NOT_COMPUTED",
            "reconciliationRequired": False, "discoveryComplete": True,
            "workspaceGraphCoverage": "SINGLE_PROJECT",
            "evaluatedManifests": [str(self.manifest)],
            "discoveredManifests": [str(self.manifest)],
            "unevaluatedManifests": [],
        }
        self.publish()

    def publish(self):
        enrich_decisions(self.bp)
        self.blueprint_file.write_text(json.dumps(self.bp, ensure_ascii=False, indent=2), encoding="utf-8")
        self.digest = self.bp["identities"]["decisionDigest"]
        return self.digest

    def evaluate(self, values, **options):
        return evaluate_assignment(blueprint_path=str(self.blueprint_file), assignment=values, **options)

    def explain(self, **options):
        return explain_conflict(blueprint_path=str(self.blueprint_file), **options)

    def test_roundtrip_publication_identity_and_hashes(self):
        saved, digest = load_blueprint(str(self.blueprint_file), expected_decision_digest=self.digest)
        self.assertEqual(saved["identities"]["decisionDigest"], self.digest)
        self.assertEqual(64, len(digest))
        self.assertEqual("MATCHES_RECORDED_FILES", self.evaluate({})["sourceFreshness"]["status"])

    def test_complete_published_target_is_not_a_runtime_proof(self):
        result = self.evaluate({})
        self.assertTrue(result["ok"], result)
        self.assertEqual("MATCHES_PUBLISHED_PLAN", result["verdict"], result)
        self.assertEqual("NOT_VERIFIED", result["verificationStatus"])
        self.assertEqual("UNKNOWN", result["compatibility"])
        self.assertEqual("NOT_RECOMPUTED", result["policyAttainment"])
        self.assertFalse(result["installedTreeVerified"])
        self.assertEqual(0, result["decision"]["changedDeclarationsFromPublishedPlan"])

    def test_advisory_same_major_candidate_requires_independent_checks(self):
        result = self.evaluate({"adapter": "1.0.7"})
        self.assertEqual("REVIEW_REQUIRED", result["verdict"], result)
        self.assertIn("ALTERNATIVE_PEER_SECURITY_POLICY_NOT_RECHECKED", {x["code"] for x in result["checks"]})
        self.assertEqual("VERIFY_ALTERNATIVE_PEERS_SECURITY_POLICY_AND_RUNTIME", result["nextAction"])


    def test_published_target_does_not_override_declared_hard_scope(self):
        self.bp["policy"]["decisionContract"]["approvedVersionLines"] = {"router": [1], "adapter": [1]}
        self.publish()
        result = self.evaluate({})
        self.assertEqual("BLOCKED_BY_KNOWN_CONSTRAINTS", result["verdict"], result)
        self.assertIn("MAJOR_LINE_NOT_APPROVED", {x["code"] for x in result["checks"]})

    def test_pinned_alternative_requires_replan(self):
        self.bp["policy"]["decisionContract"]["targetMode"] = "pinned"
        self.publish()
        result = self.evaluate({"router": "1.5.0"})
        self.assertEqual("REPLAN_REQUIRED", result["verdict"], result)
        self.assertIn("PINNED_TARGET_CHANGE_REQUIRES_REPLAN", {x["code"] for x in result["checks"]})

    def test_major_line_violation_is_known_blocker(self):
        result = self.evaluate({"router": "3.0.0"})
        self.assertEqual("BLOCKED_BY_KNOWN_CONSTRAINTS", result["verdict"], result)
        self.assertIn("MAJOR_LINE_NOT_APPROVED", {x["code"] for x in result["checks"]})
        self.assertEqual("UNKNOWN", result["compatibility"])

    def test_immutable_declaration_change_is_rejected(self):
        self.bp["policy"]["decisionContract"]["mutablePackages"] = ["router"]
        self.publish()
        result = self.evaluate({"adapter": "1.0.1"})
        self.assertIn("IMMUTABLE_PACKAGE_CHANGED", {x["code"] for x in result["checks"]})

    def test_explicit_exclusion_cannot_be_silently_changed(self):
        self.bp["policy"]["exclusions"] = ["adapter"]
        self.publish()
        result = self.evaluate({"adapter": "1.0.1"})
        self.assertIn("EXCLUDED_PACKAGE_CHANGED", {x["code"] for x in result["checks"]})
        self.assertEqual("BLOCKED_BY_KNOWN_CONSTRAINTS", result["verdict"], result)

    def test_partial_blueprint_remains_partial(self):
        self.bp["status"]["draftStatus"] = "DRAFT_PARTIAL"
        self.publish()
        result = self.evaluate({})
        self.assertEqual("REVIEW_REQUIRED", result["verdict"])
        self.assertIn("PARTIAL_BLUEPRINT", {x["code"] for x in result["checks"]})

    def test_workspace_joint_assignment_cannot_be_inferred(self):
        self.bp["workspaceCoverage"]["reconciliationRequired"] = True
        self.bp["workspaceCoverage"]["workspaceGraphCoverage"] = "PARTIAL"
        self.publish()
        result = self.evaluate({})
        self.assertEqual("REVIEW_REQUIRED", result["verdict"])
        self.assertIn("WORKSPACE_RECONCILIATION_REQUIRED", {x["code"] for x in result["checks"]})

    def test_stale_source_is_never_an_accepted_assignment(self):
        self.manifest.write_text('{"name":"changed"}', encoding="utf-8")
        result = self.evaluate({})
        self.assertEqual("STALE_BLUEPRINT", result["verdict"], result)
        self.assertIn("package.json", result["sourceFreshness"]["changedFiles"])

    def test_modified_blueprint_cannot_keep_old_digest(self):
        self.bp["dependencies"][0]["target"] = "2.0.3"
        # Tampered file: deliberately do not recalculate the published identity.
        self.blueprint_file.write_text(json.dumps(self.bp), encoding="utf-8")
        result = self.evaluate({})
        self.assertFalse(result["ok"])
        self.assertEqual("blueprint-identity-mismatch", result["error"]["code"])


    def test_modified_cached_reason_cannot_hide_an_underlying_blocker(self):
        self.bp["conflicts"] = [{
            "package": "router", "active": True, "stage": "final",
            "notes": ["PEER_RESOLUTION_DEFERRED: adapter must remain on ^1"],
        }]
        self.publish()
        self.bp["decisionEvidence"] = []
        self.blueprint_file.write_text(json.dumps(self.bp), encoding="utf-8")
        result = self.explain(package="router")
        self.assertEqual("blueprint-identity-mismatch", result["error"]["code"])

    def test_expected_digest_mismatch_rejected(self):
        result = self.evaluate({}, expected_decision_digest="0" * 64)
        self.assertEqual("decision-digest-mismatch", result["error"]["code"])

    def test_invalid_versions_and_unknown_package_rejected(self):
        for values in ({"router": "^2.0.0"}, {"router": "19.0"}, {"unknown": "1.0.0"}):
            with self.subTest(values=values):
                result = self.evaluate(values)
                self.assertFalse(result["ok"], result)

    def test_ambiguous_declaration_requires_identity(self):
        duplicate = copy.deepcopy(self.bp["dependencies"][0])
        duplicate["current"] = "1.4.0"
        duplicate["declarationId"] = "workspace/package.json#dev:router"
        duplicate["kind"] = "dev"
        self.bp["dependencies"].append(duplicate)
        # enrich_decisions creates declaration IDs itself, so make the second
        # different kind and ensure no ambiguous collision.
        self.publish()
        result = self.evaluate({"router": "2.0.4"})
        self.assertEqual("ambiguous-package", result["error"]["code"])

    def test_explain_active_final_blocker_with_pointer_and_lifecycle(self):
        self.bp["conflicts"] = [{
            "package": "router", "active": True, "stage": "final",
            "notes": ["PEER_RESOLUTION_DEFERRED: adapter requires router ^1"],
        }]
        self.publish()
        result = self.explain(package="router", expected_decision_digest=self.digest)
        self.assertTrue(result["ok"], result)
        self.assertEqual("DOCUMENTED_CONSTRAINTS", result["verdict"])
        source_reason = next(x for x in result["reasons"] if x["evidenceRef"] == "#/conflicts/0")
        self.assertEqual("PEER_CONSTRAINT", source_reason["code"])
        self.assertEqual("BLOCKER", source_reason["severity"])
        self.assertEqual("ACTIVE_FINAL", source_reason["lifecycle"])
        self.assertEqual("UNKNOWN", result["compatibility"])

    def test_explain_legacy_notes_are_not_made_final_blockers(self):
        self.bp["conflicts"] = [{"package": "router", "notes": ["PEER_RESOLUTION_DEFERRED: historical hint"]}]
        self.publish()
        result = self.explain(package="router")
        self.assertTrue(result["ok"])
        self.assertTrue(result["reasons"])
        legacy = next(x for x in result["reasons"] if x["evidenceRef"] == "#/conflicts/0")
        self.assertEqual("UNRESOLVED_LEGACY", legacy["lifecycle"])
        self.assertEqual("REVIEW", legacy["severity"])

    def test_no_published_reason_is_not_a_compatibility_proof(self):
        result = self.explain(package="adapter")
        self.assertEqual("NO_DOCUMENTED_BLOCKER_OR_REVIEW", result["verdict"])
        self.assertEqual("UNKNOWN", result["compatibility"])

    def test_explain_rejects_other_blueprint_or_unknown_package(self):
        self.assertEqual("unknown-declaration", self.explain(package="not-in-this-plan")["error"]["code"])
        self.assertEqual("invalid-blueprint-path", explain_conflict(blueprint_path="relative")["error"]["code"])


if __name__ == "__main__":
    unittest.main()
