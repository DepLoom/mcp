"""Process runner lifecycle tests (tree-kill, cancellation, bounded output).

These exercise `deploom_mcp.process_runner` on controlled child processes
(never a live npm registry): a slow sleeper is spawned, and the tests assert
that on timeout, cooperative cancellation (`cancel_event`) or asyncio task
cancellation the WHOLE process tree is killed and no orphan survives, and that
a noisy child cannot exhaust memory (bounded output).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploom_mcp.process_runner import (  # noqa: E402
    run_process,
    run_process_async,
)

SLEEP_CODE = "import time; time.sleep(120)"
LONG_LINE_CODE = "import sys; sys.stdout.write('x' * 1_000_000)"
GRANDCHILD_CODE = (
    "import subprocess, sys, time\n"
    "me = sys.executable\n"
    "p = subprocess.Popen([me, '-c', 'import time; time.sleep(120)'])\n"
    "open(sys.argv[1], 'w').write(str(p.pid))\n"
    "time.sleep(120)\n"
)


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=15,
            ).stdout
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            return True
        for line in out.splitlines():
            if line.strip() and f'"{pid}"' in line:
                return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:  # pragma: no cover
        return False


class ProcessRunnerSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="pr-sync-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self._tmp)]) if os.name != "nt" else None)
        import shutil
        self.addCleanup(lambda: shutil.rmtree(self._tmp, True))

    def _run(self, code: str, *, timeout: float = 10, max_output_bytes: int = 65536, args: tuple = ()):
        return run_process(
            [sys.executable, "-c", code, *args],
            cwd=str(self._tmp),
            env=dict(os.environ),
            timeout=timeout,
            max_output_bytes=max_output_bytes,
        )

    def test_success_captures_output(self) -> None:
        result = self._run("print('hello'); import sys; print('err', file=sys.stderr)")
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)
        self.assertIn("err", result.stderr)
        self.assertFalse(result.timed_out)

    def test_nonzero_returncode_is_visible(self) -> None:
        result = self._run("raise SystemExit(7)")
        self.assertEqual(result.returncode, 7)
        self.assertFalse(result.timed_out)

    def test_timeout_kills_process_and_is_reported(self) -> None:
        start = time.monotonic()
        result = self._run(SLEEP_CODE, timeout=2)
        elapsed = time.monotonic() - start
        self.assertTrue(result.timed_out)
        self.assertLess(elapsed, 20, "timeout must return promptly after tree-kill")
        self.assertNotEqual(result.returncode, 0)

    def test_timeout_kills_whole_child_tree(self) -> None:
        pidfile = self._tmp / "child.pid"
        result = self._run(GRANDCHILD_CODE, timeout=2, args=(str(pidfile),))
        self.assertTrue(result.timed_out)
        pid = pidfile.read_text().strip()
        self.assertTrue(pid.isdigit())
        # Give the OS a moment to reap after taskkill.
        for _ in range(50):
            if not _pid_alive(int(pid)):
                break
            time.sleep(0.1)
        self.assertFalse(_pid_alive(int(pid)), "grandchild must not outlive the killed tree")

    def test_output_is_bounded_tail(self) -> None:
        result = self._run(LONG_LINE_CODE, max_output_bytes=4096)
        self.assertEqual(result.returncode, 0)
        self.assertLessEqual(len(result.stdout), 4096)
        self.assertEqual(result.stdout, "x" * len(result.stdout))


class ProcessRunnerAsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="pr-async-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self._tmp, True))

    async def _run(self, code: str, *, timeout: float = 30, cancel_event=None, args: tuple = ()):
        return await run_process_async(
            [sys.executable, "-c", code, *args],
            cwd=str(self._tmp),
            env=dict(os.environ),
            timeout=timeout,
            cancel_event=cancel_event,
        )

    async def test_success_async(self) -> None:
        result = await self._run("print('ok')")
        self.assertEqual(result.returncode, 0)
        self.assertIn("ok", result.stdout)

    async def test_timeout_async_kills_and_reports(self) -> None:
        start = time.monotonic()
        result = await self._run(SLEEP_CODE, timeout=2)
        self.assertLess(time.monotonic() - start, 20)
        self.assertTrue(result.timed_out)

    async def test_cancel_event_kills_process_and_returns(self) -> None:
        pidfile = self._tmp / "child.pid"
        cancel = asyncio.Event()

        async def arm():
            await asyncio.sleep(0.3)
            cancel.set()

        asyncio.create_task(arm())
        start = time.monotonic()
        result = await self._run(GRANDCHILD_CODE, cancel_event=cancel, args=(str(pidfile),))
        self.assertLess(time.monotonic() - start, 20)
        self.assertFalse(result.timed_out, "a requested cancel is not a timeout")
        self.assertNotEqual(result.returncode, 0)
        pid = pidfile.read_text().strip()
        self.assertTrue(pid.isdigit())
        for _ in range(50):
            if not _pid_alive(int(pid)):
                break
            await asyncio.sleep(0.1)
        self.assertFalse(_pid_alive(int(pid)), "cooperative cancel must kill the whole tree")

    async def test_task_cancellation_kills_process_whole_tree(self) -> None:
        pidfile = self._tmp / "child.pid"
        task = asyncio.create_task(self._run(GRANDCHILD_CODE, args=(str(pidfile),)))
        await asyncio.sleep(0.3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        for _ in range(50):
            if pidfile.exists() and pidfile.read_text().strip().isdigit():
                break
            await asyncio.sleep(0.1)
        pid = pidfile.read_text().strip()
        self.assertTrue(pid.isdigit())
        for _ in range(50):
            if not _pid_alive(int(pid)):
                break
            await asyncio.sleep(0.1)
        self.assertFalse(_pid_alive(int(pid)), "task cancellation must kill the whole tree")

    async def test_many_calls_succeed_after_previous_cancellation(self) -> None:
        cancel = asyncio.Event()
        asyncio.get_event_loop().call_later(0.2, cancel.set)
        await self._run(SLEEP_CODE, timeout=30, cancel_event=cancel)
        result = await self._run("print('after')")
        self.assertEqual(result.returncode, 0)
        self.assertIn("after", result.stdout)


if __name__ == "__main__":
    unittest.main()
