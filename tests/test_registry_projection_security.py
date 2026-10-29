"""P2.6 adversarial malformed-Core evidence, redaction and false verdict review."""
from __future__ import annotations

import copy
import json
import unittest

from deploom_mcp.registry_projection import (
    project_registry_aggregate, project_registry_candidate, project_no_target_code,
)
from deploom_mcp.blueprint import BlueprintPolicy, transcode_upgrade_blueprint
from deploom_mcp.server import compact_blueprint


def valid():
    return {
        "schemaVersion": 1, "authority": "STRUCTURAL_REGISTRY_EVIDENCE_ONLY",
        "examined": 2, "accepted": 0, "rejected": 2,
        "upgradeCandidates": 1, "acceptedUpgradeCandidates": 0,
        "rejectedUpgradeCandidates": 1, "allUpgradeCandidatesRejected": True,
        "configuredRegistry": {"origin": "https://admin:pw@nexus.invalid/repository/a1b2c3/npm",
                               "pathPrefix": "/repository/a1b2c3/npm"},
        "rejectionCounts": {"BAD_REGISTRY_PATH": 2},
        "rejectionSamples": [{"code": "BAD_REGISTRY_PATH", "version": "4.18.1"}],
        "physicalProbe": {"observed": 1, "unprobed": True,
                          "statusCounts": {"planning-only": 1}, "failureCounts": {}},
    }


def fixture(diagnostic):
    p={"proposals": [{"project":"sample","rows": [{"package":"demo","project":"sample",
        "kind":"runtime","requestedSpec":"1.0.0","current":"1.0.0","target":None,
        "status":"no-target","reason":"no target","group":1,
        "registryCandidateDiagnostics":diagnostic,"noTargetDiagnosticCode":"NO_INSTALLABLE_ARTIFACT"}],
        "health":{"total":1,"critical":0,"high":0}}],
        "counts":{"no-target":1,"total":1},"unknowns":[],"conflicts":[],"unresolved":[]}
    m={"status":"DRAFT_READY","runId":"r","verificationStatus":"NOT_VERIFIED",
       "authority":"PLANNING_ONLY","compatibility":"UNKNOWN",
       "metadata":{"metadataKnown":1,"metadataTotal":1,"securityKnown":1,"securityTotal":1}}
    return transcode_upgrade_blueprint(p,m,project_path="/tmp/source",project_name="sample",
        policy=BlueprintPolicy(),artifacts={"plan":"/tmp/plan.json"},blueprint_path="/tmp/bp.json")


class DiagnosticsSecurityReviewTests(unittest.TestCase):
    def test_short_opaque_and_encoded_credentials_never_leak_via_full_or_compact(self):
        c=valid()
        c["configuredRegistry"]["pathPrefix"]="/repository/shortkey/npm"
        c["metadataRequestBase"]={"origin":"https://user:abc@some.invalid/a?token=topsecret",
                                  "pathPrefix":"/repository/%61piKey/npm"}
        c["physicalProbe"]["statusCounts"]={"planning-only":1,"secret123":42}
        bp=fixture(c)
        compact=compact_blueprint(bp)
        for value in (bp,compact):
            serialized=json.dumps(value)
            for forbidden in ("shortkey", "apiKey", "topsecret", "user:abc", "secret123"):
                self.assertNotIn(forbidden,serialized)
        clean=bp["dependencies"][0]["registryCandidateDiagnostics"]
        self.assertEqual("/repository/[redacted]/npm",clean["configuredRegistry"]["pathPrefix"])
        self.assertEqual({"planning-only":1},clean["physicalProbe"]["statusCounts"])
        self.assertTrue(clean["projectionTruncated"])

    def test_forged_false_no_installable_is_not_forwarded(self):
        mutated=valid()
        mutated["acceptedUpgradeCandidates"]=1  # inconsistent with accepted=0
        self.assertIsNone(project_registry_candidate(mutated))
        bp=fixture(mutated)
        row=bp["dependencies"][0]
        self.assertEqual("NO_TARGET",row["status"])
        self.assertNotIn("noTargetDiagnosticCode",row)
        self.assertNotIn("registryCandidateDiagnostics",row)
        self.assertNotIn("noTargetDiagnosticCode",compact_blueprint(bp)["dependencies"][0])

    def test_no_installable_requires_consistent_structural_rejections(self):
        c=project_registry_candidate(valid())
        self.assertEqual("NO_INSTALLABLE_ARTIFACT",project_no_target_code("NO_INSTALLABLE_ARTIFACT","no-target",c))
        self.assertIsNone(project_no_target_code("NO_INSTALLABLE_ARTIFACT","no-target",None))
        self.assertIsNone(project_no_target_code("NO_INSTALLABLE_ARTIFACT","proposed",c))
        self.assertIsNone(project_no_target_code("NO_PUBLISHED_UPGRADE_CANDIDATE","no-target",c))

    def test_aggregate_contradictory_and_credential_keys_cannot_become_false_clean(self):
        a={"schemaVersion":1,"authority":"STRUCTURAL_REGISTRY_EVIDENCE_ONLY",
           "examinedVersions":2,"rejectedVersions":2,"noInstallableArtifactRows":1,
           "rejectionCounts":{"BAD_REGISTRY_PATH":2}}
        self.assertEqual(2,project_registry_aggregate(a)["rejectedVersions"])
        for broken in [dict(a,rejectedVersions=0),dict(a,rejectionCounts={}),
                       dict(a,rejectionCounts={"token=secret":2}),dict(a,examinedVersions=-1)]:
            self.assertIsNone(project_registry_aggregate(broken))

    def test_legacy_without_registry_diagnostics_does_not_invent_known_security(self):
        c=valid()
        bp=fixture(c)
        self.assertEqual("PLANNING_ONLY",bp["status"]["authority"])
        self.assertEqual("NOT_VERIFIED",bp["status"]["verificationStatus"])
        self.assertEqual("UNKNOWN",bp["status"]["compatibility"])
