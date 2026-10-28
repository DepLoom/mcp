"""Offline Core -> published Draft -> MCP regressions for exact-target health.

Registry/artifact/OSV inputs are synthetic; selection, registry enrichment,
Z3 solving, Draft publication and both MCP projections use production code.
No network, package installation or application migration is performed.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploom_mcp.blueprint import (
    BlueprintPolicy, _validate_blueprint_schema, transcode_upgrade_blueprint,
)
from deploom_mcp.decisions import enrich_decisions, workspace_coverage
from deploom_mcp.server import compact_blueprint

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "upstream" / "deploom"


class TargetAssessmentIntegrationTests(unittest.TestCase):
    PROJECT = "synthetic-target-health"
    PACKAGE = "sample-library"
    REGISTRY = "https://registry.example.invalid"

    @classmethod
    def setUpClass(cls):
        # Use exactly the pinned Core; a missing inventory/dependency is a test
        # failure rather than a skip of the integration gate.
        previous_path = list(sys.path)
        cls.addClassCleanup(lambda: sys.path.__setitem__(slice(None), previous_path))
        sys.path.insert(0, str(CORE))
        cls.core = importlib.import_module("dependency_live_roadmap_generator")
        if Path(cls.core.__file__).resolve() != (CORE / "dependency_live_roadmap_generator.py").resolve():
            raise AssertionError("integration fixture imported a different Core checkout")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.project.joinpath("package.json").write_text(json.dumps({
            "name": self.PROJECT, "version": "1.0.0", "packageManager": "npm@10.0.0",
            "dependencies": {self.PACKAGE: "1.0.0"},
        }), encoding="utf-8")
        self.project.joinpath("package-lock.json").write_text(json.dumps({
            "name": self.PROJECT, "version": "1.0.0", "lockfileVersion": 3,
            "packages": {"": {"dependencies": {self.PACKAGE: "1.0.0"}},
                         f"node_modules/{self.PACKAGE}": {"version": "1.0.0"}},
        }), encoding="utf-8")
        self.inputs = {p.name: p.read_bytes() for p in self.project.iterdir()}
        self.intent = {
            "targetMode": "advisory", "objective": "latest-compatible-in-approved-lines",
            "approvedVersionLines": {self.PACKAGE: [1]},
        }
        self.policy = {"targetLevel": "yellow", "minLagOkPct": 80,
                       "maxKnownCritical": 0, "maxKnownHigh": 1}
        environment = patch.dict(os.environ, {
            "DEPLOOM_BASELINE_INTENT_JSON": json.dumps(self.intent),
            "DEPLOOM_ACCEPTANCE_POLICY_JSON": json.dumps(self.policy),
            "DEPLOOM_ACCEPTANCE_POLICY_BY_PROJECT": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        for name, value in (("_BASELINE_INTENT_CACHE_RAW", "<fixture-unset>"),
                            ("_BASELINE_INTENT_CACHE", {}),
                            ("_ARTIFACTS_BASE_FOR_DRAFT", []),
                            ("EFFECTIVE_MIN_LAG_OK_PCT", 80),
                            ("EFFECTIVE_TARGET_LEVEL", "yellow")):
            state = patch.object(self.core, name, value)
            state.start()
            self.addCleanup(state.stop)
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("unexpected network"))
        self.network = network.start()
        self.addCleanup(network.stop)
        self.client = self.core.LiveDataClient(self.REGISTRY, timeout=1, batch_size=10, sleep_sec=0)
        self.client.draft = True
        self.addCleanup(self.client.session.close)

    def project_target(self, target_security):
        core = self.core
        row = core.DependencyRow(
            project=self.PROJECT, package_dir=str(self.project), name=self.PACKAGE,
            kind="runtime", requested_spec="1.0.0", current_version="1.0.0",
            current_source="package-lock.json", latest_version="2.0.0", current_vulns="0",
            min_no_critical="1.0.0", min_no_high="1.0.0", min_no_vuln="1.0.0",
            min_lag_12m="1.0.0", min_lag_9m="1.0.0", min_lag_6m="1.0.0", min_lag_3m="1.0.0",
            group=1, reason="Synthetic latest-compatible candidate", notes="",
            draft_scan_state="done",
            vuln_evidence_by_version={"1.0.0": "0", "1.5.0": target_security},
        )
        versions = {}
        for version in ("1.0.0", "1.5.0", "2.0.0"):
            tarball = f"{self.REGISTRY}/artifact/{self.PACKAGE}/{version}.tgz"
            versions[version] = {"name": self.PACKAGE, "version": version,
                                 "dist": {"tarball": tarball}}
            self.client.registry_artifact_cache[(self.PACKAGE, version)] = {
                "status": "available", "tarballUrl": tarball,
            }
        self.client.npm_cache[self.PACKAGE] = {"versions": versions}
        rows = {self.PROJECT: [row]}
        health = core.enrich_project_targets(rows, {})
        core.apply_decision_contract_targets(rows, self.client)
        self.assertEqual(row.target_yellow, "1.5.0")
        core.capture_desired_targets(rows)
        core.enrich_registry_target_evidence(rows, self.client)
        self.assertEqual(row.registry_artifacts["1.5.0"]["status"], "available")
        statuses = {}
        # wraps observes the real solver; no assignment or solver result is
        # injected. Solving the selected yellow variant binds the assessment
        # to the final assignment rather than a pre-solver candidate.
        with patch.object(core, "_run_z3_peer_component", wraps=core._run_z3_peer_component) as exact:
            assignments = core.resolve_peer_compatibility(
                rows, self.client, modes=("yellow",), apply_results=True,
                solver_statuses_out=statuses, partial_on_incomplete=True,
            )
        exact.assert_called()
        self.assertEqual(statuses[self.PROJECT]["yellow"][self.PACKAGE], "optimal")
        self.assertEqual(assignments[self.PROJECT]["yellow"][self.PACKAGE], "1.5.0")
        self.assertEqual(row.target_yellow, "1.5.0")
        self.assertFalse(row.peer_compat_unresolved)
        spec = core.ProjectSpec(self.PROJECT, self.project)
        core.set_draft_artifacts_base(self.root / "artifacts")
        manifest = core.publish_draft_result(
            run_id="target-health-regression", workspace_id="synthetic-workspace",
            project_id=self.PROJECT, mode="draft", rows_by_project=rows,
            projects_by_name={self.PROJECT: spec}, health_by_project=health,
            status="DRAFT_READY", partial_reason=None, deadline=core.DeadlineClock(30.0),
            settings_snapshot={"intentJson": json.dumps(self.intent), "draftMaxCandidates": "3",
                               "targetLevel": "yellow", "minLagOkPct": 80},
            language="en",
        )
        plan = json.loads(Path(manifest["artifacts"]["plan"]).read_text(encoding="utf-8"))
        # This is the missed cross-version case: current evidence is complete
        # and no ordinary row/diagnostic UNKNOWN advertises the target issue.
        self.assertEqual(manifest["metadata"]["securityKnown"], 1)
        self.assertEqual(manifest["metadata"]["securityTotal"], 1)
        self.assertEqual(manifest["metadata"]["metadataKnown"], 1)
        self.assertEqual(plan["unknowns"], [])
        self.assertEqual(plan["conflicts"], [])
        self.assertEqual(plan["unresolved"], [])
        blueprint = transcode_upgrade_blueprint(
            plan, manifest, project_path=str(self.project), project_name=self.PROJECT,
            policy=BlueprintPolicy(intent=self.intent, acceptance_policy=self.policy),
            artifacts=manifest["artifacts"], blueprint_path=str(self.root / "blueprint.json"),
        )
        blueprint["workspaceCoverage"] = workspace_coverage(self.project)
        enrich_decisions(blueprint)
        self.assertIsNone(_validate_blueprint_schema(blueprint))
        compact = compact_blueprint(blueprint)
        self.assertEqual(compact["dependencies"][0]["target"], "1.5.0")
        self.assertEqual(compact["status"]["authority"], "PLANNING_ONLY")
        self.assertEqual(compact["status"]["verificationStatus"], "NOT_VERIFIED")
        self.assertEqual(compact["status"]["compatibility"], "UNKNOWN")
        self.assertTrue(compact["decisionCompleteness"]["projectionComplete"])
        self.assertFalse(compact["decisionCompleteness"]["requiresArtifactRead"])
        self.assertEqual(self.inputs, {p.name: p.read_bytes() for p in self.project.iterdir()})
        self.network.assert_not_called()
        return manifest, blueprint, compact

    def assert_target_projection(self, compact, goal, known, unknown, critical):
        assessment = compact["targetAssessment"]
        self.assertEqual(assessment["scope"], "FINAL_PLANNED_ASSIGNMENT")
        for projection in (assessment["aggregate"], assessment["projects"][self.PROJECT]):
            self.assertEqual(projection["postPlanGoal"], goal)
            self.assertEqual(projection["postPlanSecurityKnown"], known)
            self.assertEqual(projection["postPlanSecurityUnknown"], unknown)
            self.assertEqual(projection["postPlanSecurityTotal"], 1)
            self.assertEqual(projection["postPlanCritical"], critical)

    def test_known_current_unknown_target_requires_review_without_invented_blocker(self):
        manifest, _blueprint, compact = self.project_target("unknown")
        self.assertEqual(manifest["metadata"]["postPlanGoal"], "unknown")
        self.assertFalse(compact["decisionCompleteness"]["evidenceComplete"])
        self.assertTrue(compact["dependencies"][0]["needsReview"])
        self.assertEqual(compact["decisionCompleteness"]["blockerCount"], 0)
        reasons = [e for e in compact["decisionEvidence"] if e["code"] == "TARGET_SECURITY_UNKNOWN"]
        self.assertTrue(reasons)
        self.assertTrue(all(e["severity"] == "REVIEW" and not e.get("package") for e in reasons))
        self.assert_target_projection(compact, "unknown", 0, 1, 0)

    def test_known_current_critical_target_preserves_complete_evidence_and_blocker(self):
        manifest, _blueprint, compact = self.project_target("C:1")
        self.assertEqual(manifest["metadata"]["postPlanGoal"], "blocked")
        self.assertTrue(compact["decisionCompleteness"]["evidenceComplete"])
        self.assertTrue(compact["dependencies"][0]["needsReview"])
        self.assertGreaterEqual(compact["decisionCompleteness"]["blockerCount"], 1)
        reasons = [e for e in compact["decisionEvidence"] if e["code"] == "TARGET_POLICY_BLOCKED"]
        self.assertTrue(reasons)
        self.assertTrue(all(e["severity"] == "BLOCKER" and not e.get("package") for e in reasons))
        self.assert_target_projection(compact, "blocked", 1, 0, 1)

    def test_known_clean_target_is_a_positive_control(self):
        manifest, _blueprint, compact = self.project_target("0")
        self.assertEqual(manifest["metadata"]["postPlanGoal"], "feasible")
        self.assertTrue(compact["decisionCompleteness"]["evidenceComplete"])
        self.assertFalse(compact["dependencies"][0]["needsReview"])
        self.assertEqual(compact["decisionCompleteness"]["blockerCount"], 0)
        self.assert_target_projection(compact, "feasible", 1, 0, 0)


if __name__ == "__main__":
    unittest.main()
