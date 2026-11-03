"""Exercise the HTTP entry point and the default stdio contract."""
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from deploom_mcp import server as module


class TransportTests(unittest.TestCase):
    def test_default_stdio(self):
        with patch.object(sys, "argv", ["deploom-mcp"]), patch.object(module.server, "run") as run:
            self.assertEqual(module.main(), 0)
            run.assert_called_once_with(transport="stdio")

    def test_http_security(self):
        for extra, expected in [([], ["127.0.0.1:*", "localhost:*", "[::1]:*"]), (["--allowed-host", "mcp.example.com"], ["mcp.example.com"])]:
            with self.subTest(extra=extra), patch.object(sys, "argv", ["deploom-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", *extra]), patch.object(module.server, "run") as run:
                module.main()
                settings = run.call_args.kwargs["transport_security"]
                self.assertTrue(settings.enable_dns_rebinding_protection)
                self.assertEqual(settings.allowed_hosts, expected)

    def test_real_http_protocol(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        proc = subprocess.Popen([sys.executable, "-m", "deploom_mcp", "--transport", "streamable-http", "--port", str(port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    self.fail("HTTP server exited before becoming ready")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                self.fail("HTTP server did not start")

            async def exercise():
                async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        self.assertIn("get_upgrade_plan", [tool.name for tool in tools.tools])
                        result = await session.call_tool("get_upgrade_plan", {"project_path": ""})
                        self.assertTrue(result.is_error)
            asyncio.run(exercise())
        finally:
            proc.terminate()
            proc.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
