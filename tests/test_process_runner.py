"""Process runner lifecycle tests (tree-kill, cancellation, bounded output).

These exercise `deploom_mcp.process_runner` on controlled child processes
(never a live npm registry): a slow sleeper is spawned, and the tests assert
that on timeout, cooperative cancellation (`cancel_event`) or asyncio task
cancellation the WHOLE process tree is killed and no orphan survives, and that
a noisy child cannot exhaust memory (bounded output).
"""
from __future__ import annotations

import asyncio
import json
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

from process_witness import TreeWitness, WitnessUnknown

SLEEP_CODE = "import time; time.sleep(120)"
LONG_LINE_CODE = "import sys; sys.stdout.write('x' * 1_000_000)"
GRANDCHILD_CODE = (
    "import subprocess, sys, time, os, json\n"
    "me = sys.executable\n"
    "p = subprocess.Popen([me, '-c', 'import time; time.sleep(120)'])\n"
    "from pathlib import Path\n"
    "f = Path(sys.argv[1])\n"
    "nonce = f.with_suffix('.nonce').read_text(encoding='ascii')\n"
    "meta = {'parent': os.getpid(), 'child': p.pid, 'nonce': nonce}\n"
    "temp = f.with_suffix('.tree.tmp')\n"
    "temp.write_text(json.dumps(meta), encoding='ascii')\n"
    "os.replace(temp, f.with_suffix('.tree.json'))\n"
    "f.write_text(str(p.pid), encoding='ascii')\n"
    "time.sleep(120)\n"
)


def _watch_tree(test, pidfile):
    # The fixture handshake exists on every OS; native pinned handles replace
    # only the Windows tasklist witness. POSIX keeps its original kill(0) check.
    if os.name != "nt":
        pidfile.with_suffix(".nonce").write_text("posix-fixture", encoding="ascii")
        return None
    witness = TreeWitness(pidfile)
    test.addCleanup(witness.close)
    return witness


async def _await_tree_ready(pidfile, witness, *, timeout=5.0):
    """Await the fixture precondition, never interpret unavailable evidence as dead."""
    if type(timeout) not in (int, float) or not 0 < timeout <= 10:
        raise ValueError('invalid bounded fixture readiness timeout')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if witness is not None:
            if witness.error is not None:
                witness.require_bound()
            if witness.bound_at is None:
                await asyncio.sleep(.005)
                continue
            witness.require_bound()
        try:
            raw = pidfile.read_text(encoding='ascii').strip()
            metadata = json.loads(pidfile.with_suffix('.tree.json').read_text(encoding='ascii'))
            nonce = pidfile.with_suffix('.nonce').read_text(encoding='ascii')
        except (FileNotFoundError, json.JSONDecodeError):
            await asyncio.sleep(.005)
            continue
        if not raw:
            await asyncio.sleep(.005)
            continue
        if not raw.isdigit() or metadata.get('child') != int(raw) or metadata.get('nonce') != nonce:
            raise WitnessUnknown('fixture PID/nonce readiness mismatch')
        if witness is not None:
            if witness.handles['child'].pid != int(raw) or not all(h.alive() for h in witness.handles.values()):
                raise WitnessUnknown('known fixture objects not alive before cancellation')
        else:
            # POSIX retains its separate native PID liveness semantics.
            os.kill(metadata['parent'], 0)
            os.kill(int(raw), 0)
        if witness is not None and time.monotonic() <= witness.bound_at:
            # Windows monotonic observations can share one clock quantum.
            # Establish strict recorded ordering; never relax the assertion.
            await asyncio.sleep(.001)
            continue
        return
    raise WitnessUnknown('known fixture objects not ready before bounded cancellation test')


async def _close_fixture_task(task):
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _pid_alive(pid: int, witness=None) -> bool:
    if os.name == "nt":
        if witness is None:
            raise WitnessUnknown("missing prebound fixture witness")
        witness.require_bound()
        if witness.handles["child"].pid != pid:
            raise WitnessUnknown("pid does not match bound fixture child")
        return witness.any_alive()
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
        witness = _watch_tree(self, pidfile)
        started = time.monotonic()
        result = self._run(GRANDCHILD_CODE, timeout=2, args=(str(pidfile),))
        if witness is not None:
            witness.require_bound(before=started + 2)
        self.assertTrue(result.timed_out)
        pid = pidfile.read_text().strip()
        self.assertTrue(pid.isdigit())
        # Give the OS a moment to reap after taskkill.
        for _ in range(50):
            if not _pid_alive(int(pid), witness):
                break
            time.sleep(0.1)
        self.assertFalse(_pid_alive(int(pid), witness), "grandchild must not outlive the killed tree")

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
        witness = _watch_tree(self, pidfile)
        cancel = asyncio.Event()
        start = time.monotonic()
        task = asyncio.create_task(self._run(GRANDCHILD_CODE, cancel_event=cancel, args=(str(pidfile),)))
        try:
            await _await_tree_ready(pidfile, witness)
            cancelled_at = time.monotonic()
            cancel.set()
            result = await task
        finally:
            await _close_fixture_task(task)
        self.assertLess(time.monotonic() - start, 20)
        if witness is not None:
            self.assertLess(witness.bound_at, cancelled_at)
            witness.require_bound(before=cancelled_at)
        self.assertFalse(result.timed_out, "a requested cancel is not a timeout")
        self.assertNotEqual(result.returncode, 0)
        pid = pidfile.read_text().strip()
        self.assertTrue(pid.isdigit())
        for _ in range(50):
            if not _pid_alive(int(pid), witness):
                break
            await asyncio.sleep(0.1)
        self.assertFalse(_pid_alive(int(pid), witness), "cooperative cancel must kill the whole tree")

    async def test_task_cancellation_kills_process_whole_tree(self) -> None:
        pidfile = self._tmp / "child.pid"
        witness = _watch_tree(self, pidfile)
        task = asyncio.create_task(self._run(GRANDCHILD_CODE, args=(str(pidfile),)))
        try:
            await _await_tree_ready(pidfile, witness)
            cancelled_at = time.monotonic()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            await _close_fixture_task(task)
        if witness is not None:
            self.assertLess(witness.bound_at, cancelled_at)
            witness.require_bound(before=cancelled_at)
        for _ in range(50):
            if pidfile.exists() and pidfile.read_text().strip().isdigit():
                break
            await asyncio.sleep(0.1)
        pid = pidfile.read_text().strip()
        self.assertTrue(pid.isdigit())
        for _ in range(50):
            if not _pid_alive(int(pid), witness):
                break
            await asyncio.sleep(0.1)
        self.assertFalse(_pid_alive(int(pid), witness), "task cancellation must kill the whole tree")

    async def test_cancelling_before_spawn_has_no_tree_death_claim(self) -> None:
        # This is a pre-spawn coroutine boundary, not proof of killing descendants.
        from unittest.mock import patch
        entered = asyncio.Event()
        never = asyncio.Event()

        async def before_spawn(*args, **kwargs):
            entered.set()
            await never.wait()
            raise AssertionError('cancelled pre-spawn gate unexpectedly resumed')

        with patch('deploom_mcp.process_runner.asyncio.create_subprocess_exec', side_effect=before_spawn) as spawn, \
             patch('deploom_mcp.process_runner.kill_process_tree') as kill:
            task = asyncio.create_task(self._run(SLEEP_CODE))
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                await _close_fixture_task(task)
        self.assertEqual(spawn.call_count, 1)
        kill.assert_not_called()

    async def test_many_calls_succeed_after_previous_cancellation(self) -> None:
        cancel = asyncio.Event()
        asyncio.get_event_loop().call_later(0.2, cancel.set)
        await self._run(SLEEP_CODE, timeout=30, cancel_event=cancel)
        result = await self._run("print('after')")
        self.assertEqual(result.returncode, 0)
        self.assertIn("after", result.stdout)


if __name__ == "__main__":
    unittest.main()
