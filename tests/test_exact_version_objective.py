"""MCP explicit-version objective contract tests (no network/agent)."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploom_mcp.server import _build_request
from deploom_mcp.decisions import normalize_decision_contract
from deploom_mcp.blueprint import _exec_env

class ExactObjectiveBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.project=self.base/"app"
        self.project.mkdir()
        (self.project/"package.json").write_text(json.dumps({
            "name":"synthetic-app","private":True,"dependencies":{
                "sample-lib":"1.0.0","companion":"7.0.0"}}))

    def req(self,**overrides):
        default={
            "project_path":str(self.project),
            "target_level":"yellow","min_lag_ok_pct":80,"deadline_seconds":20.0,
            "include_prerelease":False,"registry":None,"registry_metadata_ttl":0.0,
            "output_dir":None,"exclude":None,
            "objective":"explicit-version-targets",
            "target_mode":"pinned",
            "mutable_packages":["sample-lib"],
            "approved_version_lines":{"sample-lib":[2]},
            "exact_targets":{"sample-lib":"2.3.0"},
        }
        default.update(overrides)
        with patch("deploom_mcp.server._state_dir",return_value=self.base/"state"),patch(
            "deploom_mcp.server._ensure_state_dir",
            side_effect=lambda: (self.base/"state").mkdir(exist_ok=True) or self.base/"state"):
            return _build_request(**default)

    def test_core_subprocess_receives_exact_v2_semantic_intent(self):
        error,request=self.req()
        self.assertIsNone(error)
        contract=request["policy"].intent
        self.assertEqual(2,contract["version"])
        self.assertEqual({"sample-lib":"2.3.0"},contract["exactTargets"])
        self.assertEqual(["sample-lib"],contract["mutablePackages"])
        self.assertEqual({"sample-lib":[2]},contract["approvedVersionLines"])
        env=_exec_env(request["policy"],"run","work","project",request["options"])
        self.assertEqual(contract,json.loads(env["DEPLOOM_BASELINE_INTENT_JSON"]))
        self.assertNotIn("companion",contract["exactTargets"])

    def test_v1_defaults_and_request_backward_compatibility(self):
        old=normalize_decision_contract(None)
        self.assertEqual({
            "version":1,"targetMode":"pinned","objective":"minimum-change-to-policy",
            "mutablePackages":None,"approvedVersionLines":{},
        },old)
        error,request=self.req(objective="minimum-change-to-policy",
                               exact_targets=None,approved_version_lines=None,
                               mutable_packages=None)
        self.assertIsNone(error)
        self.assertEqual(old,request["policy"].intent)

    def test_conflicting_or_unauthorized_inputs_fail_before_state_created(self):
        bad=[
          {"exact_targets":{"companion":"8.0.0"}},
          {"exact_targets":{"sample-lib":"2.3"}},
          {"exact_targets":{"sample-lib":"^2.3.0"}},
          {"approved_version_lines":{"sample-lib":[3]}},
          {"exact_targets":{"sample-lib":"2.3.0"},"exclude":["sample-lib"]},
          {"exact_targets":{"sample-lib":"2.3.0"},"required_packages":["sample-lib"]},
          {"exact_targets":{}},
          {"objective":"latest-compatible-in-approved-lines"},
        ]
        for item in bad:
            with self.subTest(item=item):
                error,request=self.req(**item)
                self.assertIsNone(request)
                self.assertEqual("invalid-intent",error["code"])

    def test_v2_schema_validates_exact_semver_and_version(self):
        import jsonschema
        from deploom_mcp.distribution import blueprint_schema_path
        document=json.loads(blueprint_schema_path().read_text())
        definition=document["definitions"]["decisionContract"]
        jsonschema.validate(normalize_decision_contract({
            "objective":"explicit-version-targets",
            "exactTargets":{"sample-lib":"2.3.0"}}),definition)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"version":1,"targetMode":"pinned",
              "objective":"explicit-version-targets","mutablePackages":None,
              "approvedVersionLines":{},"exactTargets":{"sample-lib":"2.3.0"}},definition)

if __name__=="__main__":
    unittest.main()
