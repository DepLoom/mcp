"""V2 output capture: stable container, resettable data child, physical manifest.

Only after the supervising owner has observed zero descendants. Handles stay
held through the caller's seal and a second physical walk. This does not attest
per-file writer provenance or arbitrary hostile concurrent filesystem closure.
"""
from contextlib import contextmanager
from pathlib import Path
import os
from deploom_mcp.goal_windows_file_swap import _API


def _fail():raise RuntimeError('TAP_NYC_OUTPUT_CAPTURE_INCOMPLETE')


@contextmanager
def held_output_receipt(outputs,*,deadline):
    api=_API(deadline);handles=[];roots={k:Path(p) for k,p in outputs.items()}
    try:
        if set(roots)!={'temp','report','cache'}:_fail()
        # The only disposable path is this fixed child. No siblings are allowed.
        if sorted(p.name for p in roots['temp'].iterdir())!=['data']:_fail()
        held={};count=0;total=0
        def walk(path,relative):
            nonlocal count,total
            api.budget();count+=1
            if count>4096:_fail()
            directory=path.is_dir()
            h=api.open(path,directory,readonly_file=not directory);handles.append(h)
            row={'relativePath':relative,'type':'directory' if directory else 'file','identity':api.identity(h)}
            if not directory:
                f=api.file(h);total+=f['size']
                if total>64*1024*1024:_fail()
                row.update(size=f['size'],sha256=f['sha256'])
            held[(str(path),relative)]=(h,row)
            rows=[row]
            if directory:
                for child in sorted(path.iterdir(),key=lambda p:p.name):rows+=walk(child,relative+'/'+child.name if relative else child.name)
            return rows
        manifests={k:walk(p,'') for k,p in roots.items()}
        if manifests['temp'][1]['type']!='directory' or manifests['temp'][1]['relativePath']!='data':_fail()
        def recheck():
            def check(path,relative):
                api.budget();entry=held.get((str(path),relative))
                if entry is None:_fail()
                h,row=entry;directory=row['type']=='directory'
                # Reopen path no-follow to catch substitutions, then compare the
                # original held handle. New equal-byte objects are not current.
                fresh=api.open(path,directory,readonly_file=not directory)
                try:
                    if api.identity(fresh)!=row['identity'] or api.identity(h)!=row['identity']:_fail()
                    if not directory and (api.file(h)!=api.file(fresh) or api.file(h)!={**row['identity'],'size':row['size'],'sha256':row['sha256']}):_fail()
                finally:api.close(fresh)
                found=[relative]
                if directory:
                    for child in sorted(path.iterdir(),key=lambda p:p.name):found+=check(child,relative+'/'+child.name if relative else child.name)
                return found
            for key,path in roots.items():
                if check(path,'')!=[r['relativePath'] for r in manifests[key]]:_fail()
        recheck()
        yield {'schemaVersion':2,'lifecycle':'STABLE_CONTAINER_RESETTABLE_DATA_CHILD_V2','manifests':manifests}
        recheck()
    finally:
        for h in reversed(handles):api.close(h)
