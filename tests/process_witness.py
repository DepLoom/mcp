"""Test-only native process-object witness; errors are UNKNOWN, never dead.

Bind while an owned fixture is alive. A Windows handle pins that object even
if its PID is subsequently reused. No process enumeration, shell or privilege
changes. This does not establish arbitrary descendant-tree coverage.
"""
from __future__ import annotations
import ctypes
import json
import os
import threading
import time
import uuid
from pathlib import Path

class WitnessUnknown(RuntimeError):
    pass

if os.name == "nt":
    from ctypes import wintypes
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel.OpenProcess.restype = wintypes.HANDLE
    _kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel.WaitForSingleObject.restype = wintypes.DWORD
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL

class ProcessWitness:
    def __init__(self, pid):
        if os.name != "nt":
            raise WitnessUnknown("Windows process-object witness unavailable")
        self.handle = _kernel.OpenProcess(0x100000 | 0x1000, False, int(pid))
        if not self.handle:
            raise WitnessUnknown(f"OpenProcess failed: {ctypes.get_last_error()}")
        self.pid = int(pid)

    def alive(self):
        if not self.handle:
            raise WitnessUnknown("unbound/closed handle")
        state = _kernel.WaitForSingleObject(self.handle, 0)
        if state == 0:
            return False
        if state == 0x102:
            return True
        raise WitnessUnknown(f"WaitForSingleObject failed: {state}/{ctypes.get_last_error()}")

    def wait_dead(self, milliseconds):
        if not self.handle or not isinstance(milliseconds, int) or not 0 <= milliseconds <= 5000:
            raise WitnessUnknown("unbound handle or invalid bounded wait")
        state = _kernel.WaitForSingleObject(self.handle, milliseconds)
        if state == 0:
            return True
        if state == 0x102:
            return False
        raise WitnessUnknown(f"WaitForSingleObject failed: {state}/{ctypes.get_last_error()}")

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            if not _kernel.CloseHandle(handle):
                raise WitnessUnknown(f"CloseHandle failed: {ctypes.get_last_error()}")

class TreeWitness:
    """Prebind the two known fixture objects through an owned nonce handshake."""
    def __init__(self, pidfile):
        self.pidfile = Path(pidfile)
        self.nonce = uuid.uuid4().hex
        self.pidfile.with_suffix(".nonce").write_text(self.nonce, encoding="ascii")
        self.handles = {}
        self.error = None
        self.bound_at = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _watch(self):
        while not self._stop.wait(.005):
            try:
                data = json.loads(self.pidfile.with_suffix(".tree.json").read_text(encoding="ascii"))
            except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                continue
            except BaseException as exc:
                self.error = exc
                return
            try:
                if data["nonce"] != self.nonce:
                    raise WitnessUnknown("fixture nonce mismatch")
                for key in ("parent", "child"):
                    self.handles[key] = ProcessWitness(data[key])
                if not all(h.alive() for h in self.handles.values()):
                    raise WitnessUnknown("fixture already dead before binding")
                self.bound_at = time.monotonic()
            except BaseException as exc:
                self.error = exc
            return

    def require_bound(self, *, before=None):
        if self.error:
            raise WitnessUnknown(str(self.error)) from self.error
        if self.bound_at is None or set(self.handles) != {"parent", "child"}:
            raise WitnessUnknown("fixture objects not prebound")
        if before is not None and self.bound_at > before:
            raise WitnessUnknown("objects bound after cancellation/timeout")

    def any_alive(self):
        self.require_bound()
        return any(h.alive() for h in self.handles.values())

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1)
        if self._thread.is_alive():
            raise WitnessUnknown("witness thread did not stop")
        for handle in self.handles.values():
            handle.close()
