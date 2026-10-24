#!/usr/bin/env python3
"""Slow planner core used ONLY by the DepLoom/mcp cancellation test (P2-cancel).

Lives next to `fake_core` but is intentionally separate: `fake_core` is used by
deterministic unit tests, while this fixture exists to prove that a real MCP
request aborted over the stdio protocol actually kills the running planner
process tree (the client sends `notifications/cancelled`, the server cancels
the tool handler, the async process runner kills the child tree).

The fixture accepts the same headless argv as the real pinned core and
publishes the same Draft artifacts on success. Behaviour is controlled by
environment variables (never by the project):

  SLOW_CORE_MARKER      absolute path of a marker file. In "slow" mode the
                        fixture appends `<pid>|<n>|<ms>\n` every 50 ms while it
                        is alive, flushing each line, so the test can watch
                        liveness: while the marker keeps growing the planner is
                        running; once it stops growing after a cancellation the
                        process tree has been killed.
  SLOW_CORE_MODE_FILE   optional absolute path of a file whose trimmed content
                        is the mode, re-read at every invocation. This lets one
                        server session serve a slow (cancelled) call followed by
                        a fast (successful) call: the test rewrites the file
                        between the two calls.
  SLOW_CORE_MODE        fallback mode when SLOW_CORE_MODE_FILE is unset.
                        "slow" (default) waits ~600 s; "ok" delegates to the
                        fake core and produces a valid blueprint.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_MODE = os.environ.get("SLOW_CORE_MODE", "slow")
_mode_file = os.environ.get("SLOW_CORE_MODE_FILE")
if _mode_file:
    try:
        text = Path(_mode_file).read_text(encoding="utf-8").strip()
        if text:
            _MODE = text
    except OSError:
        _MODE = "slow"
MARKER = os.environ.get("SLOW_CORE_MARKER", "")


def _arg(name: str, default: str = "") -> str:
    argv = sys.argv
    for i, item in enumerate(argv):
        if item == name and i + 1 < len(argv):
            return argv[i + 1]
    return default


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def slow_mode() -> int:
    marker_path = Path(MARKER) if MARKER else None
    pid = os.getpid()
    n = 0
    while True:
        n += 1
        if marker_path is not None:
            try:
                marker_path.parent.mkdir(parents=True, exist_ok=True)
                with marker_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"{pid}|{n}|{int(time.monotonic() * 1000)}\n")
                    fh.flush()
            except OSError:
                pass
        time.sleep(0.05)


def ok_mode() -> int:
    # Reproduce the deterministic fake-core output so a follow-up call in the
    # same server session can succeed. The fixture lives three levels below the
    # repo root (tests/fixtures/slow_core/... -> repo root).
    repo_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo_root))
    from tests.fixtures.fake_core.dependency_live_roadmap_generator import main as fake_main

    return fake_main()


def main() -> int:
    if _MODE == "ok":
        return ok_mode()
    return slow_mode()


if __name__ == "__main__":
    raise SystemExit(main())
