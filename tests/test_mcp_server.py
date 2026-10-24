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
import contextlib
import json
import os
import sys
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters, stdio_client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402
from mcp.shared.jsonrpc_dispatcher import REQUEST_TIMEOUT  # noqa: E402

from deploom_mcp.server import (  # noqa: E402
    DEFAULT_DEADLINE_SECONDS,
    DEFAULT_INLINE_GROUPS,
    DEFAULT_INLINE_ROWS,
    DEFAULT_PACKAGES_PER_GROUP,
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


def _tree_snapshot(root: Path) -> Dict[str, str]:
    """Path -> sha256 for every file under root (deterministic key order)."""
    import hashlib

    snapshot: Dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            snapshot[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snapshot


class ManagedStateIsolationTests(unittest.TestCase):
    """P1-fs: the managed state dir must never overlap the analysed project,
    and a rejected request must not create anything on disk."""

    STATE_ENV = "DEPLOOM_MCP_STATE_DIR"

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="ub-mcp-state-iso-"))
        self._saved_env = os.environ.get(self.STATE_ENV)
        os.environ.pop(self.STATE_ENV, None)
        self.addCleanup(self._restore_env)
        self.addCleanup(_remove_tree, self._tmp)

    def _restore_env(self) -> None:
        if self._saved_env is None:
            os.environ.pop(self.STATE_ENV, None)
        else:
            os.environ[self.STATE_ENV] = self._saved_env

    def _make_project(self) -> Path:
        proj = self._tmp / "proj"
        proj.mkdir(parents=True)
        (proj / "package.json").write_text(json.dumps({"name": "app", "version": "1.0.0"}), encoding="utf-8")
        return proj

    def test_state_dir_inside_project_is_rejected_without_side_effects(self) -> None:
        proj = self._make_project()
        state = proj / "deploom-state"
        os.environ[self.STATE_ENV] = str(state)
        before = _tree_snapshot(self._tmp)

        res = get_upgrade_plan(project_path=str(proj), output_dir=str(self._tmp / "out"))

        self.assertIs(res["ok"], False)
        self.assertEqual(res["error"]["code"], "state-inside-project")
        self.assertIn("must not overlap", res["error"]["message"])
        self.assertNotIn("runId", [k for k in res if k != "ok"])
        after = _tree_snapshot(self._tmp)
        self.assertEqual(before, after, "a rejected request must not create any file or directory")

    def test_project_nested_inside_state_dir_is_rejected(self) -> None:
        # State root is an ancestor of the project: managed writes could land
        # inside the subtree under analysis, so both overlap directions fail.
        proj = self._tmp / "work" / "proj"
        proj.mkdir(parents=True)
        (proj / "package.json").write_text(json.dumps({"name": "app"}), encoding="utf-8")
        os.environ[self.STATE_ENV] = str(self._tmp / "work")
        before = _tree_snapshot(self._tmp)

        res = get_upgrade_plan(project_path=str(proj), output_dir=str(self._tmp / "out"))

        self.assertIs(res["ok"], False)
        self.assertEqual(res["error"]["code"], "state-inside-project")
        after = _tree_snapshot(self._tmp)
        self.assertEqual(before, after)

    def test_symlinked_state_inside_project_is_rejected(self) -> None:
        proj = self._make_project()
        real = self._tmp / "real-state"
        real.mkdir()
        link = proj / "state-link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except OSError as exc:  # pragma: no cover - Windows may lack symlink privileges
            self.skipTest(f"cannot create symlink: {exc}")
        os.environ[self.STATE_ENV] = str(link)
        before = _tree_snapshot(self._tmp)

        res = get_upgrade_plan(project_path=str(proj), output_dir=str(self._tmp / "out"))

        self.assertIs(res["ok"], False)
        self.assertEqual(res["error"]["code"], "state-inside-project")
        after = _tree_snapshot(self._tmp)
        self.assertEqual(before, after)

    def test_concurrent_calls_share_state_without_clobbering(self) -> None:
        # A shared managed-state root that overlaps the project must be rejected
        # identically by concurrent callers, before any write can leak.
        proj = self._make_project()
        os.environ[self.STATE_ENV] = str(proj / "state")
        before = _tree_snapshot(self._tmp)
        codes = []
        errors = []

        def worker() -> None:
            try:
                res = get_upgrade_plan(project_path=str(proj), output_dir=str(self._tmp / "out"))
                codes.append(res["error"]["code"] if not res["ok"] else "UNEXPECTED_OK")
            except Exception as exc:  # pragma: no cover
                errors.append(f"exc:{exc}")

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(worker) for _ in range(4)]
            for fut in futs:
                fut.result()

        self.assertEqual(errors, [], f"unexpected per-call exceptions: {errors}")
        self.assertEqual(codes, ["state-inside-project"] * 4)
        after = _tree_snapshot(self._tmp)
        self.assertEqual(before, after, "concurrent rejected calls must not create anything")


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)

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

    @staticmethod
    def _huge_blueprint(groups: int = 80, packages_per_group: int = 80, rows: int = 6000) -> Dict[str, Any]:
        recommendation_groups = [
            {"group": g, "count": packages_per_group, "needsReview": False,
             "packages": sorted(f"pkg{g}-{p}" for p in range(packages_per_group))}
            for g in range(groups)
        ]
        deps = [{"package": f"pkg{i % groups}-{i % packages_per_group}", "status": "PROPOSED",
                 "current": "1.0.0", "target": "2.0.0", "group": i % groups,
                 "needsReview": False} for i in range(rows)]
        return {
            "schemaVersion": 2,
            "kind": "upgrade-blueprint",
            "status": {"draftStatus": "DRAFT_READY", "verificationStatus": "NOT_VERIFIED",
                       "authority": "PLANNING_ONLY"},
            "summary": {"totalDependencies": rows, "proposed": rows, "needsReview": 0,
                        "statusCounts": {"PROPOSED": rows}},
            "source": {"project": {"name": "app"}},
            "run": {"runId": "run-big"},
            "recommendationGroups": recommendation_groups,
            "dependencies": deps,
            "blueprintFile": "C:/state/out/bp.json",
            "artifacts": {"plan": "C:/state/out/plan.json"},
        }

    def test_groups_and_package_lists_are_bounded_with_explicit_truncation(self) -> None:
        compact = compact_blueprint(self._huge_blueprint())
        trunc = compact["truncation"]
        self.assertTrue(trunc["truncated"])
        self.assertEqual(trunc["totalDependencies"], 6000)
        self.assertEqual(trunc["inlineDependencies"], DEFAULT_INLINE_ROWS)
        self.assertLessEqual(trunc["inlineGroups"], DEFAULT_INLINE_GROUPS)
        self.assertEqual(trunc["totalGroups"], 80)
        self.assertLessEqual(trunc["inlineGroups"], 50)
        for group in compact["recommendationGroups"]:
            self.assertLessEqual(len(group["packages"]), DEFAULT_PACKAGES_PER_GROUP)
            self.assertLessEqual(len(group["packages"]), 50)
        self.assertIn("rows", trunc["limitsHit"])
        self.assertIn("groups", trunc["limitsHit"])
        self.assertIn("packages", trunc["limitsHit"])
        # Counts and artifact pointers stay complete even when display is clipped.
        self.assertEqual(compact["summary"]["statusCounts"], {"PROPOSED": 6000})
        self.assertEqual(compact["artifacts"]["plan"], "C:/state/out/plan.json")

    def test_byte_budget_is_enforced_deterministically(self) -> None:
        bp = self._huge_blueprint(groups=200, packages_per_group=200, rows=20000)
        first = compact_blueprint(bp, max_response_bytes=4096)
        second = compact_blueprint(bp, max_response_bytes=4096)
        self.assertLessEqual(first["truncation"]["bytes"], first["truncation"]["byteLimit"])
        self.assertIn("bytes", first["truncation"]["limitsHit"])
        self.assertEqual(first, second, "byte-budget reduction must be deterministic")
        # The protocol envelope and the complete counts survive any reduction.
        self.assertEqual(first["protocol"], KIND)
        self.assertEqual(first["summary"]["statusCounts"], {"PROPOSED": 20000})

    def test_small_plan_never_truncates(self) -> None:
        blueprint = self._huge_blueprint(groups=2, packages_per_group=3, rows=4)
        compact = compact_blueprint(blueprint)
        self.assertFalse(compact["truncation"]["truncated"])
        self.assertEqual(compact["truncation"]["limitsHit"], [])
        self.assertEqual(compact["truncation"]["totalDependencies"], 4)
        self.assertEqual(len(compact["dependencies"]), 4)


class MCPStdioRoundTripTests(unittest.IsolatedAsyncioTestCase):
    """Boot the real server over stdio via the official MCP client SDK."""

    async def _session(self, state_dir: Path, extra_env: Optional[Mapping[str, str]] = None):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "deploom_mcp"],
            cwd=str(ROOT),
            env={**os.environ, "DEPLOOM_MCP_STATE_DIR": str(state_dir), **(extra_env or {})},
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

    async def test_state_dir_inside_project_rejected_over_stdio_without_side_effects(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ub-mcp-statetool-") as tmp:
            root = Path(tmp)
            proj = root / "proj"
            proj.mkdir()
            (proj / "package.json").write_text(json.dumps({"name": "app", "version": "1.0.0"}), encoding="utf-8")
            before = _tree_snapshot(root)

            state = proj / "state"  # managed state NESTED inside the analysed project
            async for session in self._session(state):
                result = await session.call_tool(TOOL_NAME, {"project_path": str(proj)})
            after = _tree_snapshot(root)

            self.assertTrue(result.is_error)
            payload = json.loads(result.content[0].text)
            self.assertIs(payload["ok"], False)
            self.assertEqual(payload["error"]["code"], "state-inside-project")
            self.assertEqual(before, after, "rejected request must not create the state dir or anything else")
            self.assertFalse(state.exists(), "managed state dir must never be materialised inside the project")

    async def test_cancelling_call_tool_kills_planner_and_session_survives(self) -> None:
        """P2-cancel: a `notifications/cancelled` sent over real stdio (via the
        SDK's per-request read-timeout abandon path) must kill the running
        planner process tree; the same session must keep serving new requests."""
        slow_fixture = ROOT / "tests" / "fixtures" / "slow_core" / "dependency_live_roadmap_generator.py"
        self.assertTrue(slow_fixture.is_file(), f"slow-core fixture missing: {slow_fixture}")
        with tempfile.TemporaryDirectory(prefix="ub-mcp-cancel-") as tmp:
            root = Path(tmp)
            proj = root / "proj"
            proj.mkdir()
            (proj / "package.json").write_text(
                json.dumps({"name": "app", "version": "1.0.0", "dependencies": {"uuid": "^10.0.0"}}),
                encoding="utf-8",
            )
            marker = root / "marker.txt"
            mode_file = root / "mode.txt"
            mode_file.write_text("slow", encoding="utf-8")
            env = {
                "DEPLOOM_MCP_GENERATOR_PATH": str(slow_fixture),
                "SLOW_CORE_MARKER": str(marker),
                "SLOW_CORE_MODE_FILE": str(mode_file),
            }
            state = root / "state"
            async for session in self._session(state, extra_env=env):
                # The SDK sends `notifications/cancelled` when a request hits its
                # own read-timeout deadline (courtesy-cancel abandon path). Fire
                # it well after the planner is provably alive but far before the
                # server's 120 s planner timeout.
                call = asyncio.ensure_future(session.call_tool(
                    TOOL_NAME, {"project_path": str(proj)}, read_timeout_seconds=15.0,
                ))

                # Wait until the planner is provably alive: the fixture appends a
                # heartbeat line ~every 50 ms, so >=3 lines mean it is mid-run.
                for _ in range(400):
                    if marker.is_file() and len(marker.read_text(encoding="utf-8").strip().splitlines()) >= 3:
                        break
                    await asyncio.sleep(0.05)
                else:
                    call.cancel()
                    with contextlib.suppress(BaseException):
                        await call
                    self.fail("planner never started (heartbeat marker never appeared)")

                # The abandoned request raises MCPError with the SDK's timeout
                # code client-side, while the server cancels the handler and the
                # async process runner kills the whole planner child tree.
                # Propagation is not instant (kill completes a few hundred ms
                # after the client-side raise), so wait a bounded grace, then
                # require the heartbeat marker to stop growing.
                with self.assertRaises(MCPError) as cm:
                    await call
                self.assertEqual(cm.exception.code, REQUEST_TIMEOUT)

                await asyncio.sleep(1.0)  # grace for cancel -> scope.cancel -> child kill
                count_after_cancel = len(marker.read_text(encoding="utf-8").strip().splitlines())
                await asyncio.sleep(0.5)
                count_later = len(marker.read_text(encoding="utf-8").strip().splitlines())
                self.assertEqual(
                    count_after_cancel, count_later,
                    "planner kept running after cancellation (heartbeat marker still growing)",
                )

                # Same server session must still serve a fresh, successful call.
                mode_file.write_text("ok", encoding="utf-8")
                result = await session.call_tool(TOOL_NAME, {"project_path": str(proj)})
            self.assertFalse(result.is_error, result.content[0].text if hasattr(result, "content") else "?")
            payload = json.loads(result.content[0].text)
            self.assertTrue(payload["ok"], payload.get("error"))
            self.assertEqual(payload["status"]["draftStatus"], "DRAFT_READY")


if __name__ == "__main__":
    asyncio.run(unittest.main(verbosity=2))
