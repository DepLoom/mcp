from __future__ import annotations

import asyncio
import os
import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class AuditStdioTests(unittest.TestCase):
    def test_extended_server_exposes_both_tools_and_rejects_invalid_audit(self):
        async def scenario():
            parameters = StdioServerParameters(command=sys.executable,
                args=["-m", "deploom_mcp.audit_server"], env=os.environ.copy())
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual({"get_upgrade_plan", "audit_dependencies"}, {tool.name for tool in tools.tools})
                    result = await session.call_tool("audit_dependencies", {"project_path": "relative"})
                    self.assertTrue(result.is_error)
                    self.assertEqual("invalid-project", result.structured_content["error"]["code"])
                    self.assertEqual("UNKNOWN", result.structured_content["security"]["verdict"])
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
