from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploom_mcp.audit_evidence import AuditError, normalize_audit
from deploom_mcp.dependency_audit import checked_registry, compact_audit, validate_request
from deploom_mcp.audit_worker import Runner, configure, detect_manager


def npm_report(findings=True):
    return {
        "auditReportVersion": 2,
        "vulnerabilities": {"sample": {"severity": "high", "nodes": ["node_modules/sample"], "via": [
            {"source": 123, "name": "sample", "title": "synthetic", "severity": "high", "range": "<2", "url": "https://example.test/GHSA-demo"}
        ]}} if findings else {},
        "metadata": {"vulnerabilities": {"info": 0, "low": 0, "moderate": 0, "high": 1 if findings else 0, "critical": 0, "total": 1 if findings else 0}},
    }


class AuditNormalizationTests(unittest.TestCase):
    def test_npm_finding_exit_one_is_completed_not_execution_failure(self):
        report = normalize_audit(json.dumps(npm_report()), 1, "npm")
        self.assertTrue(report["complete"])
        self.assertEqual("FINDINGS", report["verdict"])
        self.assertEqual(1, report["counts"]["uniqueAdvisories"])
        self.assertEqual(1, report["counts"]["affectedPackages"])
        self.assertFalse(report["installedTreeVerified"])

    def test_clean_is_not_runtime_or_policy_certification(self):
        report = normalize_audit(json.dumps(npm_report(False)), 0, "npm")
        self.assertEqual("NO_KNOWN_FINDINGS", report["verdict"])
        self.assertEqual("NOT_ASSESSED", report["policyAttainment"])
        self.assertFalse(report["installedTreeVerified"])

    def test_npm_empty_object_is_not_a_clean_audit(self):
        with self.assertRaises(AuditError):
            normalize_audit("{}", 0, "npm")

    def test_missing_completion_counters_are_not_clean(self):
        with self.assertRaises(AuditError):
            normalize_audit('{"vulnerabilities":{}}', 0, "npm")

    def test_empty_stdout_is_not_clean(self):
        with self.assertRaises(AuditError):
            normalize_audit("", 0, "npm")

    def test_json_error_is_not_clean(self):
        with self.assertRaises(AuditError):
            normalize_audit('{"error":{"code":"E401"}}', 1, "npm")

    def test_invalid_json_is_not_clean(self):
        with self.assertRaises(AuditError):
            normalize_audit("not json", 0, "npm")

    def test_nonzero_without_findings_is_unknown(self):
        with self.assertRaises(AuditError):
            normalize_audit(json.dumps(npm_report(False)), 1, "npm")

    def test_unexpected_exit_cannot_hide_behind_valid_json(self):
        with self.assertRaises(AuditError):
            normalize_audit(json.dumps(npm_report()), 127, "npm")

    def test_conflicting_metadata_cannot_hide_findings(self):
        report = npm_report(False)
        report["metadata"]["vulnerabilities"]["high"] = 1
        with self.assertRaises(AuditError):
            normalize_audit(json.dumps(report), 0, "npm")

    def test_duplicate_advisory_is_counted_once_not_once_per_package(self):
        report = npm_report()
        report["vulnerabilities"]["other"] = report["vulnerabilities"]["sample"]
        normalized = normalize_audit(json.dumps(report), 1, "npm")
        self.assertEqual(1, normalized["counts"]["uniqueAdvisories"])
        self.assertEqual(2, normalized["counts"]["affectedPackages"])

    def test_berry_tree_ndjson(self):
        line = {"value": "@scope/sample", "children": {"ID": 123, "Issue": "synthetic", "Severity": "moderate",
            "Vulnerable Versions": "<2", "Tree Versions": ["1.0.0"], "URL": "https://example.test/advisory"}}
        report = normalize_audit(json.dumps(line) + "\n", 1, "yarn-berry")
        self.assertEqual("1.0.0", report["packages"][0]["versions"][0])
        self.assertEqual(1, report["counts"]["advisoriesBySeverity"]["moderate"])
        self.assertIsNone(report["counts"]["reportedNodesBySeverity"])

    def test_berry_empty_json_requires_explicit_native_success_proof(self):
        with self.assertRaises(AuditError):
            normalize_audit("", 0, "yarn-berry")
        with self.assertRaises(AuditError):
            normalize_audit("", 1, "yarn-berry", confirmed_empty_success=True)
        with self.assertRaises(AuditError):
            normalize_audit("", 0, "npm", confirmed_empty_success=True)
        evidence = normalize_audit("", 0, "yarn-berry", confirmed_empty_success=True)
        self.assertEqual("NO_KNOWN_FINDINGS", evidence["verdict"])
        self.assertEqual("YARN4_NATIVE_EXIT_ZERO_NO_JSON", evidence["completionEvidence"])
        self.assertFalse(evidence["installedTreeVerified"])

    def test_berry_clean_event(self):
        event = {"type": "info", "name": 0, "displayName": "YN0000", "data": "No audit suggestions"}
        self.assertEqual("NO_KNOWN_FINDINGS", normalize_audit(json.dumps(event), 0, "yarn-berry")["verdict"])

    def test_berry_error_ndjson_rejected(self):
        with self.assertRaises(AuditError):
            normalize_audit('{"type":"error","name":35,"data":"request failed"}', 1, "yarn-berry")

    def test_berry_old_raw_empty(self):
        self.assertEqual("NO_KNOWN_FINDINGS", normalize_audit("{}", 0, "yarn-berry")["verdict"])

    def test_berry_old_raw_findings(self):
        report = normalize_audit(json.dumps({"sample": [{"id": 7, "severity": "info", "title": "notice", "vulnerable_versions": "<2"}]}), 1, "yarn-berry")
        self.assertEqual(1, report["counts"]["advisoriesBySeverity"]["info"])


class AuditInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "package.json").write_text('{"name":"demo"}')
        (self.root / "package-lock.json").write_text('{}')

    def test_defaults_and_lock_owner(self):
        request = validate_request(str(self.root))
        self.assertEqual(str(self.root.resolve()), request["root"])
        self.assertFalse(request["includeLag"])

    def test_invalid_deadlines(self):
        for value in (True, -1, 0, 1, 601, math.inf, math.nan, "20"):
            with self.subTest(value=value), self.assertRaises(AuditError):
                validate_request(str(self.root), deadline_seconds=value)

    def test_invalid_lag_parameters(self):
        for options in ({"include_lag": 1}, {"lag_months": True}, {"lag_months": 2}):
            with self.subTest(options=options), self.assertRaises(AuditError):
                validate_request(str(self.root), **options)

    def test_relative_project_rejected(self):
        with self.assertRaises(AuditError):
            validate_request(".")

    def test_missing_lock_never_triggers_resolution(self):
        (self.root / "package-lock.json").unlink()
        (self.root / ".git").mkdir()
        with self.assertRaises(AuditError) as caught:
            validate_request(str(self.root))
        self.assertEqual("lockfile-missing", caught.exception.code)

    def test_registry_credentials_and_shell_content_rejected(self):
        for value in ("https://user:pass@registry.test", "file:///tmp/a", "https://r.test/?token=abc", "https://r.test/#x", "https://r.test/a&b", "https://r.test/\n"):
            with self.subTest(value=value), self.assertRaises(AuditError):
                checked_registry(value)

    def test_local_mock_registry_allowed_explicitly(self):
        self.assertEqual("http://127.0.0.1:1234", checked_registry("http://127.0.0.1:1234/"))

    def test_ambiguous_locks_rejected(self):
        with self.assertRaises(AuditError):
            detect_manager({"package.json": b'{}', "package-lock.json": b'{}', "yarn.lock": b''}, "auto")

    def test_classic_and_berry_are_distinguished_without_running_project_code(self):
        for lock, expected in ((b'# yarn lockfile v1\n', "yarn-classic"), (b'__metadata:\n  version: 8\n', "yarn-berry")):
            self.assertEqual(expected, detect_manager({"package.json": b'{}', "yarn.lock": lock}, "auto"))

    def test_manager_pin_mismatch_rejected(self):
        with self.assertRaises(AuditError):
            detect_manager({"package.json": b'{"packageManager":"yarn@4.9.2"}', "yarn.lock": b'# yarn lockfile v1\n'}, "auto")

    def test_pnpm_is_explicitly_out_of_scope(self):
        with self.assertRaises(AuditError):
            detect_manager({"package.json": b'{}', "pnpm-lock.yaml": b''}, "auto")

    def test_sensitive_config_is_not_forwarded_as_executable_plugins(self):
        runner = Runner(self.root, {"PATH": os.environ.get("PATH", ""), "TEST_TOKEN": "super-secret-value"})
        inputs = {".npmrc": b'registry=https://private.test\n//private.test/:_authToken=${TEST_TOKEN}\nomit=dev\n',
                  ".yarnrc.yml": b'npmRegistryServer: https://private.test\nplugins:\n  - path: /evil/plugin.js\nyarnPath: /evil/yarn.js\nnpmAuditIgnoreAdvisories: [123]\n'}
        (self.root / "project").mkdir()
        with patch("pathlib.Path.home", return_value=self.root / "unused-home"):
            registry, _ = configure(self.root / "project", inputs, runner, None, "yarn-berry")
        self.assertEqual("https://private.test", registry)
        self.assertNotIn("plugins", (self.root / "project" / ".deploom-audit.yarnrc.yml").read_text())
        self.assertNotIn("yarnPath", (self.root / "project" / ".deploom-audit.yarnrc.yml").read_text())
        self.assertNotIn("omit", (self.root / "project" / ".npmrc").read_text())
        self.assertNotIn("super-secret-value", json.dumps(runner.redact({"message": "Bearer super-secret-value"})))

    def test_runner_cannot_install_fix_or_run_scripts(self):
        runner = Runner(self.root, {})
        for cmd in (["npm", "install"], ["npm", "audit", "fix"], ["yarn", "run", "audit"], ["bash", "evil"]):
            with self.subTest(cmd=cmd), self.assertRaises(AuditError):
                runner.run(cmd, self.root)

    def test_runner_cannot_execute_inside_source(self):
        runner = Runner(self.root / "private", {})
        with self.assertRaises(AuditError):
            runner.run(["npm", "audit"], self.root)

    def test_compact_keeps_totals_and_marks_clipping(self):
        report = {"ok": True, "security": normalize_audit(json.dumps(npm_report()), 1, "npm"),
                  "lag": {}, "commands": [], "coverage": {"manifests": ["package.json"], "unresolvedEdges": []}}
        path = self.root / "audit.json"
        path.write_text(json.dumps(report))
        compact = compact_audit(report, str(path), max_items=0)
        self.assertEqual(1, compact["security"]["counts"]["uniqueAdvisories"])
        self.assertEqual([], compact["security"]["advisories"])
        self.assertTrue(compact["projection"]["requiresArtifactRead"])


if __name__ == "__main__":
    unittest.main()
