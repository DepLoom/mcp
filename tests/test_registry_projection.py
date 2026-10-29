"""Core issue #3 -> MCP issue #5: bounded diagnostics, schema and legacy behavior.

All fixtures are synthetic and offline. Pinned Core continues to be built
unchanged; live stdio + real Core PR are checked in a separate evaluation job.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import jsonschema

from deploom_mcp.blueprint import BlueprintPolicy, transcode_upgrade_blueprint
from deploom_mcp.registry_projection import (
    compact_registry_candidate, project_no_target_code,
    project_registry_aggregate, project_registry_candidate,
)
from deploom_mcp.server import compact_blueprint

SCHEMA = json.loads((ROOT / "contracts/upgrade-blueprint.schema.json").read_text(encoding="utf-8"))


def core_registry_data(code: str = "FOREIGN_TARBALL_ORIGIN", *, matching: bool = False):
    rejected = 0 if matching else 2
    return {
        "schemaVersion": 1, "authority": "STRUCTURAL_REGISTRY_EVIDENCE_ONLY",
        "examined": 2, "accepted": 2 if matching else 0, "rejected": rejected,
        "upgradeCandidates": 1,
        "acceptedUpgradeCandidates": 1 if matching else 0,
        "rejectedUpgradeCandidates": 0 if matching else 1,
        "allUpgradeCandidatesRejected": not matching,
        "configuredRegistry": {"origin": "http://127.0.0.1:43911", "pathPrefix": "/npm"},
        "metadataRequestBase": {"origin": "http://127.0.0.1:43911", "pathPrefix": "/npm"},
        "metadataRequestBaseAuthority": "CONFIGURATION_NOT_HTTP_TRACE",
        "rejectionCounts": {} if matching else {code: 2},
        "rejectedArtifactOrigins": [] if matching else ["https://registry.npmjs.org"],
        "rejectionSamples": [] if matching else [
            {"code": code, "version": "4.17.20"},
            {"code": code, "version": "4.18.1"},
        ],
        "physicalProbe": {"observed": 1 if matching else 0,
                          "failureCounts": {},
                          "statusCounts": {"planning-only": 1} if matching else {},
                          "unprobed": True},
    }


def fixture(*, status="no-target", diag=True, reason="FOREIGN_TARBALL_ORIGIN"):
    row = {"project": "app", "package": "lodash", "kind": "runtime", "requestedSpec": "4.17.20",
           "current": "4.17.20", "target": "4.18.1" if status == "proposed" else None,
           "latest": "4.18.1", "status": status, "reason": "known evidence",
           "group": 1, "candidateSearchTruncated": False, "peerCompatUnresolved": False,
           "breakingChanges": [], "migrationNotes": []}
    plan = {"proposals": [{"project": "app", "rows": [row],
                           "health": {"total": 1, "known": 1, "unknown": 0,
                                      "critical": 0, "high": 1}}],
            "counts": {"total": 1, "no-target": 1} if status != "proposed" else {"total": 1, "proposed": 1},
            "unknowns": [], "conflicts": [], "unresolved": []}
    if diag:
        match = status == "proposed"
        row["registryCandidateDiagnostics"] = core_registry_data(reason, matching=match)
        if not match:
            row["noTargetDiagnosticCode"] = "NO_INSTALLABLE_ARTIFACT"
        plan["registryDiagnostics"] = {"schemaVersion": 1, "authority": "STRUCTURAL_REGISTRY_EVIDENCE_ONLY",
            "examinedVersions": 2, "rejectedVersions": 0 if match else 2,
            "noInstallableArtifactRows": 0 if match else 1,
            "rejectionCounts": {} if match else {reason: 2}}
    manifest = {"status": "DRAFT_READY", "runId": "r", "generatedAt": None,
                "verificationStatus": "NOT_VERIFIED", "authority": "PLANNING_ONLY",
                "compatibility": "UNKNOWN", "metadata": {"metadataKnown": 1, "metadataTotal": 1,
                  "securityKnown": 1, "securityTotal": 1},
                "inputFilesByProject": {"app": [{"name": "package.json"}]},
                "summary": "one row"}
    return plan, manifest


def transcode(plan, manifest):
    return transcode_upgrade_blueprint(
        plan, manifest, project_path="/fixture/app", project_name="app",
        policy=BlueprintPolicy(), artifacts={"plan": "/fixture/plan.json"},
        blueprint_path="/fixture/blueprint.json",
    )


class RegistryDiagnosticProjectionTests(unittest.TestCase):
    def test_full_matching_registry_does_not_create_false_blocker(self):
        p, m = fixture(status="proposed")
        bp = transcode(p, m)
        jsonschema.validate(bp, SCHEMA)
        row = bp["dependencies"][0]
        self.assertEqual(row["status"], "PROPOSED")
        self.assertEqual(row["target"], "4.18.1")
        self.assertNotIn("noTargetDiagnosticCode", row)
        self.assertEqual(row["registryCandidateDiagnostics"]["acceptedUpgradeCandidates"], 1)
        self.assertTrue(row["registryCandidateDiagnostics"]["physicalProbe"]["unprobed"])
        self.assertEqual(bp["registryDiagnostics"]["rejectedVersions"], 0)
        comp = compact_blueprint(bp)
        self.assertEqual(comp["dependencies"][0]["registryCandidateDiagnostics"]["acceptedUpgradeCandidates"], 1)
        self.assertNotIn("configuredRegistry", comp["dependencies"][0]["registryCandidateDiagnostics"])
        self.assertNotIn("rejectedArtifactOrigins", comp["dependencies"][0]["registryCandidateDiagnostics"])

    def test_full_foreign_registry_exposes_code_and_counts(self):
        p, m = fixture()
        source = copy.deepcopy(p)
        bp = transcode(p, m)
        self.assertEqual(p, source, "projection is read only")
        jsonschema.validate(bp, SCHEMA)
        row = bp["dependencies"][0]
        self.assertEqual(row["status"], "NO_TARGET")
        self.assertIsNone(row["target"])
        self.assertEqual(row["noTargetDiagnosticCode"], "NO_INSTALLABLE_ARTIFACT")
        self.assertEqual(row["registryCandidateDiagnostics"]["rejectionCounts"], {"FOREIGN_TARBALL_ORIGIN": 2})
        self.assertEqual(bp["registryDiagnostics"]["noInstallableArtifactRows"], 1)
        compact = compact_blueprint(bp)
        self.assertEqual(compact["registryDiagnostics"]["rejectionCounts"]["FOREIGN_TARBALL_ORIGIN"], 2)
        self.assertEqual(compact["dependencies"][0]["noTargetDiagnosticCode"], "NO_INSTALLABLE_ARTIFACT")
        self.assertEqual(compact["dependencies"][0]["registryCandidateDiagnostics"]["evidenceRef"],
                         "#/dependencies/0/registryCandidateDiagnostics")
        self.assertEqual(compact["artifacts"]["plan"], "/fixture/plan.json")
        self.assertTrue(row["needsReview"])
        self.assertEqual(bp["status"]["authority"], "PLANNING_ONLY")
        self.assertEqual(bp["status"]["verificationStatus"], "NOT_VERIFIED")
        self.assertEqual(bp["status"]["compatibility"], "UNKNOWN")

    def test_wrong_prefix_and_missing_dist_are_distinct(self):
        for reason in ("BAD_REGISTRY_PATH", "MISSING_DIST_TARBALL"):
            with self.subTest(reason=reason):
                bp = transcode(*fixture(reason=reason))
                jsonschema.validate(bp, SCHEMA)
                self.assertEqual(bp["dependencies"][0]["registryCandidateDiagnostics"]["rejectionCounts"],
                                 {reason: 2})
                self.assertEqual(bp["dependencies"][0]["status"], "NO_TARGET")

    def test_old_pinned_core_omits_new_fields_in_both_projections(self):
        p, m = fixture(diag=False)
        bp = transcode(p, m)
        jsonschema.validate(bp, SCHEMA)
        self.assertNotIn("registryDiagnostics", bp)
        self.assertNotIn("registryCandidateDiagnostics", bp["dependencies"][0])
        self.assertNotIn("noTargetDiagnosticCode", bp["dependencies"][0])
        compact = compact_blueprint(bp)
        self.assertNotIn("registryDiagnostics", compact)
        self.assertNotIn("registryCandidateDiagnostics", compact["dependencies"][0])
        self.assertNotIn("noTargetDiagnosticCode", compact["dependencies"][0])
        self.assertNotIn("registryDiagnostics", compact["summary"])

    def test_identical_input_decision_and_security_digest_without_diagnostics(self):
        plan, manifest = fixture()
        with_diag = transcode(plan, manifest)
        plan2, manifest2 = fixture(diag=False)
        without_diag = transcode(plan2, manifest2)
        self.assertEqual(with_diag["identities"], without_diag["identities"])
        self.assertEqual(with_diag["summary"]["statusCounts"], without_diag["summary"]["statusCounts"])
        self.assertEqual(with_diag["summary"]["securityKnown"], without_diag["summary"]["securityKnown"])
        self.assertEqual(with_diag["targetAssessment"], without_diag["targetAssessment"])

    def test_no_target_code_is_status_bound_and_whitelisted(self):
        self.assertIsNone(project_no_target_code("NO_INSTALLABLE_ARTIFACT", "proposed"))
        self.assertIsNone(project_no_target_code("CANDIDATE_CAP", "no-target"))
        self.assertEqual(project_no_target_code("CANDIDATE_CAP", "candidate-search-truncated"), "CANDIDATE_CAP")
        self.assertIsNone(project_no_target_code("SECRET_UNEXPECTED", "no-target"))
        p, m = fixture(status="proposed")
        p["proposals"][0]["rows"][0]["noTargetDiagnosticCode"] = "NO_INSTALLABLE_ARTIFACT"
        self.assertNotIn("noTargetDiagnosticCode", transcode(p, m)["dependencies"][0])

    def test_unknown_schema_or_malformed_core_diagnostics_cannot_invent_known_clean(self):
        p, m = fixture()
        p["registryDiagnostics"]["schemaVersion"] = 900
        p["proposals"][0]["rows"][0]["registryCandidateDiagnostics"]["authority"] = "invented"
        bp = transcode(p, m)
        self.assertNotIn("registryDiagnostics", bp)
        self.assertNotIn("registryCandidateDiagnostics", bp["dependencies"][0])
        self.assertNotIn("noTargetDiagnosticCode", bp["dependencies"][0])
        jsonschema.validate(bp, SCHEMA)

    def test_untrusted_strings_and_credentials_are_never_forwarded(self):
        diag = core_registry_data()
        marker = "SENSITIVE_TOKEN_0123456789"
        diag["configuredRegistry"] = {"origin": f"https://user:{marker}@nexus.example.invalid/secret?token={marker}",
                                       "pathPrefix": f"/repository/{marker}/npm"}
        diag["metadataRequestBase"] = diag["configuredRegistry"]
        diag["rejectedArtifactOrigins"] = [f"https://user:{marker}@bad.example.invalid/path?token={marker}"]
        diag["rejectionSamples"] = [{"code": "FOREIGN_TARBALL_ORIGIN", "version": f"https://host/{marker}"}]
        diag["physicalProbe"]["statusCounts"] = {"planning-only": 1, marker: 2}
        clean = project_registry_candidate(diag)
        self.assertIsNotNone(clean)
        payload = json.dumps(clean)
        self.assertNotIn(marker, payload)
        self.assertNotIn("user:", payload)
        self.assertNotIn("?token=", payload)
        self.assertNotIn("/secret", payload)
        self.assertNotIn("https://host/", payload)
        self.assertEqual(clean["configuredRegistry"]["origin"], "https://nexus.example.invalid")
        self.assertEqual(clean["configuredRegistry"]["pathPrefix"], "/repository/[redacted]/npm")
        self.assertEqual(clean["rejectionSamples"], [])
        self.assertTrue(clean["projectionTruncated"])
        # A secret-bearing string could otherwise masquerade as a plain
        # version label in a full Blueprint's rejectionSamples.
        diag["rejectionSamples"] = [{"code": "FOREIGN_TARBALL_ORIGIN", "version": marker},
                                     {"code": "FOREIGN_TARBALL_ORIGIN", "version": "4.17.20-secret"}]
        self.assertEqual(project_registry_candidate(diag)["rejectionSamples"], [])
        self.assertNotIn("physicalProbe", clean,
                             "contradictory observed/status counters must not be published")
        self.assertTrue(clean["projectionTruncated"])
        self.assertNotIn("configuredRegistry", compact_registry_candidate(clean, "#/dependencies/0"))
        self.assertIsNone(project_registry_aggregate({"schemaVersion": 1, "authority": diag["authority"],
                                                     "rejectionCounts": {f"https://host/{marker}": 8}}))

    def test_sanitizer_handles_bad_types_without_echo_or_crash(self):
        diag = core_registry_data()
        diag["rejectionCounts"] = {"BAD_REGISTRY_PATH": True, 5: "secret", "FOREIGN_TARBALL_ORIGIN": -2}
        diag["examined"] = "12;rm"
        diag["metadataRequestBase"] = {"origin": "https://x/../../secret", "pathPrefix": None}
        clean = project_registry_candidate(diag)
        self.assertIsNone(clean, "malformed counts must not become false clean evidence")

    def test_projection_caps_examples_and_flags_truncation(self):
        p, m = fixture()
        d = p["proposals"][0]["rows"][0]["registryCandidateDiagnostics"]
        d["rejectionSamples"] = [{"code": "FOREIGN_TARBALL_ORIGIN", "version": f"4.18.{i}"} for i in range(100)]
        d["rejectedArtifactOrigins"] = [f"https://host{i}.example" for i in range(100)]
        bp = transcode(p, m)
        jsonschema.validate(bp, SCHEMA)
        full = bp["dependencies"][0]["registryCandidateDiagnostics"]
        self.assertEqual(len(full["rejectionSamples"]), 4)
        self.assertEqual(len(full["rejectedArtifactOrigins"]), 4)
        self.assertTrue(full["projectionTruncated"])
        compact = compact_blueprint(bp)
        self.assertTrue(compact["decisionCompleteness"]["requiresArtifactRead"])
        self.assertIn("evidenceDetails", compact["truncation"]["limitsHit"])
        self.assertNotIn("rejectionSamples", compact["dependencies"][0]["registryCandidateDiagnostics"])

    def test_compact_budget_stays_bounded_and_references_full_blueprint(self):
        p, m = fixture()
        bp = transcode(p, m)
        bp["dependencies"] *= 300
        bp["summary"]["totalDependencies"] = 300
        bp["summary"]["statusCounts"] = {"NO_TARGET": 300}
        compact = compact_blueprint(bp, max_response_bytes=6500)
        self.assertLessEqual(compact["truncation"]["bytes"], 6500)
        self.assertEqual(compact["truncation"]["bytes"], len(json.dumps(compact, ensure_ascii=False, indent=2).encode()))
        self.assertEqual(compact["summary"]["statusCounts"], {"NO_TARGET": 300})
        self.assertEqual(compact["registryDiagnostics"]["noInstallableArtifactRows"], 1)
        self.assertTrue(compact["truncation"]["truncated"])

    def test_schema_rejects_unbounded_or_unrecognized_diagnostics(self):
        bp = transcode(*fixture())
        bp["dependencies"][0]["registryCandidateDiagnostics"]["rejectionSamples"] *= 5
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(bp, SCHEMA)
        bp = transcode(*fixture())
        bp["registryDiagnostics"]["sensitiveExtraField"] = "URL secrets"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(bp, SCHEMA)

    def test_schema_and_compact_support_unknown_not_falsely_zero(self):
        p, m = fixture(diag=False)
        m["metadata"]["securityKnown"] = 0
        bp = transcode(p, m)
        self.assertIsNone(bp["dependencies"][0]["target"])
        jsonschema.validate(bp, SCHEMA)
        comp = compact_blueprint(bp)
        self.assertFalse(comp["decisionCompleteness"]["evidenceComplete"])
        self.assertNotIn("registryDiagnostics", comp)


if __name__ == "__main__":
    unittest.main()
