"""Preserve/switch ordinary generated dependency trees; no recursive deletion.

Directory object IDs plus complete Core payload manifests bind the boundaries.
Child bytes are boundary-checked, not continuously write-locked or authenticated.
The final host owner must persist this plan before any registered-root mutation.
"""
from pathlib import Path
import time
from deploom_mcp.goal_contract import canonical_digest
from deploom_mcp.goal_delta_trial import _core
from deploom_mcp.goal_windows_file_swap import _API,protected_parents,WindowsExactSwapError


def tree_digest(path,*,deadline,_budget=None):
    core=_core();remaining=deadline-time.time()
    if remaining<=0:raise WindowsExactSwapError('FINAL_TREE_DEADLINE_EXCEEDED')
    policy=core.SourceInputPolicy(excluded_dir_names=(),excluded_file_names=())
    timeout=max(1,int(remaining)) if _budget is None else min(remaining,_budget.remaining())
    exact={'exact_timeout':True} if _budget is not None else {}
    manifest=core.build_source_tree_manifest(Path(path),policy=policy,timeout_seconds=timeout,**exact)
    if _budget is not None:_budget.remaining()
    return canonical_digest({'entries':list(manifest.entries),'directories':manifest.directory_count})


def _observation(api,handle,path):
    budget=getattr(api,'_proof_budget',None)
    args={'_budget':budget} if budget is not None else {}
    return {**api.identity(handle),'payloadDigest':tree_digest(path,deadline=api.deadline,**args)}


def prepare_tree_swap(target,staged,backup,*,deadline):
    api=_API(deadline);paths=[str(Path(p).absolute()) for p in (target,staged,backup)]
    if len(set(paths))!=3:raise WindowsExactSwapError('FINAL_TREE_PATH_COLLISION')
    with protected_parents(api,paths) as parents:
        for path in paths:api.ntfs(path)
        if Path(backup).exists():raise WindowsExactSwapError('FINAL_TREE_BACKUP_EXISTS')
        handles=[]
        try:
            after_handle=api.open(staged,True,rename_directory=True);handles.append(after_handle)
            after=_observation(api,after_handle,staged);before=None
            if Path(target).exists():
                h=api.open(target,True,rename_directory=True);handles.append(h)
                before=_observation(api,h,target)
            target_volume=parents[str(Path(target).absolute().parent)]['volumeSerial']
            if after['volumeSerial']!=target_volume or (before and before['volumeSerial']!=target_volume):
                raise WindowsExactSwapError('FINAL_TREE_CROSS_VOLUME_UNSUPPORTED')
            return {'schemaVersion':1,'deadline':deadline,'target':paths[0],'staged':paths[1],
                'backup':paths[2],'parents':parents,'before':before,'after':after}
        finally:
            for h in reversed(handles):api.close(h)


def apply_tree_swap(plan,*,deadline,cutpoint=None):
    api=_API(min(deadline,plan['deadline']));target,staged,backup=[Path(plan[k]) for k in ('target','staged','backup')]
    handles=[]
    def checked(path,expected):
        h=api.open(path,True,rename_directory=True);handles.append(h)
        if _observation(api,h,path)!=expected:raise WindowsExactSwapError('FINAL_TREE_FOREIGN_OBJECT_OR_BYTES')
        return h
    try:
        with protected_parents(api,[target,staged,backup],plan['parents']):
            old=None
            if plan['before'] is None:
                if backup.exists():raise WindowsExactSwapError('FINAL_TREE_UNEXPECTED_BACKUP')
                if target.exists():
                    checked(target,plan['after'])
                    if staged.exists():raise WindowsExactSwapError('FINAL_TREE_STAGE_CONFLICT')
                    return {'status':'DELIVERED','mutated':False}
            elif backup.exists():
                old=checked(backup,plan['before'])
                if target.exists():
                    checked(target,plan['after'])
                    if staged.exists():raise WindowsExactSwapError('FINAL_TREE_STAGE_CONFLICT')
                    return {'status':'DELIVERED','mutated':False}
            else:
                old=checked(target,plan['before'])
            new=checked(staged,plan['after'])
            if old is not None and not backup.exists():
                api.rename(old,backup)
                if cutpoint:cutpoint('OLD_TREE_PRESERVED')
            api.rename(new,target)
            if cutpoint:cutpoint('NEW_TREE_PLACED')
            if _observation(api,new,target)!=plan['after'] or (old is not None and _observation(api,old,backup)!=plan['before']):
                raise WindowsExactSwapError('FINAL_TREE_BOUNDARY_CHANGED')
            return {'status':'DELIVERED','mutated':True}
    finally:
        for h in reversed(handles):api.close(h)


def observe_delivered_tree(plan,*,deadline,_budget=None):
    """Completed read only: never invokes a relocation or repeats installation."""
    api=_API(deadline);target,staged,backup=[Path(plan[k]) for k in ('target','staged','backup')];handles=[]
    if _budget is not None:api._proof_budget=_budget
    try:
        with protected_parents(api,[target,staged,backup],plan['parents']):
            if staged.exists():raise WindowsExactSwapError('FINAL_TREE_STAGE_CONFLICT')
            current=api.open(target,True,rename_directory=True);handles.append(current)
            if _observation(api,current,target)!=plan['after']:raise WindowsExactSwapError('FINAL_TREE_FOREIGN_OBJECT_OR_BYTES')
            if plan['before'] is not None:
                old=api.open(backup,True,rename_directory=True);handles.append(old)
                if _observation(api,old,backup)!=plan['before']:raise WindowsExactSwapError('FINAL_TREE_BACKUP_CHANGED')
            elif backup.exists():raise WindowsExactSwapError('FINAL_TREE_UNEXPECTED_BACKUP')
            return True
    finally:
        for h in reversed(handles):api.close(h)
