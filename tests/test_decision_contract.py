"""Synthetic regression cases for the decision boundary; no registry or migration."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploom_mcp.blueprint import (
    BlueprintOptions, BlueprintPolicy, _exec_env, _prepare_argv_full, _prepare_generation,
    _validate_blueprint_schema, generate_upgrade_blueprint, transcode_upgrade_blueprint,
)
from deploom_mcp.decisions import (
    enrich_decisions, normalize_decision_contract, runtime_provenance, workspace_coverage,
)
from deploom_mcp.server import _build_request, compact_blueprint, get_upgrade_plan

ROOT = Path(__file__).resolve().parents[1]


def clean_target_health():
    return {"postPlanSecurityKnown": 1, "postPlanSecurityUnknown": 0,
            "postPlanSecurityTotal": 1, "postPlanCritical": 0, "postPlanHigh": 0,
            "postPlanModerate": 0, "postPlanLow": 0, "postPlanGoal": "feasible"}


def blueprint(*, conflict=False, capped=False, migration=False):
    row = {"project": "app", "package": "ui-runtime", "kind": "runtime", "current": "1.0.0",
           "target": "2.0.0", "status": "proposed", "reason": "Candidate update", "group": 1,
           "candidateSearchTruncated": capped,
           "breakingChanges": ["Review runtime migration"] if migration else [], "migrationNotes": []}
    plan = {"proposals": [{"project": "app", "rows": [row], "health": {"total": 1, **clean_target_health()}}],
            "counts": {"total": 1, "proposed": 1}, "unknowns": [], "unresolved": [],
            "conflicts": [{"package": "ui-runtime", "active": True, "stage": "final", "notes": [
                "PEER_RESOLUTION_DEFERRED: desired=2.0.0; resolved=current 1.0.0; consumer requires ^1"
            ]}] if conflict else []}
    manifest = {"status": "DRAFT_READY", "runId": "run-a", "generatedAt": None,
                "verificationStatus": "NOT_VERIFIED", "authority": "PLANNING_ONLY", "compatibility": "UNKNOWN",
                "metadata": clean_target_health(), "inputFilesByProject": {"app": [{"name": "package.json"}]}}
    bp = transcode_upgrade_blueprint(plan, manifest, project_path="/synthetic/app", project_name="app",
                                     policy=BlueprintPolicy(), artifacts={"plan": "/artifacts/a/plan.json"},
                                     blueprint_path="/artifacts/a/blueprint.json")
    bp["workspaceCoverage"] = {"mode": "SINGLE_MANIFEST_PLANNING", "jointAssignment": "NOT_COMPUTED",
                               "workspaceGraphCoverage": "SINGLE_PROJECT", "discoveryComplete": True,
                               "reconciliationRequired": False}
    enrich_decisions(bp)
    return bp


class DecisionEvidenceTests(unittest.TestCase):
    def test_peer_blocker_visible_even_without_row_truncation(self):
        result = compact_blueprint(blueprint(conflict=True))
        self.assertFalse(result["truncation"]["truncated"])
        self.assertEqual(result["decisionEvidence"][0]["code"], "PEER_CONSTRAINT")
        self.assertEqual(result["decisionEvidence"][0]["severity"], "BLOCKER")
        self.assertEqual(result["decisionEvidence"][0]["evidenceRef"], "#/conflicts/0")
        self.assertTrue(result["dependencies"][0]["needsReview"])
        self.assertEqual(result["decisionCompleteness"]["nextAction"], "RECONCILE_CONSTRAINTS")

    def test_omitted_blocker_still_requires_artifact_and_keeps_count(self):
        result = compact_blueprint(blueprint(conflict=True), max_inline_evidence=0)
        self.assertEqual(result["decisionEvidence"], [])
        self.assertEqual(result["decisionCompleteness"]["blockerCount"], 1)
        self.assertFalse(result["decisionCompleteness"]["projectionComplete"])
        self.assertTrue(result["decisionCompleteness"]["requiresArtifactRead"])
        self.assertIn("decisionEvidence", result["truncation"]["limitsHit"])

    def test_legacy_conflict_note_is_review_not_a_proven_active_blocker(self):
        bp = blueprint(conflict=True)
        bp["conflicts"][0].pop("active")
        bp["conflicts"][0].pop("stage")
        enrich_decisions(bp)
        result = compact_blueprint(bp)
        note = result["decisionEvidence"][0]
        self.assertEqual(note["code"], "PEER_CONSTRAINT")
        self.assertEqual(note["lifecycle"], "UNRESOLVED_LEGACY")
        self.assertEqual(note["severity"], "REVIEW")
        self.assertEqual(result["decisionCompleteness"]["blockerCount"], 0)

    def test_complete_projection_is_not_complete_search(self):
        result = compact_blueprint(blueprint(capped=True))
        self.assertFalse(result["truncation"]["truncated"])
        self.assertTrue(result["decisionCompleteness"]["projectionComplete"])
        self.assertFalse(result["decisionCompleteness"]["evidenceComplete"])
        self.assertEqual(result["recovery"]["nextAction"], "EXPAND_CANDIDATE_EVIDENCE")

    def test_missing_coverage_cannot_claim_complete_evidence(self):
        self.assertFalse(compact_blueprint(blueprint())["decisionCompleteness"]["evidenceComplete"])
        bp = blueprint()
        bp["summary"].update(metadataKnown=1, metadataTotal=1, securityKnown=1, securityTotal=1)
        self.assertTrue(compact_blueprint(bp)["decisionCompleteness"]["evidenceComplete"])
        # Current coverage alone cannot certify a legacy target assessment.
        bp["projections"] = {}
        bp["perProject"]["app"] = {"total": 1}
        self.assertFalse(compact_blueprint(bp)["decisionCompleteness"]["evidenceComplete"])

    def test_migration_reason_requires_review_but_ordinary_recommendation_does_not(self):
        self.assertFalse(blueprint()["dependencies"][0]["needsReview"])
        bp = blueprint(migration=True)
        self.assertTrue(bp["dependencies"][0]["needsReview"])
        self.assertTrue(bp["recommendationGroups"][0]["needsReview"])
        self.assertEqual(bp["summary"]["needsReview"], 1)

    def test_excluded_declaration_stays_resolved_without_hiding_peer_evidence(self):
        bp = blueprint(conflict=True, capped=True, migration=True)
        row = bp["dependencies"][0]
        row.update(status="EXCLUDED", target=row["current"], needsReview=False,
                   reason="Explicit health-scope exclusion")
        enrich_decisions(bp)
        self.assertFalse(row["needsReview"])
        self.assertEqual(row["nextAction"], "KEEP_EXCLUDED_CURRENT")
        self.assertEqual(bp["summary"]["needsReview"], 0)
        self.assertFalse(bp["recommendationGroups"][0]["needsReview"])
        codes = {item["code"] for item in row["decisionReasons"]}
        self.assertTrue({"PEER_CONSTRAINT", "CANDIDATE_CAP", "MIGRATION_REVIEW"}.issubset(codes))
        compact = compact_blueprint(bp)
        self.assertEqual(compact["decisionCompleteness"]["blockerCount"], 1)
        self.assertEqual(compact["decisionCompleteness"]["nextAction"], "RECONCILE_CONSTRAINTS")
        self.assertFalse(compact["decisionCompleteness"]["evidenceComplete"])
        self.assertFalse(compact["dependencies"][0]["needsReview"])

    def test_semantic_evidence_fits_small_budget_without_silent_loss(self):
        bp = blueprint(conflict=True)
        bp["decisionEvidence"][0]["message"] = "constraint evidence " * 10000
        result = compact_blueprint(bp, max_response_bytes=5200)
        wire_size = len(json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8"))
        self.assertLessEqual(wire_size, 5200)
        self.assertEqual(result["truncation"]["bytes"], wire_size)
        self.assertTrue(result["decisionCompleteness"]["requiresArtifactRead"])
        self.assertEqual(result["decisionCompleteness"]["blockerCount"], 1)

    def test_decision_digest_ignores_run_identity_but_changes_for_assignment(self):
        first = blueprint()
        second = copy.deepcopy(first)
        second["run"]["runId"] = "other-run"
        second["generatedAt"] = "later"
        second["blueprintFile"] = "/other/run/blueprint.json"
        second["artifacts"]["plan"] = "/other/run/plan.json"
        enrich_decisions(second)
        self.assertEqual(first["identities"], second["identities"])
        second["dependencies"][0]["target"] = "2.1.0"
        enrich_decisions(second)
        self.assertNotEqual(first["identities"]["decisionDigest"], second["identities"]["decisionDigest"])

    def test_decision_digest_includes_reasons_and_scope(self):
        first = blueprint()
        changed = copy.deepcopy(first)
        changed["dependencies"][0]["reason"] = "Different material reason"
        enrich_decisions(changed)
        self.assertNotEqual(first["identities"]["decisionDigest"], changed["identities"]["decisionDigest"])
        changed = copy.deepcopy(first)
        changed["workspaceCoverage"]["reconciliationRequired"] = True
        enrich_decisions(changed)
        self.assertNotEqual(first["identities"]["decisionDigest"], changed["identities"]["decisionDigest"])

    def test_evidence_digest_tracks_published_security_and_normalizes_run_paths(self):
        first = blueprint()
        second = copy.deepcopy(first)
        first["dependencies"][0]["reason"] = "See /artifacts/a/plan.json for evidence"
        second["artifacts"]["plan"] = "/other/run/plan.json"
        second["dependencies"][0]["reason"] = "See /other/run/plan.json for evidence"
        enrich_decisions(first)
        enrich_decisions(second)
        self.assertEqual(first["identities"]["evidenceDigest"], second["identities"]["evidenceDigest"])
        second["perProject"]["app"]["critical"] = 1
        enrich_decisions(second)
        self.assertNotEqual(first["identities"]["evidenceDigest"], second["identities"]["evidenceDigest"])

    def test_unchanged_evidence_retry_is_explicit(self):
        bp = blueprint(capped=True)
        previous = bp["identities"]["evidenceDigest"]
        enrich_decisions(bp, candidate_cap=3, previous_evidence_digest=previous)
        self.assertEqual(bp["recovery"]["previousEvidenceComparison"], "UNCHANGED_EVIDENCE")
        self.assertEqual(bp["recovery"]["suggestedDraftMaxCandidates"], 6)
        self.assertIn("deadline does not widen", bp["recovery"]["note"])

    def test_additive_full_contract_validates(self):
        self.assertIsNone(_validate_blueprint_schema(blueprint(conflict=True, capped=True, migration=True)))


class WorkspaceCoverageTests(unittest.TestCase):
    def test_root_and_child_never_claim_joint_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            child = root / "packages" / "app"
            child.mkdir(parents=True)
            (root / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
            (child / "package.json").write_text("{}")
            for requested in (root, child):
                coverage = workspace_coverage(requested)
                self.assertEqual(len(coverage["discoveredManifests"]), 2)
                self.assertEqual(coverage["evaluatedManifests"], [str(requested / "package.json")])
                self.assertTrue(coverage["reconciliationRequired"])
                self.assertEqual(coverage["jointAssignment"], "NOT_COMPUTED")
                self.assertEqual(coverage["workspaceGraphCoverage"], "PARTIAL")

    def test_discovery_limit_is_unknown_coverage_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
            (root / "packages" / "app").mkdir(parents=True)
            coverage = workspace_coverage(root, max_directories=1)
            self.assertFalse(coverage["discoveryComplete"])
            self.assertTrue(coverage["reconciliationRequired"])

    def test_unreadable_manifest_is_unknown_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "package.json").write_text("not json")
            coverage = workspace_coverage(root)
            self.assertFalse(coverage["discoveryComplete"])
            self.assertTrue(coverage["reconciliationRequired"])
            self.assertTrue(coverage["discoveryErrors"])

    def test_unreadable_workspace_subtree_is_unknown_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            child = root / "packages" / "app"
            child.mkdir(parents=True)
            (root / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
            (child / "package.json").write_text("{}")
            inaccessible = root / "packages"
            original_scandir = os.scandir

            def guarded_scandir(path):
                if Path(path) == inaccessible:
                    raise PermissionError(13, "Permission denied", str(path))
                return original_scandir(path)

            with patch("deploom_mcp.decisions.os.scandir", side_effect=guarded_scandir):
                coverage = workspace_coverage(root)
            self.assertFalse(coverage["discoveryComplete"])
            self.assertTrue(coverage["reconciliationRequired"])
            self.assertIn(str(inaccessible), coverage["discoveryErrors"])
            self.assertNotIn(str(child / "package.json"), coverage["discoveredManifests"])


class RequestContractTests(unittest.TestCase):
    def test_interpreter_override_keeps_virtual_environment_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            (project / "package.json").write_text("{}")
            interpreter = root / "venv" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            try:
                interpreter.symlink_to(sys.executable)
            except OSError:
                self.skipTest("platform does not permit creating interpreter symlinks")
            error, prepared = _prepare_generation(str(project), BlueprintPolicy(),
                BlueprintOptions(python=str(interpreter), output_dir=str(root / "output"),
                    generator_path=str(ROOT / "tests/fixtures/fake_core/dependency_live_roadmap_generator.py")), None, None)
            self.assertIsNone(error)
            self.assertEqual(prepared.interpreter, str(interpreter.absolute()))
            self.assertNotEqual(prepared.interpreter, str(interpreter.resolve()))

    def test_invalid_cap_and_intent_fail_before_planning(self):
        for cap in (0, 129, True, 3.5):
            result = get_upgrade_plan(project_path="/missing/project", draft_max_candidates=cap)
            self.assertEqual(result["error"]["code"], "invalid-candidate-cap")
        for kwargs in ({"target_mode": "automatic"}, {"objective": "latest-at-any-cost"},
                       {"mutable_packages": "runtime"}, {"approved_version_lines": {"ui-runtime": [True]}},
                       {"approved_version_lines": []}):
            result = get_upgrade_plan(project_path="/missing/project", **kwargs)
            self.assertEqual(result["error"]["code"], "invalid-intent")

    def test_mutable_empty_is_preserved_and_not_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEPLOOM_MCP_STATE_DIR": tmp + "/state"}):
            error, request = _build_request(project_path=tmp + "/project", target_level="yellow",
                                           min_lag_ok_pct=80, deadline_seconds=600,
                                           include_prerelease=False, registry=None, registry_metadata_ttl=0,
                                           output_dir=None, exclude=[], target_mode="advisory",
                                           mutable_packages=[], draft_max_candidates=12)
            self.assertIsNone(error)
            policy, options = request["policy"], request["options"]
            self.assertEqual(policy.intent["mutablePackages"], [])
            self.assertEqual(policy.exclusions, ())
            self.assertEqual(options.draft_max_candidates, 12)
            env = _exec_env(policy, "r", "w", "p", options)
            self.assertEqual(json.loads(env["DEPLOOM_BASELINE_INTENT_JSON"])["targetMode"], "advisory")
            argv = _prepare_argv_full(Path("core.py"), Path("projects"), Path("artifacts"), Path("state"),
                                      Path("history"), "r", "w", "p", policy, options)
            self.assertEqual(argv[argv.index("--draft-max-candidates") + 1], "12")
            self.assertNotIn("--exclude-dependency", argv)

    def test_empty_allowed_major_set_is_not_default_current_major(self):
        contract = normalize_decision_contract({"approvedVersionLines": {"ui-runtime": []}})
        self.assertEqual(contract["approvedVersionLines"], {"ui-runtime": []})
        with self.assertRaises(ValueError):
            normalize_decision_contract({"mutablePackages": [], "policies": {"ui-runtime": "required"}})

    def test_legacy_override_cannot_silently_accept_new_objective(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            (project / "package.json").write_text("{}")
            result = generate_upgrade_blueprint(str(project),
                policy=BlueprintPolicy(intent={"objective": "latest-compatible-in-approved-lines"}),
                options=BlueprintOptions(generator_path=str(ROOT / "tests/fixtures/fake_core/dependency_live_roadmap_generator.py"),
                                         output_dir=str(Path(tmp) / "output")))
            self.assertFalse(result.ok)
            self.assertEqual(result.error["code"], "decision-contract-unsupported")

    def test_legacy_override_cannot_label_strict_core_prompt_as_advisory(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            (project / "package.json").write_text("{}")
            result = generate_upgrade_blueprint(str(project),
                policy=BlueprintPolicy(intent={"targetMode": "advisory"}),
                options=BlueprintOptions(generator_path=str(ROOT / "tests/fixtures/fake_core/dependency_live_roadmap_generator.py"),
                                         output_dir=str(Path(tmp) / "output")))
            self.assertFalse(result.ok)
            self.assertEqual(result.error["code"], "decision-contract-unsupported")

    def test_new_contract_roundtrip_requires_exact_applied_echo(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            (project / "package.json").write_text("{}")
            with patch.dict(os.environ, {"DEPLOOM_MCP_GENERATOR_PATH": str(ROOT / "tests/fixtures/fake_core/dependency_live_roadmap_generator.py"),
                                         "DEPLOOM_MCP_STATE_DIR": str(Path(tmp) / "state"),
                                         "FAKE_GENERATOR_CONTRACT": "echo"}):
                result = get_upgrade_plan(project_path=str(project), target_mode="advisory",
                                          objective="latest-compatible-in-approved-lines",
                                          mutable_packages=["pkg1"], approved_version_lines={"pkg1": [1, 2]},
                                          draft_max_candidates=12)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["policy"]["decisionContract"]["targetMode"], "advisory")
                self.assertEqual(result["policy"]["coreContractSupport"], "V1")
                self.assertEqual(result["recovery"]["draftMaxCandidates"], 12)
                self.assertEqual(result["planningOptions"]["capSource"], "CORE_MANIFEST")
                with patch.dict(os.environ, {"FAKE_GENERATOR_CONTRACT": "mismatch"}):
                    rejected = get_upgrade_plan(project_path=str(project), target_mode="advisory")
                self.assertFalse(rejected["ok"])
                self.assertEqual(rejected["error"]["code"], "decision-contract-mismatch")

    def test_override_provenance_hashes_actual_file_not_expected_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            generator = Path(tmp) / "alternate_core.py"
            generator.write_text("# synthetic override\n")
            first = runtime_provenance(generator)
            self.assertEqual(first["core"]["generatorSha256"], hashlib.sha256(generator.read_bytes()).hexdigest())
            self.assertEqual(first["skill"]["loadedClientContent"], "UNVERIFIED")
            generator.write_text("# changed synthetic override\n")
            second = runtime_provenance(generator)
            self.assertNotEqual(first["core"]["generatorSha256"], second["core"]["generatorSha256"])

    def test_fixture_inside_mcp_repository_does_not_inherit_core_git_revision(self):
        generator = ROOT / "tests/fixtures/fake_core/dependency_live_roadmap_generator.py"
        provenance = runtime_provenance(generator)
        self.assertIsNone(provenance["core"]["revision"])


if __name__ == "__main__":
    unittest.main()
