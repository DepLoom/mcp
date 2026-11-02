"""Read-only payload check used by wheel smoke tests, never an installer.

The trusted input is a caller-pinned wheel and a known installed site directory.
This compares package bytes, not signatures, dependency closure or live project
correctness. Installed RECORD is not an authority. No interpreter is launched
until every declared package file passes the local checks.
"""
from __future__ import annotations
import hashlib,json,os,stat,subprocess,zipfile
from pathlib import Path,PurePosixPath


def _ordinary(path: Path, *, directory: bool = False) -> None:
    try:
        info=path.lstat()
    except OSError as exc:
        raise RuntimeError('RUNTIME_PACKAGE_MISSING') from exc
    if (stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400
            or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))):
        raise RuntimeError('RUNTIME_PACKAGE_LINK_OR_TYPE')


def verify_runtime(python: Path,wheel: Path,site: Path,*,cwd=None,env=None) -> dict:
    """Verify ordinary package payload before read-only pip dependency checks.

    ``site`` is required: discovering it via an unverified Python would violate
    this helper's pre-subprocess boundary. No installation or repair is done.
    """
    python,wheel,site=Path(python).absolute(),Path(wheel).absolute(),Path(site).absolute()
    _ordinary(site,directory=True)
    payload=[];names=set();total=0
    with zipfile.ZipFile(wheel) as archive:
        if len(archive.infolist())>4096:raise RuntimeError('RUNTIME_WHEEL_LIMIT')
        for row in archive.infolist():
            name=row.filename;parts=PurePosixPath(name).parts
            if (not name or '\\' in name or name.startswith('/') or ':' in name
                    or any(p in {'.','..',''} for p in name.rstrip('/').split('/'))):
                raise RuntimeError('RUNTIME_WHEEL_UNSAFE_PATH')
            identity=name.rstrip('/').casefold()
            if identity in names:raise RuntimeError('RUNTIME_WHEEL_DUPLICATE_PATH')
            names.add(identity)
            if stat.S_ISLNK(row.external_attr>>16):raise RuntimeError('RUNTIME_WHEEL_LINK')
            total+=row.file_size
            if row.file_size>32*1024*1024 or total>256*1024*1024:
                raise RuntimeError('RUNTIME_WHEEL_LIMIT')
            if row.is_dir() or not name.startswith('deploom_mcp/'):continue
            path=site.joinpath(*parts)
            for parent in [site,*[site.joinpath(*parts[:i]) for i in range(1,len(parts))]]:
                _ordinary(parent,directory=True)
            _ordinary(path)
            if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(archive.read(row)).digest():
                raise RuntimeError('RUNTIME_PACKAGE_MISMATCH')
            payload.append(name)
    if not payload:raise RuntimeError('RUNTIME_WHEEL_PACKAGE_MISSING')
    result=subprocess.run([str(python),'-I','-m','pip','check'],cwd=cwd,env=env,
        capture_output=True,text=True,timeout=30,check=False)
    if result.returncode:raise RuntimeError('RUNTIME_DEPENDENCIES_INVALID')
    inventory=json.loads(subprocess.check_output([str(python),'-I','-m','pip','list',
        '--format=json','--disable-pip-version-check'],cwd=cwd,env=env,text=True,timeout=30))
    if not isinstance(inventory,list):raise RuntimeError('RUNTIME_DEPENDENCY_INVENTORY_INVALID')
    return {'verifiedPackageFiles':len(payload),'installationPerformed':False,
        'dependencyCount':len(inventory),'scope':'WHEEL_DECLARED_PACKAGE_BYTES',
        'signatureAuthenticity':'NOT_ATTESTED','extraInstalledFiles':'NOT_ATTESTED'}
