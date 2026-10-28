"""Stage 1 headless Upgrade Blueprint boundary tests.

Unit tests cover the pure projection (transcode_upgrade_blueprint) and the
status vocabulary; integration tests run the REAL planner subprocess through
generate_upgrade_blueprint against a real package.json project and assert:

  * the project directory is never written into (byte-for-byte file snapshot),
  * the blueprint is structurally valid and carries NOT_VERIFIED / PLANNING_ONLY
    / UNKNOWN / UNPROVEN semantics,
  * reserved verified statuses never appear,
  * re-running the same project reproduces the same plan (determinism).

These are the production boundary; a failure here means the headless contract
diverged from the real planner output.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploom_mcp.blueprint import (  # noqa: E402
    RESERVED_VERIFIED_STATUSES,
    BlueprintOptions,
    BlueprintPolicy,
    _flatten_rows,
    _recommendation_groups,
    draft_status_to_blueprint,
    generate_upgrade_blueprint,
    transcode_upgrade_blueprint,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _dir_snapshot(path: Path) -> dict:
    """Relative path -> sha256 of contents; excludes __pycache__."""
    snapshot = {}
    for p in sorted(path.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            snapshot[str(p.relative_to(path))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snapshot


def _make_project(root: Path) -> Path:
    proj = root / "proj"
    _write(proj / "package.json", json.dumps({
        "name": "ub-test", "version": "1.0.0",
        "dependencies": {"uuid": "^10.0.0", "is-number": "^7.0.0"},
    }, indent=2))
    lock = {"name": "ub-test", "version": "1.0.0", "lockfileVersion": 3, "requires": True,
            "packages": {"": {"name": "ub-test", "version": "1.0.0", "dependencies": {"uuid": "^10.0.0", "is-number": "^7.0.0"}},
                         "node_modules/uuid": {"version": "10.0.0"}, "node_modules/is-number": {"version": "7.0.0"}}}
    _write(proj / "package-lock.json", json.dumps(lock, indent=2))
    return proj


def _make_project_with_react(root: Path) -> Path:
    """A real-npm project with `react`, whose exclusion must take effect INSIDE
    the real planner (not be filtered by the adapter afterwards)."""
    proj = root / "proj-react"
    _write(proj / "package.json", json.dumps({
        "name": "ub-react-test", "version": "1.0.0",
        "dependencies": {"uuid": "^10.0.0", "is-number": "^7.0.0", "react": "^18.0.0"},
    }, indent=2))
    lock = {"name": "ub-react-test", "version": "1.0.0", "lockfileVersion": 3, "requires": True,
            "packages": {"": {"name": "ub-react-test", "version": "1.0.0", "dependencies": {"uuid": "^10.0.0", "is-number": "^7.0.0", "react": "^18.0.0"}},
                         "node_modules/uuid": {"version": "10.0.0"}, "node_modules/is-number": {"version": "7.0.0"},
                         "node_modules/react": {"version": "18.2.0"}}}
    _write(proj / "package-lock.json", json.dumps(lock, indent=2))
    return proj


class StatusVocabularyTests(unittest.TestCase):
    def test_all_draft_statuses_map_to_blueprint_vocabulary(self) -> None:
        draft_statuses = [
            "proposed", "no-change", "ok", "excluded", "deferred", "blocked",
            "candidate-search-truncated", "no-target", "unknown-security",
            "unknown-metadata", "peer-compat-unresolved", "not-attempted",
            "interrupted",
        ]
        for status in draft_statuses:
            mapped = draft_status_to_blueprint(status)
            self.assertNotIn(mapped, RESERVED_VERIFIED_STATUSES, status)
            self.assertTrue(mapped.isupper(), mapped)

    def test_reserved_verified_statuses_are_never_part_of_planning_mapping(self) -> None:
        # The vocabulary is closed: reserved verified statuses cannot be the
        # result of mapping ANY draft status, and unknown inputs collapse to
        # UNKNOWN instead of leaking through as a half-recognized value.
        self.assertNotIn("CONSTRAINT_SATISFIED", draft_status_to_blueprint("proposed"))
        for reserved in RESERVED_VERIFIED_STATUSES:
            self.assertNotEqual(draft_status_to_blueprint(reserved.lower().replace("_", "-")), reserved)
            self.assertNotEqual(draft_status_to_blueprint(reserved.lower().replace("_", " ")), reserved)
            self.assertNotIn(draft_status_to_blueprint(reserved), RESERVED_VERIFIED_STATUSES)
        self.assertEqual(draft_status_to_blueprint("gibberish-status"), "UNKNOWN")
        self.assertEqual(draft_status_to_blueprint(""), "UNKNOWN")

    def test_proposed_maps_to_proposed(self) -> None:
        self.assertEqual(draft_status_to_blueprint("proposed"), "PROPOSED")
        self.assertEqual(draft_status_to_blueprint("no-change"), "NO_CHANGE")
        self.assertEqual(draft_status_to_blueprint("ok"), "POLICY_SATISFIED")


class TranscodeUnitTests(unittest.TestCase):
    def _synthetic(self):
        plan = {
            "schemaVersion": 1,
            "projects": ["app"],
            "proposals": [{
                "project": "app",
                "rows": [
                    {"project": "app", "package": "a", "kind": "runtime", "requestedSpec": "^2.0.0",
                     "current": "1.0.0", "target": "2.0.0", "status": "proposed", "reason": "update",
                     "group": 2, "latest": "2.0.0", "candidateSearchTruncated": False,
                     "peerCompatUnresolved": False, "breakingChanges": ["peer dropped"],
                     "migrationNotes": ["check peers"]},
                    {"project": "app", "package": "b", "kind": "runtime", "requestedSpec": "^1.0.0",
                     "current": "1.0.0", "target": None, "status": "ok", "reason": "ok ok",
                     "group": 1, "latest": "1.0.0", "candidateSearchTruncated": False,
                     "peerCompatUnresolved": False, "breakingChanges": [], "migrationNotes": []},
                ],
                "health": {"total": 2, "critical": 0, "high": 0, "postPlanGoal": "feasible", "postPlanLagOkPct": 100.0},
            }],
            "unknowns": [], "conflicts": [], "unresolved": [],
            "counts": {"proposed": 1, "ok": 1, "total": 2},
            "manifests": {}, "scan": {},
        }
        manifest = {
            "schemaVersion": 2, "status": "DRAFT_READY", "runId": "run-x", "workspaceId": "ws",
            "projectId": "app", "mode": "draft", "generatedAt": "2026-10-23T00:00:00Z",
            "elapsedMs": 123, "deadline": 60, "policyHash": "ph",
            "verificationStatus": "NOT_VERIFIED", "authority": "PLANNING_ONLY", "compatibility": "UNKNOWN",
            "partialReason": None, "summary": "ok",
            "metadata": {"metadataKnown": 2, "metadataTotal": 2, "securityKnown": 2, "securityTotal": 2, "insufficientData": False},
            "inputHashes": {"package.json": "h1", "package-lock.json": "h2"},
            "inputFilesByProject": {"app": [{"name": "package.json"}, {"name": "package-lock.json"}]},
            "hashes": {"plan": "planhash", "prompt": "prompthash"},
            "artifacts": {"manifest": "m", "plan": "p", "prompt": "r", "summary": "s", "solverComponents": "c"},
            "solverComponents": [{"component": 1}],
        }
        return plan, manifest

    def test_transcode_carries_planning_only_semantics(self) -> None:
        plan, manifest = self._synthetic()
        bp = transcode_upgrade_blueprint(
            plan, manifest, project_path="C:/proj", project_name="app",
            policy=BlueprintPolicy(), artifacts=manifest["artifacts"], blueprint_path="C:/out/ub.json",
        )
        self.assertEqual(bp["schemaVersion"], 2)
        self.assertEqual(bp["kind"], "upgrade-blueprint")
        status = bp["status"]
        self.assertEqual(status["verificationStatus"], "NOT_VERIFIED")
        self.assertEqual(status["authority"], "PLANNING_ONLY")
        self.assertEqual(status["compatibility"], "UNKNOWN")
        self.assertEqual(status["parallelExecutionSafety"], "UNPROVEN")
        self.assertEqual(bp["hashes"], {"plan": "planhash", "prompt": "prompthash"})
        self.assertEqual(bp["summary"]["proposed"], 1)
        # A concrete target still needs review when the source reports a
        # breaking change; PROPOSED alone is not the complete decision.
        self.assertEqual(bp["summary"]["needsReview"], 1)
        by_pkg = {d["package"]: d["status"] for d in bp["dependencies"]}
        self.assertEqual(by_pkg, {"a": "PROPOSED", "b": "POLICY_SATISFIED"})

    def test_transcode_reserved_statuses_never_on_planning_rows(self) -> None:
        plan, manifest = self._synthetic()
        bp = transcode_upgrade_blueprint(
            plan, manifest, project_path="C:/proj", project_name="app",
            policy=BlueprintPolicy(), artifacts=manifest["artifacts"], blueprint_path="C:/out/ub.json",
        )
        emitted = {d["status"] for d in bp["dependencies"]}
        self.assertTrue(emitted.isdisjoint(RESERVED_VERIFIED_STATUSES))

    def test_recommendation_groups_are_display_priority_not_parallel_proof(self) -> None:
        rows = _flatten_rows(self._synthetic()[0])
        groups = _recommendation_groups(rows)
        self.assertEqual(len(groups), 2)
        for group in groups:
            self.assertEqual(group["kind"], "display-priority")
            self.assertEqual(group["parallelSafety"], "UNPROVEN")
        needs_review_groups = [g for g in groups if g["needsReview"]]
        self.assertEqual(needs_review_groups, [])


class HeadlessIntegrationTests(unittest.TestCase):
    """Runs the REAL planner subprocess through generate_upgrade_blueprint."""

    def setUp(self) -> None:
        self._tmp: Path = Path(tempfile.mkdtemp(prefix="deploom-ub-"))
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def test_planning_run_produces_valid_blueprint_and_never_touches_project(self) -> None:
        proj = _make_project(self._tmp)
        before = _dir_snapshot(proj)

        result = generate_upgrade_blueprint(
            str(proj),
            policy=BlueprintPolicy(target_level="yellow", min_lag_ok_pct=80),
            options=BlueprintOptions(
                output_dir=str(self._tmp / "out"),
                draft_deadline_seconds=90.0,
                draft_max_candidates=3,
                registry_metadata_ttl=0.0,
            ),
            project_name="ub-test",
            run_id="run-ub-it",
        )

        self.assertTrue(result.ok, f"planning failed: {result.error} stderr={result.stderr[-2000:]}")
        self.assertIsNotNone(result.blueprint)

        after = _dir_snapshot(proj)
        self.assertEqual(before, after, "generate_upgrade_blueprint must not write into the project directory")

        bp = result.blueprint
        self.assertEqual(bp["schemaVersion"], 2)
        self.assertEqual(bp["kind"], "upgrade-blueprint")
        self.assertIn(result.draft_status, ("DRAFT_READY", "DRAFT_PARTIAL"))
        self.assertEqual(bp["status"]["verificationStatus"], "NOT_VERIFIED")
        self.assertEqual(bp["status"]["authority"], "PLANNING_ONLY")
        self.assertEqual(bp["status"]["compatibility"], "UNKNOWN")
        self.assertEqual(bp["source"]["project"]["name"], "ub-test")
        self.assertEqual(bp["summary"]["totalDependencies"], 2)
        self.assertEqual(len(bp["dependencies"]), 2)
        for dep in bp["dependencies"]:
            self.assertNotIn(dep["status"], RESERVED_VERIFIED_STATUSES)
        self.assertEqual(len({d["package"] for d in bp["dependencies"]}), 2)
        self.assertTrue(Path(result.manifest_path).is_file())
        self.assertTrue(Path(result.plan_path).is_file())
        self.assertTrue(Path(result.blueprint_path).is_file())

        # Real published artifact must satisfy the shipped JSON Schema (the
        # schema was reconciled with the real planner deadline clock in Stage A/B).
        try:
            import jsonschema
        except ImportError:  # pragma: no cover - optional dependency
            jsonschema = None  # type: ignore[assignment]
        if jsonschema is not None:
            schema = _read_json(ROOT / "contracts" / "upgrade-blueprint.schema.json")
            jsonschema.validate(bp, schema)

    def test_repeated_planning_is_deterministic(self) -> None:
        proj = _make_project(self._tmp)
        results = []
        for i, run_id in enumerate(("run-ub-d1", "run-ub-d2")):
            result = generate_upgrade_blueprint(
                str(proj),
                options=BlueprintOptions(
                    output_dir=str(self._tmp / f"out{i}"),
                    draft_deadline_seconds=90.0,
                    registry_metadata_ttl=0.0,
                ),
                project_name="ub-test",
                run_id=run_id,
            )
            self.assertTrue(result.ok, result.error)
            results.append(result)

        bp1, bp2 = results[0].blueprint, results[1].blueprint
        # Same plan bytes -> same published plan hash.
        self.assertEqual(bp1["hashes"]["plan"], bp2["hashes"]["plan"])
        # Same per-dependency plan verdicts.
        b1 = sorted(((d["package"], d["status"], d["target"]) for d in bp1["dependencies"]), key=lambda item: item[0])
        b2 = sorted(((d["package"], d["status"], d["target"]) for d in bp2["dependencies"]), key=lambda item: item[0])
        self.assertEqual(b1, b2)
        self.assertEqual(bp1["summary"]["statusCounts"], bp2["summary"]["statusCounts"])

    def test_missing_project_dir_is_machine_readable_error(self) -> None:
        ghost = self._tmp / "does-not-exist"
        result = generate_upgrade_blueprint(str(ghost))
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "project-not-found")

    def test_directory_without_package_json_is_invalid_project(self) -> None:
        empty = self._tmp / "empty"
        empty.mkdir()
        result = generate_upgrade_blueprint(str(empty))
        self.assertFalse(result.ok)
        self.assertEqual(result.error["code"], "invalid-project")

    def test_exclusions_reach_real_planner_and_stay_visible(self) -> None:
        """Exclusions must be applied by the REAL planner (react removed from the
        active scope with an EXCLUDED verdict), stay visible in the blueprint,
        surface in policy.exclusions, and never leak onto peer packages."""
        proj = _make_project_with_react(self._tmp)
        before = _dir_snapshot(proj)

        result = generate_upgrade_blueprint(
            str(proj),
            policy=BlueprintPolicy(target_level="yellow", min_lag_ok_pct=80, exclusions=("react",)),
            options=BlueprintOptions(
                output_dir=str(self._tmp / "out-x"),
                draft_deadline_seconds=90.0,
                draft_max_candidates=3,
                registry_metadata_ttl=0.0,
            ),
            project_name="ub-react-test",
            run_id="run-ub-it-excl",
        )

        self.assertTrue(result.ok, f"planning failed: {result.error} stderr={result.stderr[-2000:]}")
        self.assertIsNotNone(result.blueprint)

        after = _dir_snapshot(proj)
        self.assertEqual(before, after, "generate_upgrade_blueprint must not write into the project directory")

        bp = result.blueprint
        self.assertEqual(bp["policy"]["exclusions"], ["react"], "applied exclusions must be exposed in policy")

        by_pkg = {d["package"]: d for d in bp["dependencies"]}
        self.assertIn("react", by_pkg, "EXCLUDED row must stay visible, not vanish")
        self.assertEqual(by_pkg["react"]["status"], "EXCLUDED")
        self.assertFalse(by_pkg["react"]["needsReview"], "EXCLUDED is a resolution, not a review item")

        for other in ("uuid", "is-number"):
            self.assertNotEqual(by_pkg[other]["status"], "EXCLUDED", f"{other} must not be collateral-excluded")

        self.assertGreaterEqual(bp["summary"]["statusCounts"].get("EXCLUDED", 0), 1)
        per_proj_excluded = sum(int(v.get("excluded") or 0) for v in bp["perProject"].values())
        self.assertGreaterEqual(per_proj_excluded, 1, "health must count scope-excluded rows per project")
        status_excluded = bp["summary"]["statusCounts"].get("EXCLUDED", 0)
        self.assertEqual(per_proj_excluded, status_excluded, "health excludes and row-status excludes must agree")


if __name__ == "__main__":
    unittest.main()
