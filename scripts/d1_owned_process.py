"""Owned subprocess trees for the no-agent-tested D1 launcher.

Windows uses a dedicated Job Object with KILL_ON_JOB_CLOSE. Never taskkill by
name or walk unrelated processes. Linux/macOS use a new session + process
group. Fails closed if Windows job assignment fails; no paid model may start
until this facility has passed its local no-model regression.
"""
from __future__ import annotations
import os
import signal
import subprocess


class OwnedProcessError(RuntimeError):
    pass


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL

    class _WindowsJob:
        def __init__(self):
            self.handle = _k32.CreateJobObjectW(None, None)
            if not self.handle:
                raise OwnedProcessError("WINDOWS_JOB_CREATE_FAILED")
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            if not _k32.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                self.close()
                raise OwnedProcessError("WINDOWS_JOB_KILL_ON_CLOSE_FAILED")

        def assign(self, proc):
            if not _k32.AssignProcessToJobObject(self.handle, proc._handle):
                raise OwnedProcessError("WINDOWS_JOB_ASSIGN_FAILED")

        def terminate(self):
            if self.handle:
                _k32.TerminateJobObject(self.handle, 1)

        def close(self):
            if self.handle:
                handle, self.handle = self.handle, None
                _k32.CloseHandle(handle)


class OwnedProcess:
    def __init__(self, argv: list[str], **kwargs):
        kwargs = dict(kwargs)
        self.job = None
        self.proc = None
        self.closed = False
        if os.name == "nt":
            self.job = _WindowsJob()
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(argv, **kwargs)
            if self.job:
                self.job.assign(self.proc)
        except BaseException:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait(timeout=5)
            if self.job:
                self.job.close()
            raise

    def stop(self):
        """Terminate only this invocation's process group/job; idempotent."""
        if self.closed:
            return
        if os.name == "nt":
            self.job.terminate()
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                self.job.terminate()
            else:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                raise OwnedProcessError("OWNED_PROCESS_TREE_NOT_STOPPED")

    def close(self):
        if self.closed:
            return
        try:
            self.stop()
        finally:
            if self.job:
                self.job.close()
            if self.proc.stdout:
                self.proc.stdout.close()
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
