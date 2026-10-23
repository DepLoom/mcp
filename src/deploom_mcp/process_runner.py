#!/usr/bin/env python3
"""Shared subprocess runner for the planner process with hardening.

Both the synchronous CLI/unit path and the asynchronous MCP path run the
planner through this module so every caller gets the same guarantees:

* the whole process tree of the child is killed on timeout, external
  cancellation or task cancellation (Windows: process group + ``taskkill
  /T /F``; POSIX: ``killpg``);
* stdout/stderr are captured with a hard memory budget (tail kept, overflow
  discarded) so a noisy child can never exhaust the host;
* a timeout is reported explicitly (``timed_out``) and never surfaces as a
  generic failure.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

_DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024

_CREATION_FLAGS = 0
if os.name == "nt":
    _CREATION_FLAGS = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "CREATE_NO_WINDOW", 0
    )


@dataclass(frozen=True)
class ProcessRun:
    """Outcome of one child invocation."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class _TailBuffer:
    """Bounded byte buffer that keeps only the tail of a stream."""

    __slots__ = ("_tail", "_cap", "_overflow")

    def __init__(self, cap: int) -> None:
        self._tail = bytearray()
        self._cap = max(1, int(cap))
        self._overflow = 0

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._tail.extend(chunk)
        excess = len(self._tail) - self._cap
        if excess > 0:
            del self._tail[:excess]
            self._overflow += excess

    def total(self) -> int:
        return self._overflow + len(self._tail)

    def text(self, max_chars: Optional[int] = None) -> str:
        text = bytes(self._tail).decode("utf-8", errors="replace")
        if max_chars is not None and len(text) > max_chars:
            text = text[-max_chars:]
        return text


def _popen_kwargs() -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = _CREATION_FLAGS
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _drain(stream, buffer: _TailBuffer) -> None:
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            buffer.append(chunk)
    except (OSError, ValueError):  # pragma: no cover - broken pipe after kill
        pass
    finally:
        try:
            stream.close()
        except OSError:  # pragma: no cover
            pass


def kill_process_tree(proc) -> None:
    """Kill the child and every descendant (the whole process tree).

    Works for both ``subprocess.Popen`` (exposes ``poll()``) and the asyncio
    ``Process`` (exposes ``returncode`` only)."""
    if proc is None:
        return
    if getattr(proc, "returncode", None) is not None:
        return
    poll = getattr(proc, "poll", None)
    if poll is not None and callable(poll):
        try:
            if proc.poll() is not None:
                return
        except (OSError, ProcessLookupError):  # pragma: no cover
            return
    pid = int(getattr(proc, "pid", 0) or 0)
    if pid <= 0:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
            )
            return
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            pass
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except (OSError, ProcessLookupError):  # pragma: no cover
            pass
    try:
        proc.kill()
    except (OSError, ProcessLookupError):  # pragma: no cover
        pass


def run_process(
    argv: List[str],
    *,
    cwd: str,
    env: Dict[str, str],
    timeout: Optional[float] = None,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> ProcessRun:
    """Run one child synchronously with tree-kill and bounded output."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_popen_kwargs(),
    )
    out = _TailBuffer(max_output_bytes)
    err = _TailBuffer(max_output_bytes)
    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, out), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, err), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_process_tree(proc)
        try:
            returncode = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - tree-kill failed
            returncode = proc.poll() if proc.poll() is not None else -1
            kill_process_tree(proc)
    for thread in threads:
        thread.join(timeout=10)
    return ProcessRun(
        returncode=returncode,
        stdout=out.text(),
        stderr=err.text(),
        timed_out=timed_out,
    )


async def _async_drain(stream, buffer: _TailBuffer) -> None:
    try:
        while True:
            chunk = await stream.read(65536)
            if chunk in (b"", None):
                break
            buffer.append(chunk)
    except (
        OSError, ValueError, asyncio.CancelledError,
    ):  # pragma: no cover - broken pipe after kill
        pass


async def _wait_with_cancel(proc, cancel_event) -> int:
    while True:
        returncode = proc.returncode
        if returncode is not None:
            return returncode
        if cancel_event is not None and cancel_event.is_set():
            kill_process_tree(proc)
            return await proc.wait()
        await asyncio.sleep(0.05)


async def _stop_readers(readers, timeout: float = 5.0) -> None:
    """Wait for the reader tasks, then force-stop any that never reach EOF.

    A child killed externally (timeout/cancellation) may never deliver a final
    EOF to its pipes on Windows, so the drain must not block forever; a healthy
    run drains fully within the timeout."""
    try:
        await asyncio.wait_for(asyncio.gather(*readers), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


def _close_async_process(proc) -> None:
    """Best-effort close of the async subprocess transport so a killed child's
    pipes do not leak unclosed Proactor transports (and their ResourceWarnings)."""
    transport = getattr(proc, "_transport", None)
    if transport is None:
        return
    for stream in (proc.stdout, proc.stderr):
        if stream is None:
            continue
        try:
            stream.feed_eof()
        except Exception:  # pragma: no cover - defensive
            pass
    try:
        transport.close()
    except Exception:  # pragma: no cover - already closed
        pass


async def run_process_async(
    argv: List[str],
    *,
    cwd: str,
    env: Dict[str, str],
    timeout: Optional[float] = None,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    cancel_event: Optional[asyncio.Event] = None,
) -> ProcessRun:
    """Run one child asynchronously with tree-kill, bounded output and
    cooperative cancellation via ``cancel_event`` or task cancellation."""
    proc = await asyncio.create_subprocess_exec(
        argv[0],
        *argv[1:],
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **_popen_kwargs(),
    )
    out = _TailBuffer(max_output_bytes)
    err = _TailBuffer(max_output_bytes)
    readers = [
        asyncio.create_task(_async_drain(proc.stdout, out)),
        asyncio.create_task(_async_drain(proc.stderr, err)),
    ]
    timed_out = False
    try:
        if timeout is not None:
            returncode = await asyncio.wait_for(
                _wait_with_cancel(proc, cancel_event), timeout=timeout
            )
        else:
            returncode = await _wait_with_cancel(proc, cancel_event)
    except asyncio.TimeoutError:
        timed_out = True
        kill_process_tree(proc)
        returncode = await proc.wait()
    except BaseException:
        kill_process_tree(proc)
        try:
            await proc.wait()
        except (OSError, ProcessLookupError):  # pragma: no cover
            pass
        await _stop_readers(readers)
        _close_async_process(proc)
        raise
    await _stop_readers(readers)
    _close_async_process(proc)
    return ProcessRun(
        returncode=returncode,
        stdout=out.text(),
        stderr=err.text(),
        timed_out=timed_out,
    )
