"""Handle-bound reads for the read-only npm workspace inventory."""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import os
from pathlib import Path
import stat
from deploom_mcp.goal_contract import GoalContractError


def _plain(info, directory):
    return (not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400
            and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)))


def _same_object(left, right):
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino and left.st_ino != 0


@contextmanager
def _windows_fd(path, directory):
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW
    create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    create.restype=wintypes.HANDLE
    close=kernel.CloseHandle;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
    final=kernel.GetFinalPathNameByHandleW
    final.argtypes=[wintypes.HANDLE,wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD];final.restype=wintypes.DWORD
    before=path.lstat()
    if not _plain(before,directory):raise GoalContractError('WORKSPACE_UNSAFE_INPUT')
    flags=0x00200000 | (0x02000000 if directory else 0)  # OPEN_REPARSE_POINT, BACKUP_SEMANTICS
    handle=create(str(path),0x80000000,1,None,3,flags,None)
    if handle == ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    fd=None
    try:
        buffer=ctypes.create_unicode_buffer(32768)
        length=final(handle,buffer,len(buffer),0)
        if not length or length >= len(buffer):raise GoalContractError('WORKSPACE_UNSAFE_INPUT')
        actual=buffer.value
        if actual.startswith('\\\\?\\UNC\\'):actual='\\\\'+actual[8:]
        elif actual.startswith('\\\\?\\'):actual=actual[4:]
        if os.path.normcase(os.path.normpath(actual)) != os.path.normcase(os.path.normpath(str(path))):
            raise GoalContractError('WORKSPACE_UNSAFE_INPUT')
        fd=msvcrt.open_osfhandle(handle,os.O_RDONLY | os.O_BINARY)
        handle=None
        opened=os.fstat(fd)
        if not _plain(opened,directory) or not _same_object(before,opened):
            raise GoalContractError('WORKSPACE_INPUT_CHANGED')
        yield fd
    finally:
        if fd is not None:os.close(fd)
        if handle is not None:close(handle)


@dataclass(frozen=True)
class DirectoryBinding:
    path: Path
    fd: int


@contextmanager
def bound_directory(root: Path, directory: Path, root_fd=None):
    """Keep the selected namespace bound while opening or enumerating a child."""
    relative=directory.relative_to(root)
    with ExitStack() as held:
        if os.name=='nt':
            current=root
            fd=root_fd if root_fd is not None else held.enter_context(_windows_fd(current,True))
            for part in relative.parts:
                current=current/part
                fd=held.enter_context(_windows_fd(current,True))
            yield DirectoryBinding(directory,fd)
        else:
            if not hasattr(os,'O_NOFOLLOW') or os.open not in os.supports_dir_fd or os.scandir not in os.supports_fd:
                raise GoalContractError('WORKSPACE_HANDLE_IO_UNSUPPORTED')
            flags=os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd=os.dup(root_fd) if root_fd is not None else os.open(root,flags)
            held.callback(os.close,fd)
            for part in relative.parts:
                fd=os.open(part,flags,dir_fd=fd);held.callback(os.close,fd)
            yield DirectoryBinding(directory,fd)


@contextmanager
def selected_root(project: Path, expected):
    # Walk from the filesystem anchor without resolving input-controlled links.
    # Keep the complete ancestor chain and selected root handles until observation ends.
    with bound_directory(Path(project.anchor),project) as binding:
        if not _same_object(expected,os.fstat(binding.fd)):
            raise GoalContractError('WORKSPACE_INPUT_CHANGED')
        yield binding.fd


@contextmanager
def open_bound_file(path: Path, parent):
    if os.name=='nt':
        with _windows_fd(path,False) as fd:
            with os.fdopen(os.dup(fd),'rb') as stream:yield stream
    else:
        fd=os.open(path.name,os.O_RDONLY | os.O_NOFOLLOW,dir_fd=parent.fd)
        with os.fdopen(fd,'rb') as stream:yield stream
