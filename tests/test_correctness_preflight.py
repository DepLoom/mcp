"""Offline focused unit tests for the independent P1 correctness oracle.

These tests are not agent sessions and cannot assert migration quality.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from correctness_preflight import (BASELINE, FINAL, LocalRegistry,
                                   plan_oracle, static_oracle)


def fixture_file_pair(project: Path, registry: str) -> None:
    (project / ".npmrc").write_text(f"registry={registry}\n", encoding="utf-8")
    (project / "package.json").write_text(
        json.dumps({"name": "fixture", "dependencies": FINAL}), encoding="utf-8")
    entries = {"": {"dependencies": dict(FINAL)}}
    for name, version in FINAL.items():
        entries[f"node_modules/{name}"] = {
            "version": version, "resolved": f"{registry}/{name}/-/{name}-{version}.tgz",
            "integrity": "sha512-SYNTHETIC_STATIC_UNIT_TEST_ONLY",
        }
    (project / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": entries}), encoding="utf-8")


class IndependentOracleTests(unittest.TestCase):
    def test_static_verdict_comes_from_fixture_contract_not_blueprint(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            registry = "http://127.0.0.1:49551"
            fixture_file_pair(path, registry)
            self.assertIsNone(static_oracle(path, registry))
            m = json.loads((path / "package.json").read_text())
            m["dependencies"] = BASELINE
            (path / "package.json").write_text(json.dumps(m))
            self.assertEqual("TARGET_ASSIGNMENT_MISMATCH", static_oracle(path, registry))

    def test_excluded_version_and_bad_lock_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            registry = "http://127.0.0.1:49551"
            fixture_file_pair(path, registry)
            m = json.loads((path / "package.json").read_text())
            m["dependencies"]["kontur-fixture-c"] = "4.0.0"
            (path / "package.json").write_text(json.dumps(m))
            self.assertEqual("EXCLUDED_DEPENDENCY_CHANGED", static_oracle(path, registry))
            fixture_file_pair(path, registry)
            lock = json.loads((path / "package-lock.json").read_text())
            lock["packages"]["node_modules/kontur-fixture-a"]["version"] = "1.2.0"
            (path / "package-lock.json").write_text(json.dumps(lock))
            self.assertEqual("LOCK_VERSION_MISMATCH", static_oracle(path, registry))
            fixture_file_pair(path, registry)
            (path / ".npmrc").write_text("registry=https://example.invalid/\n")
            self.assertEqual("UNAPPROVED_REGISTRY", static_oracle(path, registry))

    def test_plan_success_is_not_inferred_from_transport_success(self):
        rows = [
            {"package": "kontur-fixture-a", "status": "PROPOSED", "target": "1.9.0"},
            {"package": "kontur-fixture-b", "status": "PROPOSED", "target": "2.1.0"},
            {"package": "kontur-fixture-c", "status": "EXCLUDED", "target": None},
        ]
        plan = {"ok": True, "status": {"authority": "PLANNING_ONLY",
                                     "verificationStatus": "NOT_VERIFIED",
                                     "compatibility": "UNKNOWN"},
                "dependencies": rows}
        self.assertEqual("PASS", plan_oracle(plan))
        fake_verified = copy.deepcopy(plan)
        fake_verified["status"]["verificationStatus"] = "VERIFIED"
        self.assertEqual("MCP_FALSE_VERIFICATION", plan_oracle(fake_verified))
        wrong = copy.deepcopy(plan)
        wrong["dependencies"][0]["target"] = "1.2.0"
        self.assertEqual("MCP_EXPECTED_PROPOSAL_MISSING", plan_oracle(wrong))
        bad_foreign = copy.deepcopy(plan)
        bad_foreign["dependencies"][0]["status"] = "NO_TARGET"
        bad_foreign["dependencies"][0]["target"] = None
        self.assertEqual("MCP_FOREIGN_ORIGIN_EVIDENCE_MISSING",
                         plan_oracle(bad_foreign, expect_foreign=True))
        bad_foreign["dependencies"][0]["registryCandidateDiagnostics"] = {
            "acceptedUpgradeCandidates": 0,
            "rejectionCounts": {"FOREIGN_TARBALL_ORIGIN": 1},
        }
        self.assertEqual("MCP_NO_TARGET_CODE_MISSING",
                         plan_oracle(bad_foreign, expect_foreign=True))
        bad_foreign["dependencies"][0]["noTargetDiagnosticCode"] = "NO_INSTALLABLE_ARTIFACT"
        self.assertEqual("PASS", plan_oracle(bad_foreign, expect_foreign=True))
        # Policy-satisfied/current version is not required to say NO_TARGET.
        retained = copy.deepcopy(bad_foreign)
        retained["dependencies"][0]["status"] = "POLICY_SATISFIED"
        del retained["dependencies"][0]["noTargetDiagnosticCode"]
        self.assertEqual("PASS", plan_oracle(retained, expect_foreign=True))
        retained["dependencies"][0]["target"] = "1.9.0"
        self.assertEqual("MCP_FOREIGN_ARTIFACT_SELECTED",
                         plan_oracle(retained, expect_foreign=True))

    def test_local_registry_denies_unpublished_tarballs(self):
        with LocalRegistry() as reg:
            with self.assertRaises(HTTPError) as rejected:
                urlopen(reg.base + "/kontur-fixture-a/-/kontur-fixture-a-99.0.0.tgz",
                        timeout=3)
            self.assertEqual(404, rejected.exception.code)
            with urlopen(reg.base + "/kontur-fixture-a", timeout=3) as response:
                metadata = json.load(response)
            self.assertEqual(set(metadata["versions"]), {"1.2.0", "1.9.0"})


if __name__ == "__main__":
    unittest.main()
