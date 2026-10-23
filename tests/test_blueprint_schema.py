"""Upgrade Blueprint JSON Schema conformance tests (fast, no planner).

The schema in ``contracts/upgrade-blueprint.schema.json`` is the canonical
contract of the planning boundary. These tests build a synthetic blueprint via
the pure projection (transcode_upgrade_blueprint) and validate it against the
schema, so any drift between the adapter output and the declared schema is
caught without launching the real planner. The production-boundary test in
test_blueprint.py additionally validates a real published artifact.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional, installed via pip
    jsonschema = None

from deploom_mcp.blueprint import (  # noqa: E402
    BlueprintPolicy,
    transcode_upgrade_blueprint,
)

SCHEMA_PATH = ROOT / "contracts" / "upgrade-blueprint.schema.json"


def _synthetic() -> tuple[dict, dict]:
    """Same planned shape the real Draft run publishes (incl. deadline clock)."""
    plan = {
        "proposals": [{
            "project": "app",
            "rows": [
                {"project": "app", "package": "a", "kind": "runtime", "requestedSpec": "^2.0.0",
                 "current": "1.0.0", "target": "2.0.0", "status": "proposed", "reason": "update",
                 "group": 2, "latest": "2.0.0", "candidateSearchTruncated": False,
                 "peerCompatUnresolved": False, "breakingChanges": [], "migrationNotes": []},
            ],
            "health": {"total": 1, "critical": 0, "high": 0},
        }],
        "unknowns": [], "conflicts": [], "unresolved": [],
        "counts": {"proposed": 1, "total": 1},
    }
    manifest = {
        "schemaVersion": 2, "status": "DRAFT_READY", "runId": "run-x", "workspaceId": "ws",
        "projectId": "app", "mode": "draft", "generatedAt": "2026-10-23T00:00:00Z",
        "elapsedMs": 123, "deadline": {"deadlineSeconds": 60.0, "remainingMs": 12000},
        "policyHash": "ph",
        "verificationStatus": "NOT_VERIFIED", "authority": "PLANNING_ONLY",
        "compatibility": "UNKNOWN", "partialReason": None, "summary": "ok",
        "metadata": {"metadataKnown": 1, "metadataTotal": 1, "securityKnown": 1, "securityTotal": 1},
        "inputHashes": {"package.json": "h1"},
        "inputFilesByProject": {"app": [{"name": "package.json"}]},
        "hashes": {"plan": "ph1", "prompt": "pp1"},
        "artifacts": {"manifest": "m", "plan": "p", "prompt": "r", "summary": "s", "solverComponents": "c"},
        "solverComponents": [{"component": 1}],
    }
    return plan, manifest


@unittest.skipIf(jsonschema is None, "jsonschema not installed")
class BlueprintSchemaTests(unittest.TestCase):
    def test_transcoded_blueprint_validates_against_shipped_schema(self) -> None:
        plan, manifest = _synthetic()
        bp = transcode_upgrade_blueprint(
            plan, manifest, project_path="C:/proj", project_name="app",
            policy=BlueprintPolicy(), artifacts=manifest["artifacts"], blueprint_path="C:/out/ub.json",
        )
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8-sig"))
        jsonschema.validate(bp, schema)

    def test_planning_only_semantics_are_statically_required(self) -> None:
        plan, manifest = _synthetic()
        bp = transcode_upgrade_blueprint(
            plan, manifest, project_path="C:/proj", project_name="app",
            policy=BlueprintPolicy(), artifacts=manifest["artifacts"], blueprint_path="C:/out/ub.json",
        )
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8-sig"))
        status = schema["properties"]["status"]
        self.assertEqual(status["properties"]["verificationStatus"]["enum"], ["NOT_VERIFIED"])
        self.assertEqual(status["properties"]["authority"]["enum"], ["PLANNING_ONLY"])
        self.assertEqual(status["properties"]["compatibility"]["enum"], ["UNKNOWN"])
        self.assertEqual(status["properties"]["parallelExecutionSafety"]["const"], "UNPROVEN")
        # And the emitted blueprint really carries them.
        self.assertEqual(bp["status"]["verificationStatus"], "NOT_VERIFIED")
        self.assertEqual(bp["status"]["authority"], "PLANNING_ONLY")
        self.assertEqual(bp["status"]["compatibility"], "UNKNOWN")
        self.assertEqual(bp["status"]["parallelExecutionSafety"], "UNPROVEN")

    def test_reserved_verified_statuses_absent_from_schema_vocabulary_sources(self) -> None:
        plan, manifest = _synthetic()
        bp = transcode_upgrade_blueprint(
            plan, manifest, project_path="C:/proj", project_name="app",
            policy=BlueprintPolicy(), artifacts=manifest["artifacts"], blueprint_path="C:/out/ub.json",
        )
        for dep in bp.get("dependencies", []):
            self.assertNotIn(dep["status"], {"CONSTRAINT_SATISFIED", "MATERIALIZED", "PROJECT_VERIFIED"})

    def test_deadline_clock_shape_is_accepted_and_legacy_number_is_nullable(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8-sig"))
        deadline = schema["properties"]["deadline"]
        # The planner publishes {deadlineSeconds, remainingMs}; legacy numeric and null stay valid.
        self.assertIn("object", deadline["type"])
        self.assertIn("number", deadline["type"])
        self.assertIn("null", deadline["type"])


if __name__ == "__main__":
    unittest.main()
