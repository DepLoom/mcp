from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
import deploom_mcp

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class AuditStdioTests(unittest.TestCase):
    def test_extended_server_exposes_both_tools_and_rejects_invalid_audit(self):
        async def scenario():
            env = os.environ.copy()
            # Test the package imported by this runner, not a different globally
            # installed wheel selected by a fresh child's default sys.path.
            env["PYTHONPATH"] = str(Path(deploom_mcp.__file__).resolve().parent.parent)
            parameters = StdioServerParameters(command=sys.executable,
                args=["-m", "deploom_mcp.audit_server"], env=env)
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual({"get_upgrade_plan", "evaluate_assignment", "explain_conflict", "audit_dependencies", "get_goal_plan", "get_goal_report", "verify_goal_candidate", "apply_goal_candidate", "get_goal_checkpoint", "observe_workspace_inventory_v1"}, {tool.name for tool in tools.tools})
                    result = await session.call_tool("audit_dependencies", {"project_path": "relative"})
                    self.assertTrue(result.is_error)
                    self.assertEqual("invalid-project", result.structured_content["error"]["code"])
                    self.assertEqual("UNKNOWN", result.structured_content["security"]["verdict"])
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
