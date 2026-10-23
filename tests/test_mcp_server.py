"""DepLoom MCP server tests.

Two layers:

* fast unit tests that call the validation paths of `get_upgrade_plan`
  directly (misuse cases: invalid args, goal, deadline, output-inside-project,
  missing project) -- none of them launch the planner subprocess;
* a real stdio round-trip through the official MCP SDK (`ClientSession` +
  `stdio_client`) that boots `python -m deploom_mcp`, lists tools and calls
  `get_upgrade_plan` on a real minimal project, asserting the compact
  planning-only protocol. The genuine planning integration itself is covered by
  test_blueprint.py; here we exercise the server wiring end-to-end.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters, stdio_client  # noqa: E402

from deploom_mcp.server import (  # noqa: E402
    DEFAULT_DEADLINE_SECONDS,
    KIND,
    MIN_DEADLINE_SECONDS,
    TOOL_NAME,
    compact_blueprint,
    get_upgrade_plan,
)

RESERVED_VERIFIED = {"CONSTRAINT_SATISFIED", "MATERIALIZED", "PROJECT_VERIFIED"}


class GetUpgradePlanValidationTests(unittest.TestCase):
    """Misuse paths: pure validation, never launches the planner."""

    def test_empty_project_path_is_invalid_args(self) -> None:
        res = get_upgrade_plan(project_path="")
        self.assertIs(res["ok"], False)
        self.assertEqual(res["error"]["code"], "invalid-args")

    def test_unknown_target_level_is_invalid_goal(self) -> None:
        res = get_upgrade_plan(project_path="C:/nope", target_level="purple")
        self.assertIs(res["ok"], False)
        self.assertEqual(res["error"]["code"], "invalid-goal")
        self.assertNotIn("runId", [k for k in res if k != "ok"])

    def test_non_numeric_deadline_is_invalid_deadline(self) -> None:
        res = get_upgrade_plan(project_path="C:/nope", deadline_seconds="abc")
        self.assertIs(res["ok"], False)
        self.assertEqual(res["error"]["code"], "invalid-deadline")

    def test_non_positive_deadline_is_invalid_deadline(self) -> None:
        res = get_upgrade_plan(project_path="C:/nope", deadline_seconds=0)
        self.assertIs(res["ok"], False)
        self.assertEqual(res["error"]["code"], "invalid-deadline")

    def test_output_inside_project_is_rejected(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="ub-mcp-outside-"))
        proj = tmp / "proj"
        proj.mkdir(parents=True)
        (proj / "package.json").write_text("{}", encoding="utf-8")
        try:
            res = get_upgrade_plan(project_path=str(proj), output_dir=str(proj / "node_modules"))
            self.assertIs(res["ok"], False)
            self.assertEqual(res["error"]["code"], "output-inside-project")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_project_is_project_not_found(self) -> None:
        res = get_upgrade_plan(project_path="C:/definitely/not/here")
        self.assertIs(res["ok"], False)
        self.assertEqual(res["error"]["code"], "project-not-found")


class GetUpgradePlanHardeningValidationTests(unittest.TestCase):
    """Fail-closed input validation: NaN/Inf, ranges, registry URL, exclude."""

    def _code(self, **kwargs) -> str:
        res = get_upgrade_plan(project_path="C:/nope", **kwargs)
        self.assertIs(res["ok"], False)
        return res["error"]["code"]

    def test_nan_deadline_is_invalid_deadline(self) -> None:
        self.assertEqual(self._code(deadline_seconds=float("nan")), "invalid-deadline")

    def test_inf_deadline_is_invalid_deadline(self) -> None:
        self.assertEqual(self._code(deadline_seconds=float("inf")), "invalid-deadline")
        self.assertEqual(self._code(deadline_seconds=float("-inf")), "invalid-deadline")

    def test_min_lag_out_of_range_is_invalid_policy(self) -> None:
        self.assertEqual(self._code(min_lag_ok_pct=150), "invalid-policy")
        self.assertEqual(self._code(min_lag_ok_pct=-1), "invalid-policy")
        self.assertEqual(self._code(min_lag_ok_pct=80.5), "invalid-policy")

    def test_registry_url_with_credentials_is_rejected(self) -> None:
        self.assertEqual(
            self._code(registry="https://user:secret@registry.example.com/"),
            "invalid-registry",
        )

    def test_registry_non_http_scheme_is_rejected(self) -> None:
        self.assertEqual(self._code(registry="file:///tmp/reg"), "invalid-registry")
        self.assertEqual(self._code(registry="not-a-url"), "invalid-registry")

    def test_registry_ttl_negative_or_nan_is_rejected(self) -> None:
        self.assertEqual(self._code(registry_metadata_ttl=-5.0), "invalid-registry")
        self.assertEqual(self._code(registry_metadata_ttl=float("nan")), "invalid-registry")

    def test_exclude_must_be_list_of_strings(self) -> None:
        self.assertEqual(self._code(exclude="react"), "invalid-exclude")
        self.assertEqual(self._code(exclude=["react", ""]), "invalid-exclude")


class CompactProtocolTests(unittest.TestCase):
    def test_compact_protocol_carries_planning_only_semantics(self) -> None:
        blueprint = {
            "schemaVersion": 2,
            "kind": "upgrade-blueprint",
            "status": {
                "draftStatus": "DRAFT_PARTIAL",
                "verificationStatus": "NOT_VERIFIED",
                "authority": "PLANNING_ONLY",
                "compatibility": "UNKNOWN",
                "parallelExecutionSafety": "UNPROVEN",
            },
            "summary": {"totalDependencies": 3, "proposed": 1, "needsReview": 1,
                        "statusCounts": {"PROPOSED": 1, "NO_CHANGE": 1, "NEEDS_REVIEW": 1}},
            "recommendationGroups": [{"group": "G1", "count": 1, "needsReview": False,
                                      "packages": ["uuid"]}],
            "dependencies": [{"package": "uuid", "status": "PROPOSED", "current": "9.0.1",
                              "target": "10.0.0", "group": "G1", "needsReview": False}],
            "source": {"project": {"name": "app"}},
            "run": {"runId": "run-x", "projectId": "app"},
            "artifacts": {"manifest": "C:/m.json", "plan": "C:/p.json", "prompt": "C:/r.md",
                          "summary": "C:/s.md", "solverComponents": "C:/c.json"},
            "blueprintFile": "C:/ub.json",
        }
        compact = compact_blueprint(blueprint)
        self.assertEqual(compact["protocol"], "upgrade-blueprint")
        self.assertEqual(compact["status"]["authority"], "PLANNING_ONLY")
        self.assertEqual(compact["status"]["verificationStatus"], "NOT_VERIFIED")
        self.assertEqual(compact["status"]["compatibility"], "UNKNOWN")
        self.assertEqual(compact["status"]["parallelExecutionSafety"], "UNPROVEN")
        self.assertEqual(compact["summary"]["proposed"], 1)
        self.assertEqual(compact["recommendationGroups"][0]["packages"], ["uuid"])
        self.assertEqual(compact["dependencies"][0]["status"], "PROPOSED")
        self.assertEqual(compact["run"]["runId"], "run-x")

    def test_projected_dependency_rows_never_carry_reserved_statuses(self) -> None:
        blueprint = {
            "schemaVersion": 2,
            "status": {"verificationStatus": "NOT_VERIFIED", "authority": "PLANNING_ONLY"},
            "summary": {},
            "source": {"project": {"name": "app"}},
            "run": {},
            "recommendationGroups": [],
            "dependencies": [{"package": "a", "status": "PROPOSED"}],
        }
        compact = compact_blueprint(blueprint)
        for dep in compact["dependencies"]:
            self.assertNotIn(dep["status"], RESERVED_VERIFIED)

    def test_many_dependency_rows_are_bounded_with_explicit_truncation(self) -> None:
        blueprint = {
            "schemaVersion": 2,
            "status": {"verificationStatus": "NOT_VERIFIED", "authority": "PLANNING_ONLY"},
            "summary": {"totalDependencies": 250, "proposed": 250, "needsReview": 0,
                        "statusCounts": {"PROPOSED": 250}},
            "source": {"project": {"name": "app"}},
            "run": {},
            "recommendationGroups": [],
            "dependencies": [{"package": f"pkg{i}", "status": "PROPOSED"} for i in range(250)],
        }
        compact = compact_blueprint(blueprint, max_inline_rows=50)
        self.assertTrue(compact["truncation"]["truncated"])
        self.assertEqual(compact["truncation"]["inlineDependencies"], 50)
        self.assertEqual(compact["truncation"]["totalDependencies"], 250)
        self.assertEqual(len(compact["dependencies"]), 50)
        # The status summary stays complete even though rows were clipped.
        self.assertEqual(compact["summary"]["statusCounts"], {"PROPOSED": 250})

    def test_small_plan_reports_no_truncation(self) -> None:
        blueprint = {
            "schemaVersion": 2,
            "status": {"verificationStatus": "NOT_VERIFIED", "authority": "PLANNING_ONLY"},
            "summary": {"totalDependencies": 2},
            "source": {"project": {"name": "app"}},
            "run": {},
            "recommendationGroups": [],
            "dependencies": [{"package": "a", "status": "PROPOSED"},
                             {"package": "b", "status": "NO_CHANGE"}],
        }
        compact = compact_blueprint(blueprint)
        self.assertFalse(compact["truncation"]["truncated"])
        self.assertEqual(len(compact["dependencies"]), 2)

    def test_draft_partial_sets_partial_replan_required(self) -> None:
        blueprint = {
            "schemaVersion": 2,
            "status": {"draftStatus": "DRAFT_PARTIAL", "verificationStatus": "NOT_VERIFIED",
                       "authority": "PLANNING_ONLY",
                       "partialReason": "deadline budget exhausted"},
            "summary": {"processed": 5, "processedTotal": 9},
            "source": {"project": {"name": "app"}},
            "run": {},
            "recommendationGroups": [],
            "dependencies": [],
        }
        compact = compact_blueprint(blueprint)
        self.assertTrue(compact["partial"]["partial"])
        self.assertTrue(compact["partial"]["replanRequired"])
        self.assertEqual(compact["partial"]["processed"], 5)
        self.assertEqual(compact["partial"]["processedTotal"], 9)
        self.assertEqual(compact["status"]["draftStatus"], "DRAFT_PARTIAL")


class MCPStdioRoundTripTests(unittest.IsolatedAsyncioTestCase):
    """Boot the real server over stdio via the official MCP client SDK."""

    async def _session(self, state_dir: Path):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "deploom_mcp"],
            cwd=str(ROOT),
            env={**os.environ, "DEPLOOM_MCP_STATE_DIR": str(state_dir)},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def test_list_tools_exposes_get_upgrade_plan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ub-mcp-state-") as state:
            async for session in self._session(Path(state)):
                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
        self.assertIn(TOOL_NAME, names)

    async def test_invalid_goal_returns_machine_readable_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ub-mcp-state-") as state:
            async for session in self._session(Path(state)):
                result = await session.call_tool(
                    TOOL_NAME, {"project_path": "C:/nope", "target_level": "purple"}
                )
        self.assertTrue(result.is_error)
        payload = json.loads(result.content[0].text)
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["error"]["code"], "invalid-goal")

    async def test_missing_project_returns_project_not_found(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ub-mcp-state-") as state:
            async for session in self._session(Path(state)):
                result = await session.call_tool(
                    TOOL_NAME, {"project_path": "C:/definitely/not/here"}
                )
        self.assertTrue(result.is_error)
        payload = json.loads(result.content[0].text)
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["error"]["code"], "project-not-found")

    async def test_real_planning_run_via_mcp_returns_compact_protocol(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ub-mcp-it-") as tmp:
            root = Path(tmp)
            proj = root / "proj"
            proj.mkdir()
            (proj / "package.json").write_text(json.dumps({
                "name": "ub-mcp-test", "version": "1.0.0",
                "dependencies": {"uuid": "^10.0.0", "is-number": "^7.0.0"},
            }, indent=2), encoding="utf-8")
            (proj / "package-lock.json").write_text(json.dumps({
                "name": "ub-mcp-test", "version": "1.0.0", "lockfileVersion": 3,
                "requires": True,
                "packages": {
                    "": {"name": "ub-mcp-test", "version": "1.0.0",
                         "dependencies": {"uuid": "^10.0.0", "is-number": "^7.0.0"}},
                    "node_modules/uuid": {"version": "10.0.0"},
                    "node_modules/is-number": {"version": "7.0.0"},
                },
            }, indent=2), encoding="utf-8")

            before = {p.name: p.read_bytes() for p in proj.iterdir()}
            state = root / "state"
            async for session in self._session(state):
                result = await session.call_tool(TOOL_NAME, {
                    "project_path": str(proj), "target_level": "yellow",
                    "min_lag_ok_pct": 80, "deadline_seconds": 90.0,
                })
            after = {p.name: p.read_bytes() for p in proj.iterdir()}
            self.assertEqual(before, after, "get_upgrade_plan must never write into the project")

        self.assertFalse(result.is_error, result.content[0].text if hasattr(result, "content") else "?")
        payload = json.loads(result.content[0].text)
        self.assertTrue(payload["ok"], payload.get("error"))
        self.assertEqual(payload["protocol"], KIND)
        self.assertEqual(payload["status"]["authority"], "PLANNING_ONLY")
        self.assertEqual(payload["status"]["verificationStatus"], "NOT_VERIFIED")
        self.assertIn("honesty", payload)
        self.assertIn("boundaryNote", payload)
        for dep in payload["dependencies"]:
            self.assertNotIn(dep["status"], RESERVED_VERIFIED)
        self.assertIn("draftStatus", payload["status"])

    async def test_deadline_is_clamped_on_the_wire(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ub-mcp-state-") as state:
            async for session in self._session(Path(state)):
                result = await session.call_tool(TOOL_NAME, {
                    "project_path": "C:/nope", "deadline_seconds": 0.5,
                })
        self.assertTrue(result.is_error)
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["error"]["code"], "project-not-found")


if __name__ == "__main__":
    asyncio.run(unittest.main(verbosity=2))
