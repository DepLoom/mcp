#!/usr/bin/env python3
"""stdio smoke test for `python -m deploom_mcp`.

Boots the real MCP server over stdio with the official client SDK and asserts
that initialize + list_tools succeed. A server that leaks non-protocol bytes on
stdout (e.g. a stray print at import time) would break the JSON-RPC framing and
make initialize/list_tools fail, so this is a direct cleanliness check as well
as a startup smoke test.
"""
from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters, stdio_client


async def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "deploom_mcp"],
        cwd=root,
        env={**os.environ},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
    assert len(listed.tools) >= 1, "no tools exposed"
    names = [t.name for t in listed.tools]
    assert "get_upgrade_plan" in names, f"get_upgrade_plan missing, got {names}"
    print("smoke ok: initialize + list_tools over clean stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
