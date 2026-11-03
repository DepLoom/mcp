"""Generic required-package MCP -> existing Core intent bridge.

Zero registry/model calls. Do NOT assert that required pins an exact version;
the Core V1 contract only has approved major lines + required movement.
"""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from deploom_mcp.server import _build_request, compact_blueprint
from deploom_mcp.blueprint import _exec_env


class RequiredLegacyIntentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "fixture"
        self.project.mkdir()
        self.state = Path(self.temp.name) / "state"

    def request(self, **kwargs):
        args = dict(
            project_path=str(self.project), target_level="yellow",
            min_lag_ok_pct=80, deadline_seconds=30.0,
            include_prerelease=False, registry=None,
            registry_metadata_ttl=0.0, output_dir=None,
            exclude=None, mutable_packages=["pkg-a"],
            approved_version_lines={"pkg-a": [5]},
        )
        args.update(kwargs)
        with patch("deploom_mcp.server._state_dir", return_value=self.state), patch(
            "deploom_mcp.server._ensure_state_dir",
            side_effect=lambda: self.state.mkdir(exist_ok=True) or self.state,
        ):
            return _build_request(**args)

    def test_requested_required_intent_crosses_core_subprocess_boundary(self):
        error, result = self.request(required_packages=["pkg-a", "pkg-a"])
        self.assertIsNone(error)
        policy = result["policy"]
        self.assertEqual({"pkg-a": "required"}, policy.intent["policies"])
        self.assertEqual([5], policy.intent["approvedVersionLines"]["pkg-a"])
        self.assertEqual(["pkg-a"], policy.intent["mutablePackages"])
        env = _exec_env(policy, "run", "work", "proj", result["options"])
        sent = json.loads(env["DEPLOOM_BASELINE_INTENT_JSON"])
        self.assertEqual({"pkg-a": "required"}, sent["policies"])
        self.assertNotIn("exactTargets", sent)
        self.assertNotIn("exactVersions", sent)
        self.assertEqual("pinned", sent["targetMode"])

    def test_absent_required_leaves_existing_contract_byte_compatible(self):
        error, result = self.request()
        self.assertIsNone(error)
        self.assertNotIn("policies", result["policy"].intent)
        self.assertEqual({"pkg-a": [5]}, result["policy"].intent["approvedVersionLines"])

    def test_required_cannot_change_immutable_or_excluded_package(self):
        for values in (
            {"required_packages":["pkg-b"]},
            {"required_packages":["pkg-a"],"exclude":["pkg-a"]},
            {"required_packages":[False]},
            {"required_packages":[""]},
            {"required_packages":"pkg-a"},
            {"required_packages":[1]},
        ):
            with self.subTest(values=values):
                error, result = self.request(**values)
                self.assertIsNone(result)
                self.assertEqual("invalid-intent", error["code"])
        # An out-of-scope requirement must not create a runner state directory.
        with patch("deploom_mcp.server._state_dir", side_effect=AssertionError("early validation not applied")):
            error, result = _build_request(
                project_path=str(self.project), target_level="yellow",
                min_lag_ok_pct=80, deadline_seconds=30,
                include_prerelease=False, registry=None,
                registry_metadata_ttl=0.0, output_dir=None,exclude=None,
                mutable_packages=["pkg-a"],required_packages=["pkg-b"])
        self.assertEqual("invalid-intent", error["code"])

    def test_requested_scope_visible_without_claiming_exact_target(self):
        from unittest.mock import MagicMock
        # Existing compact projection must not invent a PROPOSED status.
        from deploom_mcp.server import _policy_snapshot
        error, result = self.request(required_packages=["pkg-a"])
        self.assertIsNone(error)
        summary = _policy_snapshot(result["policy"])
        self.assertEqual({"pkg-a": [5]}, summary["decisionContract"]["approvedVersionLines"])
        self.assertNotIn("policies", summary["decisionContract"])


if __name__=="__main__":
    unittest.main()
