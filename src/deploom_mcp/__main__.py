"""Entry point for `python -m deploom_mcp`: run the MCP stdio server."""
from __future__ import annotations

from deploom_mcp.server import main

if __name__ == "__main__":
    raise SystemExit(main())
