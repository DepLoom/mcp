"""Synthetic regressions for claim consistency; no project commands run."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import runpy
import unittest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "deploom-upgrade"
HELPER = runpy.run_path(str(SKILL / "scripts" / "validate_evidence.py"))
validate = HELPER["validate_report"]
counts = HELPER["change_counts"]


def plan_record() -> dict:
    text = (SKILL / "references" / "evidence-report.md").read_text(encoding="utf-8")
    return json.loads(re.search(r"```json\n(.*?)\n```", text, re.DOTALL).group(1))


def passed_record() -> dict:
    report = plan_record()
    inputs = {name: hashlib.sha256(name.encode()).hexdigest()
              for name in ("manifests", "lockfile", "environment", "source")}
    report.update(mode="EXECUTE", outcome="PASSED", finalInputs=inputs,
                  acceptance={"install": ["clean-install"], "checks": ["typecheck", "build"],
                              "runtime": ["affected-runtime"], "policy": ["installed-policy"]},
                  results={kind: "PASSED" for kind in ("install", "checks", "runtime", "policy")})
    report["decisions"] = [{"id": "utility", "package": "example-utility", "plannerStatus": "PROPOSED",
                             "plannerTarget": "1.4.2", "selectedTarget": "1.4.2", "targetOrigin": "planner",
                             "authorityRef": "task", "evidence": ["plan.json#/dependencies/0"]}]
    report["declarationChanges"] = [{"file": "package.json", "section": "dependencies", "package": "example-utility",
                                      "from": "1.3.0", "to": "1.4.2", "decisionId": "utility"}]
    report["counts"] = counts(report["declarationChanges"])
    for kind, names in report["acceptance"].items():
        for name in names:
            check = {"kind": kind, "check": name, "status": "PASSED", "command": f"synthetic {name}",
                     "exitCode": 0, "startedAt": "2026-01-01T00:00:00Z", "finishedAt": "2026-01-01T00:01:00Z",
                     "log": f"synthetic/{name}.log", "inputs": dict(inputs)}
            if kind == "install":
                check["installMode"] = "clean"
            if kind == "policy":
                check["scopeComplete"] = True
            report["verification"].append(check)
    return report


class EvidenceContractTests(unittest.TestCase):
    def test_published_plan_template_is_valid_without_execution(self):
        self.assertEqual(validate(plan_record()), [])

    def test_complete_synthetic_record_has_no_consistency_error(self):
        self.assertEqual(validate(passed_record()), [])
        report = passed_record()
        report["deliverables"] = [{"path": "docs/migration/report.md", "requiredBy": "task", "status": "MISSING"}]
        self.assertTrue(any("required delivery" in e for e in validate(report)))
        report["deliverables"][0]["status"] = "PRESENT"
        self.assertEqual(validate(report), [])

    def test_lock_resync_invalidates_prior_clean_install(self):
        report = passed_record()
        new_lock = hashlib.sha256(b"resynchronized lock").hexdigest()
        report["finalInputs"]["lockfile"] = new_lock
        for check in report["verification"]:
            if check["kind"] != "install":
                check["inputs"]["lockfile"] = new_lock
        self.assertTrue(any("install/clean-install: no PASSED evidence on final inputs" in e for e in validate(report)))
        # Retain the genuine historical pass, but stop claiming final success.
        report["outcome"] = "PARTIAL"
        report["results"]["install"] = "NOT_VERIFIED"
        self.assertEqual(validate(report), [])

    def test_incremental_install_cannot_replace_clean_or_frozen_install(self):
        report = passed_record()
        report["verification"][0]["installMode"] = "incremental"
        self.assertTrue(any("incremental install" in e for e in validate(report)))

    def test_source_change_invalidates_checks_but_not_install(self):
        report = passed_record()
        report["finalInputs"]["source"] = hashlib.sha256(b"new source").hexdigest()
        errors = validate(report)
        self.assertTrue(any("checks/typecheck" in e for e in errors))
        self.assertFalse(any("install/clean-install" in e for e in errors))

    def test_build_pass_is_not_runtime_evidence(self):
        report = passed_record()
        report["verification"] = [c for c in report["verification"] if c["kind"] != "runtime"]
        self.assertTrue(any("runtime/affected-runtime" in e for e in validate(report)))

    def test_policy_requires_explicit_intended_scope_coverage(self):
        report = passed_record()
        report["verification"][-1]["scopeComplete"] = False
        self.assertTrue(any("scope coverage" in e for e in validate(report)))

    def test_later_failure_overrides_an_older_pass(self):
        report = passed_record()
        failed = deepcopy(next(c for c in report["verification"] if c["check"] == "typecheck"))
        failed.update(status="FAILED", exitCode=2, finishedAt="2026-01-01T00:02:00Z")
        report["verification"].append(failed)
        self.assertTrue(any("checks/typecheck: no PASSED evidence" in e for e in validate(report)))

    def test_pass_needs_exit_code_timestamps_and_actual_log_reference(self):
        for field, value in (("exitCode", None), ("exitCode", False), ("log", None), ("finishedAt", None)):
            with self.subTest(field=field, value=value):
                report = passed_record()
                report["verification"][0][field] = value
                self.assertTrue(validate(report))

    def test_manual_proposal_preserves_verdict_and_uses_existing_authority(self):
        report = passed_record()
        decision = report["decisions"][0]
        decision.update(plannerStatus="CANDIDATE_SEARCH_TRUNCATED", plannerTarget=None, targetOrigin="manual")
        self.assertEqual(validate(report), [])
        report["targetMode"] = "pinned"
        self.assertTrue(any("revised-target authorization" in e for e in validate(report)))
        decision["revisedTargetAuthorization"] = True
        self.assertEqual(validate(report), [])
        decision["authorityRef"] = "unrecorded-decision"
        self.assertTrue(any("authority reference" in e for e in validate(report)))

    def test_manual_alternative_cannot_be_labelled_planner_target(self):
        report = passed_record()
        report["decisions"][0]["selectedTarget"] = "1.4.3"
        self.assertTrue(any("manual alternative" in e for e in validate(report)))

    def test_changed_declaration_must_have_a_matching_decision(self):
        report = passed_record()
        report["declarationChanges"][0]["package"] = "unrelated-package"
        self.assertTrue(any("same package" in e for e in validate(report)))
        report = passed_record()
        report["decisions"][0].update(selectedTarget=None, plannerTarget=None, targetOrigin="manual")
        self.assertTrue(any("needs a selected target" in e for e in validate(report)))

    def test_declaration_and_unique_name_counts_are_separate(self):
        changes = [{"package": "same-name", "from": "1.0", "to": "1.1"},
                   {"package": "same-name", "from": "1.0", "to": "1.1"},
                   {"package": "added", "from": None, "to": "2.0"},
                   {"package": "removed", "from": "2.0", "to": None},
                   {"package": "unchanged", "from": "3.0", "to": "3.0"}]
        self.assertEqual(counts(changes), {"changedDeclarations": 4, "updatedUniqueNames": 1,
                                          "addedUniqueNames": 1, "removedUniqueNames": 1})

    def test_unchanged_or_duplicate_declarations_are_not_changes(self):
        report = passed_record()
        report["declarationChanges"].append(deepcopy(report["declarationChanges"][0]))
        report["counts"] = counts(report["declarationChanges"])
        self.assertTrue(any("duplicate declaration" in e for e in validate(report)))
        report = passed_record()
        report["declarationChanges"][0]["to"] = "1.3.0"
        report["counts"] = counts(report["declarationChanges"])
        self.assertTrue(any("unchanged declaration" in e for e in validate(report)))

    def test_plan_only_cannot_claim_migration_success(self):
        report = passed_record()
        report["mode"] = "PLAN_ONLY"
        self.assertTrue(any("PLAN_ONLY" in e for e in validate(report)))

    def test_invalid_field_types_report_errors_without_crashing(self):
        for field in ("mode", "targetMode", "objective", "completion", "outcome", "targetLevel"):
            report = passed_record()
            report[field] = []
            self.assertTrue(validate(report), field)
        for collection, field in (("decisions", "authorityRef"), ("decisions", "targetOrigin"),
                                  ("declarationChanges", "package"), ("declarationChanges", "decisionId"),
                                  ("verification", "status"), ("verification", "kind")):
            report = passed_record()
            report[collection][0][field] = []
            self.assertTrue(validate(report), (collection, field))


if __name__ == "__main__":
    unittest.main()
