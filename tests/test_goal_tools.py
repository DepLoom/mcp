import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from deploom_mcp.goal_tools import get_goal_plan
from deploom_mcp.process_runner import ProcessRun
import test_goal_contract

class GoalToolBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        data=test_goal_contract.GoalContractTests();data.setUp();self.goal=data.goal
    async def test_implicit_profile_never_runs_worker(self):
        goal=dict(self.goal);goal.pop("goalKind")
        with patch("deploom_mcp.goal_tools.run_process_async",new_callable=AsyncMock) as worker:
            result=await get_goal_plan(str(Path("public-fixture").absolute()),goal)
            self.assertFalse(result["ok"]);worker.assert_not_awaited()
    async def test_unbounded_deadline_never_runs_worker(self):
        for deadline in [float("nan"),float("inf"),0,61,True]:
            with patch("deploom_mcp.goal_tools.run_process_async",new_callable=AsyncMock) as worker:
                result=await get_goal_plan(str(Path("public-fixture").absolute()),self.goal,deadline)
                self.assertEqual(result["error"]["code"],"INVALID_DEADLINE");worker.assert_not_awaited()
    async def test_timeout_cannot_produce_success(self):
        with patch("deploom_mcp.goal_tools.run_process_async",new_callable=AsyncMock,return_value=ProcessRun(-1,'{"ok":true}',"",True)):
            result=await get_goal_plan(str(Path("public-fixture").absolute()),self.goal)
            self.assertEqual(result["error"]["code"],"GOAL_BINDING_TIMEOUT")
    async def test_process_failure_cannot_be_overridden_by_stdout_success(self):
        with patch("deploom_mcp.goal_tools.run_process_async",new_callable=AsyncMock,return_value=ProcessRun(1,'{"ok":true}',"private diagnostic")):
            result=await get_goal_plan(str(Path("public-fixture").absolute()),self.goal)
            self.assertFalse(result["ok"]);self.assertNotIn("private",str(result))
    async def test_worker_cannot_promote_planning_authority(self):
        for payload in ['{"ok":true}', '{"ok":true,"authority":"VERIFIED"}']:
            with patch("deploom_mcp.goal_tools.run_process_async",new_callable=AsyncMock,return_value=ProcessRun(0,payload,"")):
                result=await get_goal_plan(str(Path("public-fixture").absolute()),self.goal)
                self.assertFalse(result["ok"])
    async def test_invalid_output_fails_closed(self):
        with patch("deploom_mcp.goal_tools.run_process_async",new_callable=AsyncMock,return_value=ProcessRun(0,"not JSON","")):
            result=await get_goal_plan(str(Path("public-fixture").absolute()),self.goal)
            self.assertFalse(result["ok"])
    async def test_cancellation_propagates_to_owning_runner(self):
        with patch("deploom_mcp.goal_tools.run_process_async",new_callable=AsyncMock,side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):await get_goal_plan(str(Path("public-fixture").absolute()),self.goal)

if __name__=="__main__":unittest.main()
