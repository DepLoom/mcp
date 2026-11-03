"""Host-pinned, receipt-observed empty auxiliary preparation before payload seal.

This changes filesystem layout, not package identity. Final commands verify their
own actual result; private success never proves arbitrary filesystem equivalence.
"""
import os,re,stat
from pathlib import Path,PurePosixPath
from deploom_mcp.goal_contract import GoalContractError,canonical_digest

MODE='PRESEAL_OBSERVED_EMPTY_AUX_V1'

def policy_paths(policy):
    value=policy.get('finalAuxiliaryPreparation')
    if value is None:return ()
    if (not isinstance(value,dict) or set(value)!={'schemaVersion','mode','paths'}
            or type(value['schemaVersion']) is not int or value['schemaVersion']!=1
            or value['mode']!=MODE or not isinstance(value['paths'],list)
            or not 1<=len(value['paths'])<=16):
        raise GoalContractError('FINAL_AUXILIARY_POLICY_INVALID')
    paths=value['paths']
    for text in paths:
        if not isinstance(text,str) or len(text)>240 or '\\' in text or ':' in text:
            raise GoalContractError('FINAL_AUXILIARY_PATH_INVALID')
        parts=PurePosixPath(text).parts
        if (not 2<=len(parts)<=32 or parts[0]!='node_modules' or parts[-2]!='node_modules'
                or PurePosixPath(text).as_posix()!=text or any(p in {'.','..'} for p in parts)
                or not re.fullmatch(r'\.[A-Za-z0-9][A-Za-z0-9._-]{0,78}',parts[-1])
                or parts[-1].endswith('.') or parts[-1].casefold() in {'.bin','.package-lock.json'}):
            raise GoalContractError('FINAL_AUXILIARY_PATH_INVALID')
    if paths!=sorted(paths) or len({p.casefold() for p in paths})!=len(paths):
        raise GoalContractError('FINAL_AUXILIARY_PATH_COLLISION')
    return tuple(paths)


def observed_paths(raw):
    phases=raw.get('auxiliaryDirectoryObservations')
    if not isinstance(phases,list):raise GoalContractError('FINAL_AUXILIARY_OBSERVATION_MISSING')
    paths=set()
    for phase in phases:
        if not isinstance(phase,dict) or not isinstance(phase.get('directories'),list):
            raise GoalContractError('FINAL_AUXILIARY_OBSERVATION_INVALID')
        for row in phase['directories']:
            if (not isinstance(row,dict) or row.get('classification')!='UNSCOPED_NONPACKAGE_EMPTY_DIRECTORY'
                    or row.get('emptyObserved') is not True or row.get('linkFreeObserved') is not True
                    or not isinstance(row.get('relativePath'),str)):
                raise GoalContractError('FINAL_AUXILIARY_OBSERVATION_INVALID')
            paths.add(row['relativePath'])
    return paths


def _ordinary(path):
    s=path.lstat()
    if not stat.S_ISDIR(s.st_mode) or stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400:
        raise GoalContractError('FINAL_AUXILIARY_LINK_OR_TYPE_UNPROVEN')
    return (s.st_dev,s.st_ino,s.st_mode,s.st_mtime_ns,s.st_ctime_ns)


def validate_layout(project,paths,plan,*,create=False):
    """Only leaf mkdir; all package/ancestor directories must already exist."""
    paths=policy_paths({'finalAuxiliaryPreparation':{'schemaVersion':1,'mode':MODE,'paths':list(paths)}})
    project=Path(project).absolute();_ordinary(project)
    locked={r['path'].casefold() for r in plan['instances']};out=[]
    for text in paths:
        parts=PurePosixPath(text).parts
        owner='/'.join(parts[:-2])
        if text.casefold() in locked or (owner and owner.casefold() not in locked):
            raise GoalContractError('FINAL_AUXILIARY_PACKAGE_SLOT_CONFLICT')
        parent=project
        for segment in parts[:-1]:
            parent=parent/segment;_ordinary(parent)
            if not parent.resolve().is_relative_to(project.resolve()):
                raise GoalContractError('FINAL_AUXILIARY_CONTAINMENT_UNPROVEN')
        target=parent/parts[-1];existed=os.path.lexists(target)
        if not existed and create:target.mkdir()  # no parents, no deletion or replacement
        first=_ordinary(target)
        if any(target.iterdir()):raise GoalContractError('FINAL_AUXILIARY_NOT_EMPTY')
        if first!=_ordinary(target) or target.resolve().parent!=parent.resolve():
            raise GoalContractError('FINAL_AUXILIARY_LAYOUT_CHANGED')
        out.append({'relativePath':text,'existedBeforePreparation':existed,'emptyObserved':True,'linkFreeObserved':True})
    return out


def prepare_observed_auxiliary(tx,project,plan):
    paths=policy_paths(tx.registration.policy)
    if not paths:return None
    from deploom_mcp.goal_frozen_candidate import frozen_profile_complete
    receipt=tx.context.accepted_receipt
    if not frozen_profile_complete(receipt,tx.registration):
        raise GoalContractError('FINAL_AUXILIARY_FRESH_RECEIPT_UNPROVEN')
    raw=receipt['verificationObservation']['coreWorkspaceObservation']
    if not set(paths).issubset(observed_paths(raw)):
        raise GoalContractError('FINAL_AUXILIARY_PATH_NOT_OBSERVED')
    if (Path(project).absolute()!=tx.directory/'dependency-stage/repo'
            or canonical_digest(tx.registration.policy)!=tx.registration.goal['policyDigest']):
        raise GoalContractError('FINAL_AUXILIARY_OWNERSHIP_UNPROVEN')
    tx.remaining()
    rows=validate_layout(project,paths,plan,create=True)
    return {'mode':MODE,'paths':list(paths),'observations':rows,
        'acceptedReceiptDigest':canonical_digest(receipt),'policyDigest':canonical_digest(tx.registration.policy),
        'obligationPlanDigest':plan['obligationPlanDigest'],'mutationPhase':'BEFORE_FIRST_PAYLOAD_SEAL',
        'arbitraryFilesystemSemanticsVerified':False}


def validate_live_auxiliary(tx,plan,raw):
    paths=policy_paths(tx.registration.policy)
    if not paths:return
    state=tx.record['dependencyStage'].get('auxiliaryPreparation')
    if (not isinstance(state,dict) or state.get('mode')!=MODE or state.get('paths')!=list(paths)
            or state.get('acceptedReceiptDigest')!=canonical_digest(tx.context.accepted_receipt)
            or state.get('policyDigest')!=canonical_digest(tx.registration.policy)
            or state.get('obligationPlanDigest')!=plan['obligationPlanDigest']):
        raise GoalContractError('FINAL_AUXILIARY_PREPARATION_UNPROVEN')
    validate_layout(tx.project,paths,plan)
    if observed_paths(raw)!=set(paths):raise GoalContractError('FINAL_AUXILIARY_UNEXPECTED_LIVE_SET')
