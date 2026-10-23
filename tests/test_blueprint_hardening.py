"""Deterministic hardening tests for the headless Upgrade Blueprint boundary.

These tests run the boundary against the FAKE planning core fixture
(`tests/fixtures/fake_core`), a deterministic stand-in for the real pinned
planner. That makes every fail-closed guarantee testable WITHOUT network access
and WITHOUT depending on planner internals:

* §2  plan.json is mandatory and structurally validated; plan/prompt hashes are
      verified against the on-disk bytes; a planning-only contract violation is
      refused (never silently copied into a "successful" blueprint); the final
      blueprint conforms to the shipped JSON Schema (fail-closed).
* §3  output/state/cache offsets from the analysed project are refused at the
      write boundary; every run gets its own unique directory; concurrent runs
      never clobber each other.
* §5  exclusions are real planning scope: the planner receives
      `--exclude-dependency` and EXCLUDED rows stay visible in the blueprint.
* §6  solverComponents come from the canonical manifest source (file envelope
      as fallback) and source.inputFiles resolve relative to the project.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploom_mcp.blueprint import (  # noqa: E402
    BlueprintOptions,
    BlueprintPolicy,
    generate_upgrade_blueprint,
    validate_planning_contract,
)

FAKE_GENERATOR = ROOT / "tests" / "fixtures" / "fake_core" / "dependency_live_roadmap_generator.py"

_FAKE_ENV_KEYS = (
    "FAKE_GENERATOR_MODE",
    "FAKE_GENERATOR_ROWS",
    "FAKE_GENERATOR_EXCLUDED",
    "FAKE_GENERATOR_PARTIAL",
    "FAKE_GENERATOR_SOLVER",
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _make_project(root: Path) -> Path:
    proj = root / "proj"
    _write(proj / "package.json", json.dumps({"name": "app", "version": "1.0.0",
                                              "dependencies": {"pkg1": "^1.0.0", "pkg2": "^1.0.0"}}, indent=2))
    _write(proj / "package-lock.json", "{}")
    return proj


class _FakeEnv(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="ub-harden-"))
        self._saved = {k: os.environ.get(k) for k in _FAKE_ENV_KEYS}
        self.addCleanup(self._restore_env)
        self.addCleanup(shutil.rmtree, self._tmp, True)
        for key in _FAKE_ENV_KEYS:
            os.environ.pop(key, None)

    def _restore_env(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _run(self, proj: Path, **overrides):
        options = BlueprintOptions(
            generator_path=str(FAKE_GENERATOR),
            output_dir=str(self._tmp / "out"),
            draft_deadline_seconds=60.0,
            run_timeout_seconds=30,
        )
        policy = BlueprintPolicy()
        opts = {**self._base_options(), **overrides}
        return generate_upgrade_blueprint(str(proj), policy=opts.pop("policy", policy), options=opts.pop("options", options))

    def _base_options(self) -> dict:
        return {}

    def _generate(self, proj: Path, policy=None, options=None, run_id=None):
        return generate_upgrade_blueprint(
            str(proj),
            policy=policy if policy is not None else BlueprintPolicy(),
            options=options if options is not None else BlueprintOptions(
                generator_path=str(FAKE_GENERATOR),
                output_dir=str(self._tmp / "out"),
                run_timeout_seconds=30,
            ),
            run_id=run_id,
        )


class MandatoryPlanAndContractTests(_FakeEnv):
    def test_missing_plan_json_is_plan_missing(self) -> None:
        os.environ["FAKE_GENERATOR_MODE"] = "no-plan"
        result = self._generate(_make_project(self._tmp))
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "plan-missing")

    def test_plan_without_proposals_is_plan_invalid(self) -> None:
        os.environ["FAKE_GENERATOR_MODE"] = "bad-plan"
        result = self._generate(_make_project(self._tmp))
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "plan-invalid")

    def test_broken_plan_json_is_plan_invalid(self) -> None:
        os.environ["FAKE_GENERATOR_MODE"] = "broken-plan-json"
        result = self._generate(_make_project(self._tmp))
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "plan-invalid")

    def test_hash_mismatch_is_refused(self) -> None:
        os.environ["FAKE_GENERATOR_MODE"] = "bad-hash"
        result = self._generate(_make_project(self._tmp))
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "hash-mismatch")

    def test_planning_contract_violation_is_refused(self) -> None:
        os.environ["FAKE_GENERATOR_MODE"] = "contract-violation"
        result = self._generate(_make_project(self._tmp))
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "contract-violation")

    def test_validate_planning_contract_accepts_planning_only(self) -> None:
        self.assertIsNone(validate_planning_contract({
            "status": "DRAFT_READY",
            "verificationStatus": "NOT_VERIFIED",
            "authority": "PLANNING_ONLY",
            "compatibility": "UNKNOWN",
        }))
        self.assertIsNotNone(validate_planning_contract({
            "status": "DRAFT_READY",
            "verificationStatus": "VERIFIED",
            "authority": "PLANNING_ONLY",
            "compatibility": "UNKNOWN",
        }))
        self.assertIsNotNone(validate_planning_contract({
            "status": "SOMETHING_NEW",
            "verificationStatus": "NOT_VERIFIED",
            "authority": "PLANNING_ONLY",
            "compatibility": "UNKNOWN",
        }))
        self.assertIsNotNone(validate_planning_contract({"status": "DRAFT_READY",
                                                          "compatibility": "PROVEN_BY_MAGIC"}))

    def test_successful_blueprint_matches_schema_and_hashes(self) -> None:
        os.environ["FAKE_GENERATOR_ROWS"] = "3"
        result = self._generate(_make_project(self._tmp))
        self.assertTrue(result.ok, result.error)
        bp = result.blueprint
        import jsonschema
        schema = json.loads((ROOT / "contracts" / "upgrade-blueprint.schema.json").read_text(encoding="utf-8-sig"))
        jsonschema.validate(bp, schema)
        plan_bytes = Path(result.plan_path).read_bytes()
        import hashlib
        self.assertEqual(bp["hashes"]["plan"], hashlib.sha256(plan_bytes).hexdigest())
        self.assertEqual(bp["status"]["verificationStatus"], "NOT_VERIFIED")
        self.assertEqual(bp["status"]["draftStatus"], "DRAFT_READY")


class FilesystemIsolationTests(_FakeEnv):
    def test_output_dir_inside_project_is_rejected_by_boundary(self) -> None:
        proj = _make_project(self._tmp)
        result = self._generate(
            proj,
            options=BlueprintOptions(generator_path=str(FAKE_GENERATOR),
                                     output_dir=str(proj / "sub"), run_timeout_seconds=30),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "output-inside-project")

    def test_registry_cache_inside_project_is_rejected(self) -> None:
        proj = _make_project(self._tmp)
        result = self._generate(
            proj,
            options=BlueprintOptions(generator_path=str(FAKE_GENERATOR),
                                     output_dir=str(self._tmp / "out"),
                                     registry_metadata_cache=str(proj / "cache"),
                                     run_timeout_seconds=30),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "output-inside-project")

    def test_each_run_gets_its_own_run_directory(self) -> None:
        proj = _make_project(self._tmp)
        opts = BlueprintOptions(generator_path=str(FAKE_GENERATOR),
                                output_dir=str(self._tmp / "out"), run_timeout_seconds=30)
        r1 = self._generate(proj, options=opts)
        r2 = self._generate(proj, options=opts)
        self.assertTrue(r1.ok, r1.error)
        self.assertTrue(r2.ok, r2.error)
        self.assertNotEqual(r1.run_id, r2.run_id)
        self.assertNotEqual(Path(r1.blueprint_path).parent, Path(r2.blueprint_path).parent)
        self.assertTrue(Path(r1.blueprint_path).is_file())
        self.assertTrue(Path(r2.blueprint_path).is_file())

    def test_same_run_id_same_output_dir_is_not_clobbered(self) -> None:
        proj = _make_project(self._tmp)
        opts = BlueprintOptions(generator_path=str(FAKE_GENERATOR),
                                output_dir=str(self._tmp / "fixed"), run_timeout_seconds=30)
        r1 = self._generate(proj, options=opts, run_id="fixed-run")
        r2 = self._generate(proj, options=opts, run_id="fixed-run")
        self.assertTrue(r1.ok, r1.error)
        self.assertTrue(r2.ok, r2.error)
        self.assertNotEqual(r1.run_id, r2.run_id)
        self.assertTrue(r2.run_id.startswith("fixed-run"))

    def test_concurrent_runs_never_clobber_each_other(self) -> None:
        proj_a = _make_project(self._tmp)
        proj_b = Path(self._tmp) / "proj-b"
        _write(proj_b / "package.json", json.dumps({"name": "b", "version": "1.0.0"}, indent=2))
        _write(proj_b / "package-lock.json", "{}")
        opts_a = BlueprintOptions(generator_path=str(FAKE_GENERATOR),
                                  output_dir=str(self._tmp / "out"), run_timeout_seconds=30)
        opts_b = BlueprintOptions(generator_path=str(FAKE_GENERATOR),
                                  output_dir=str(self._tmp / "out"), run_timeout_seconds=30)
        errors = []
        results = []

        def worker(proj, opts):
            try:
                results.append(self._generate(proj, options=opts))
            except Exception as exc:  # pragma: no cover - surface thread errors
                errors.append(exc)

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = []
            for _ in range(4):
                futs.append(pool.submit(worker, proj_a, opts_a))
                futs.append(pool.submit(worker, proj_b, opts_b))
            for fut in futs:
                fut.result()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        for result in results:
            self.assertTrue(result.ok, result.error)
        paths = [Path(r.blueprint_path).parent for r in results]
        self.assertEqual(len(set(paths)), len(paths), "every run must have its own output dir")


class ExclusionsTests(_FakeEnv):
    def test_excluded_package_keeps_excluded_row_and_others_get_planned(self) -> None:
        os.environ["FAKE_GENERATOR_EXCLUDED"] = "pkg2"
        proj = _make_project(self._tmp)
        result = self._generate(proj, policy=BlueprintPolicy(exclusions=("pkg2",)))
        self.assertTrue(result.ok, result.error)
        by_pkg = {d["package"]: d["status"] for d in result.blueprint["dependencies"]}
        self.assertEqual(by_pkg["pkg2"], "EXCLUDED")
        self.assertEqual(by_pkg["pkg1"], "PROPOSED")
        self.assertEqual(result.blueprint["summary"]["proposed"], 1)
        # EXCLUDED stays visible; it is not dropped from the health denominator fully.
        per_project = result.blueprint["perProject"].get("app") or {}
        self.assertEqual(per_project.get("excluded"), 1)


class SourceIdentityAndSolverTests(_FakeEnv):
    def test_source_input_files_resolve_relative_to_project(self) -> None:
        proj = _make_project(self._tmp)
        result = self._generate(proj)
        self.assertTrue(result.ok, result.error)
        input_files = result.blueprint["source"]["inputFiles"]
        self.assertEqual(input_files, [str((proj / "package.json").resolve())])

    def test_solver_components_canonical_source_is_manifest(self) -> None:
        os.environ["FAKE_GENERATOR_SOLVER"] = "manifest"
        result = self._generate(_make_project(self._tmp))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.blueprint["solverComponents"], [{"solver": "z3", "component": 1}])

    def test_solver_components_fall_back_to_file_envelope(self) -> None:
        os.environ["FAKE_GENERATOR_SOLVER"] = "file"
        result = self._generate(_make_project(self._tmp))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.blueprint["solverComponents"], [{"solver": "z3", "component": 1}])


class PartialPlanningTests(_FakeEnv):
    def test_draft_partial_is_reported_not_masked(self) -> None:
        os.environ["FAKE_GENERATOR_PARTIAL"] = "1"
        result = self._generate(_make_project(self._tmp))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.draft_status, "DRAFT_PARTIAL")
        self.assertEqual(result.blueprint["status"]["draftStatus"], "DRAFT_PARTIAL")
        self.assertTrue(result.blueprint["status"]["partialReason"])


if __name__ == "__main__":
    unittest.main()
