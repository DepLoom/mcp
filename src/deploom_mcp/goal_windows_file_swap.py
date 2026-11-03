"""Windows NTFS exact file delivery by held handles, preserving the preimage.

A caller-owned durable journal must pin prepare_swap() before apply_swap().
Two no-overwrite renames are recoverable, not an atomic multi-file operation.
No ACL/settings changes, fallback os.replace or inferred goal authority.
"""
from __future__ import annotations
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as w
import hashlib
import math
import os
from pathlib import Path
import time


class WindowsExactSwapError(RuntimeError):pass


class _Info(ctypes.Structure):
    _fields_=[('attributes',w.DWORD),('created',w.FILETIME),('accessed',w.FILETIME),
        ('written',w.FILETIME),('volume',w.DWORD),('sizeHigh',w.DWORD),('sizeLow',w.DWORD),
        ('links',w.DWORD),('indexHigh',w.DWORD),('indexLow',w.DWORD)]
class _Id(ctypes.Structure):
    _fields_=[('volume',ctypes.c_ulonglong),('fileId',ctypes.c_ubyte*16)]
class _Rename(ctypes.Structure):
    _fields_=[('flags',w.DWORD),('root',w.HANDLE),('length',w.DWORD),('name',w.WCHAR*1)]


class _API:
    def __init__(self,deadline):
        if os.name!='nt':raise WindowsExactSwapError('FINAL_SOURCE_PLATFORM_UNSUPPORTED')
        if isinstance(deadline,bool) or not isinstance(deadline,(int,float)) or not math.isfinite(deadline):
            raise WindowsExactSwapError('FINAL_SOURCE_LEASE_DEADLINE_UNPROVEN')
        self.deadline=deadline;self.budget();self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        signatures={
            'CreateFileW':([w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE],w.HANDLE),
            'CloseHandle':([w.HANDLE],w.BOOL),
            'GetFileInformationByHandle':([w.HANDLE,ctypes.POINTER(_Info)],w.BOOL),
            'GetFileInformationByHandleEx':([w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD],w.BOOL),
            'GetFinalPathNameByHandleW':([w.HANDLE,w.LPWSTR,w.DWORD,w.DWORD],w.DWORD),
            'GetLongPathNameW':([w.LPCWSTR,w.LPWSTR,w.DWORD],w.DWORD),
            'GetFileAttributesW':([w.LPCWSTR],w.DWORD),
            'ReadFile':([w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p],w.BOOL),
            'SetFilePointerEx':([w.HANDLE,ctypes.c_longlong,ctypes.c_void_p,w.DWORD],w.BOOL),
            'SetFileInformationByHandle':([w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD],w.BOOL),
            'GetVolumeInformationW':([w.LPCWSTR,w.LPWSTR,w.DWORD,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,w.LPWSTR,w.DWORD],w.BOOL)}
        for name,(args,result) in signatures.items():
            f=getattr(self.k,name);f.argtypes=args;f.restype=result
    def budget(self):
        if time.time()>=self.deadline:raise WindowsExactSwapError('FINAL_SOURCE_LEASE_DEADLINE_EXCEEDED')
    def check(self,ok):
        if not ok:
            error=ctypes.get_last_error()
            raise WindowsExactSwapError('FINAL_SOURCE_WIN32_'+str(error)) from ctypes.WinError(error)
    def close(self,h):self.k.CloseHandle(h)
    def open(self,path,directory=False,*,rename_directory=False,readonly_file=False):
        self.budget();path=Path(path).absolute()
        if len(path.drive)!=2 or path.drive[1]!=':':raise WindowsExactSwapError('FINAL_SOURCE_LOCAL_NTFS_REQUIRED')
        if type(readonly_file) is not bool or readonly_file and (directory or rename_directory):
            raise WindowsExactSwapError('FINAL_SOURCE_INVALID_READ_LEASE')
        access=0x80000000|(0x10000 if (not directory and not readonly_file) or rename_directory else 0)
        share=3 if directory else 1
        flags=0x00200000|(0x02000000 if directory else 0)
        h=self.k.CreateFileW(str(path),access,share,None,3,flags,None)
        if h==ctypes.c_void_p(-1).value:self.check(False)
        try:
            info=_Info();self.check(self.k.GetFileInformationByHandle(h,ctypes.byref(info)))
            if info.attributes&0x400 or bool(info.attributes&0x10)!=directory or (not directory and info.links!=1):
                raise WindowsExactSwapError('FINAL_SOURCE_LINK_OR_TYPE_UNSUPPORTED')
            buf=ctypes.create_unicode_buffer(32768)
            n=self.k.GetFinalPathNameByHandleW(h,buf,len(buf),0)
            if not n or n>=len(buf):self.check(False)
            actual=buf.value
            if actual.startswith('\\\\?\\'):actual=actual[4:]
            if os.path.normcase(os.path.normpath(actual))!=os.path.normcase(os.path.normpath(str(path))):
                self.verify_alias(h,path,directory,actual)
            return h
        except BaseException:self.close(h);raise
    def verify_alias(self,h,path,directory,actual):
        """Admit a spelling alias only; held-object/parent authority is unchanged.

        GetLongPathName is not an identity oracle. Keep the original handle and
        leases, reject every raw reparse component, and compare a second native
        object's identity. Normal long-path opens keep their original fast path.
        """
        self.budget()
        for component in (path,*path.parents):
            self.budget()
            attributes=self.k.GetFileAttributesW(str(component))
            if attributes==0xffffffff:self.check(False)
            if attributes&0x400:
                raise WindowsExactSwapError('FINAL_SOURCE_LINK_OR_TYPE_UNSUPPORTED')
        expanded=ctypes.create_unicode_buffer(32768)
        n=self.k.GetLongPathNameW(str(path),expanded,len(expanded))
        if not n or n>=len(expanded):self.check(False)
        if os.path.normcase(os.path.normpath(expanded.value))!=os.path.normcase(os.path.normpath(actual)):
            raise WindowsExactSwapError('FINAL_SOURCE_PATH_SUBSTITUTION')
        flags=0x00200000|(0x02000000 if directory else 0)
        # Share-all on this READ-ONLY observation does not relax the first
        # handle's existing deny-write/delete lease; that handle remains open.
        peer=self.k.CreateFileW(expanded.value,0x80000000,7,None,3,flags,None)
        if peer==ctypes.c_void_p(-1).value:self.check(False)
        try:
            info=_Info();self.check(self.k.GetFileInformationByHandle(peer,ctypes.byref(info)))
            if info.attributes&0x400 or bool(info.attributes&0x10)!=directory or (not directory and info.links!=1):
                raise WindowsExactSwapError('FINAL_SOURCE_LINK_OR_TYPE_UNSUPPORTED')
            if self.identity(h)!=self.identity(peer):
                raise WindowsExactSwapError('FINAL_SOURCE_PATH_SUBSTITUTION')
            self.budget()
        finally:self.close(peer)

    def identity(self,h):
        value=_Id();self.check(self.k.GetFileInformationByHandleEx(h,18,ctypes.byref(value),ctypes.sizeof(value)))
        return {'volumeSerial':str(value.volume),'fileId':bytes(value.fileId).hex()}
    def file(self,h):
        self.check(self.k.SetFilePointerEx(h,0,None,0));buf=ctypes.create_string_buffer(65536)
        digest=hashlib.sha256();size=0;n=w.DWORD()
        while True:
            self.budget();self.check(self.k.ReadFile(h,buf,len(buf),ctypes.byref(n),None))
            if not n.value:break
            size+=n.value
            if size>8*1024*1024:raise WindowsExactSwapError('FINAL_SOURCE_FILE_SIZE_UNSUPPORTED')
            digest.update(buf.raw[:n.value])
        return {**self.identity(h),'sha256':digest.hexdigest(),'size':size}
    def ntfs(self,path):
        name=ctypes.create_unicode_buffer(64)
        self.check(self.k.GetVolumeInformationW(Path(path).anchor,None,0,None,None,None,name,len(name)))
        if name.value.upper()!='NTFS':raise WindowsExactSwapError('FINAL_SOURCE_LOCAL_NTFS_REQUIRED')
    def rename(self,h,path):
        self.budget();raw=str(Path(path).absolute()).encode('utf-16-le')
        buf=ctypes.create_string_buffer(_Rename.name.offset+len(raw)+2)
        info=ctypes.cast(buf,ctypes.POINTER(_Rename)).contents
        info.flags=0;info.root=None;info.length=len(raw)
        ctypes.memmove(ctypes.addressof(buf)+_Rename.name.offset,raw,len(raw))
        self.check(self.k.SetFileInformationByHandle(h,3,buf,len(buf)))


@contextmanager
def protected_parents(api,paths,expected=None):
    """Hold every ancestor identity, including registered-root ancestors."""
    directories=set()
    for path in paths:
        parent=Path(path).absolute().parent
        directories.add(parent);directories.update(parent.parents)
    handles=[];observed={}
    try:
        for path in sorted(directories,key=lambda p:(len(p.parts),str(p).casefold())):
            h=api.open(path,True);handles.append(h);observed[str(path)]=api.identity(h)
        if expected is not None and observed!=expected:
            raise WindowsExactSwapError('FINAL_SOURCE_PARENT_IDENTITY_CHANGED')
        yield observed
    finally:
        for h in reversed(handles):api.close(h)


def prepare_swap(target,staged,backup,*,before_sha256,after_sha256,deadline):
    """Prepare actual object identities. Caller persists this exact plan first."""
    api=_API(deadline);paths=[str(Path(p).absolute()) for p in (target,staged,backup)]
    if len(set(map(os.path.normcase,paths)))!=3:raise WindowsExactSwapError('FINAL_SOURCE_SWAP_PATH_COLLISION')
    with protected_parents(api,paths) as parents:
        for path in paths:api.ntfs(path)
        if Path(paths[2]).exists():raise WindowsExactSwapError('FINAL_SOURCE_BACKUP_ALREADY_EXISTS')
        old=api.open(paths[0]);new=None
        try:
            new=api.open(paths[1]);before=api.file(old);after=api.file(new)
            if (before['sha256']!=before_sha256 or after['sha256']!=after_sha256
                    or before['volumeSerial']!=after['volumeSerial']):
                raise WindowsExactSwapError('FINAL_SOURCE_SWAP_IMAGE_MISMATCH')
            return {'schemaVersion':1,'deadline':deadline,'target':paths[0],'staged':paths[1],'backup':paths[2],
                'parents':parents,'before':before,'after':after}
        finally:
            if new is not None:api.close(new)
            api.close(old)


def observe_swap(plan,*,deadline,completed_read=False):
    """Read-only classification. Completed checkpoint reads have their own cap."""
    if type(completed_read) is not bool:raise WindowsExactSwapError('FINAL_INVALID_READ_MODE')
    api=_API(deadline if completed_read else min(deadline,plan['deadline']));handles=[]
    target,staged,backup=[Path(plan[k]) for k in ('target','staged','backup')]
    def checked(path,wanted):
        h=api.open(path);handles.append(h)
        if api.file(h)!=wanted:raise WindowsExactSwapError('FINAL_SOURCE_FOREIGN_OBJECT_CONFLICT')
    try:
        with protected_parents(api,[target,staged,backup],plan['parents']):
            if backup.exists():
                checked(backup,plan['before'])
                if target.exists():
                    checked(target,plan['after'])
                    if staged.exists():raise WindowsExactSwapError('FINAL_SOURCE_STAGE_OBJECT_CONFLICT')
                    return 'DELIVERED'
                checked(staged,plan['after'])
                return 'OLD_MOVED_TO_OWNED_BACKUP'
            checked(target,plan['before']);checked(staged,plan['after'])
            return 'PREIMAGE'
    finally:
        for h in reversed(handles):api.close(h)


def apply_swap(plan,*,deadline,cutpoint=None):
    """Apply/recover only pinned object IDs; same-hash foreign files conflict."""
    api=_API(min(deadline,plan['deadline']));target,staged,backup=[Path(plan[k]) for k in ('target','staged','backup')]
    if type(plan.get('schemaVersion')) is not int or plan['schemaVersion']!=1:
        raise WindowsExactSwapError('FINAL_SOURCE_SWAP_PLAN_UNPROVEN')
    handles=[]
    def open_checked(path,wanted):
        h=api.open(path);handles.append(h)
        if api.file(h)!=wanted:raise WindowsExactSwapError('FINAL_SOURCE_FOREIGN_OBJECT_CONFLICT')
        return h
    try:
        with protected_parents(api,[target,staged,backup],plan['parents']):
            if backup.exists():
                old=open_checked(backup,plan['before'])
                if target.exists():
                    open_checked(target,plan['after'])
                    if staged.exists():raise WindowsExactSwapError('FINAL_SOURCE_STAGE_OBJECT_CONFLICT')
                    return {'status':'DELIVERED','before':plan['before'],'after':plan['after'],'mutated':False}
                if not staged.exists():raise WindowsExactSwapError('FINAL_SOURCE_RECOVERY_UNPROVEN')
                new=open_checked(staged,plan['after'])
            else:
                if not target.exists() or not staged.exists():raise WindowsExactSwapError('FINAL_SOURCE_RECOVERY_UNPROVEN')
                old=open_checked(target,plan['before']);new=open_checked(staged,plan['after'])
                api.rename(old,backup)
                if cutpoint:cutpoint('OLD_MOVED_TO_OWNED_BACKUP')
            api.rename(new,target)
            if cutpoint:cutpoint('POSTIMAGE_PLACED')
            if api.file(old)!=plan['before'] or api.file(new)!=plan['after']:
                raise WindowsExactSwapError('FINAL_SOURCE_SWAP_IDENTITY_CHANGED')
            return {'status':'DELIVERED','before':plan['before'],'after':plan['after'],'mutated':True}
    finally:
        for h in reversed(handles):api.close(h)
