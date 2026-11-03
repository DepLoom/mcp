"""Read terminal owner records without renewing execution or claiming current state."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import hashlib, json, math, os
from deploom_mcp.goal_contract import GoalContractError, canonical_digest, validate_goal, assert_current_binding
from deploom_mcp.goal_delta_trial import _json
from deploom_mcp.goal_report import _plain_under, _RUN, _SHA
from deploom_mcp.goal_execution_registry import configured_state_root, OPERATION
from deploom_mcp.goal_final_authorization import final_workspace_profile, FINAL_OPERATION
from deploom_mcp.goal_historical_lineage import validate_recorded_lineage


def _bytes(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()


def _read(path,root,limit=8*1024*1024):
    path=_plain_under(path,root)
    if path.stat().st_size>limit:raise GoalContractError('HISTORICAL_ARTIFACT_TOO_LARGE')
    return _json(path.read_bytes(),limit)


@dataclass(frozen=True)
class HistoricalReadContext:
    """Immutable stored bytes only. Never an opaque execution capability."""
    entry_bytes: bytes
    registry_digest: str
    receipt_bytes: bytes
    profile_bytes: bytes
    authorization_digest: str
    attempt_id: str
    budget_seconds: int

    @property
    def registration(self):
        entry=json.loads(self.entry_bytes)
        return SimpleNamespace(run_id=entry['runId'],candidate_id=entry['candidateId'],
            project_path=entry['projectPath'],goal=entry['goal'],binding=entry['binding'],
            profile=entry['profile'],policy=entry['policy'],
            delta_authorization=entry['deltaAuthorization'],candidate_files=entry['candidateFiles'],
            final_authorization=entry['finalAuthorization'],manifest_sha256=self.registry_digest,
            deadline=datetime.fromisoformat(entry['deadlineUTC']).timestamp())

    @property
    def final_profile(self):return json.loads(self.profile_bytes)

    @property
    def accepted_receipt(self):return json.loads(self.receipt_bytes)


def _entry(root,run_id,candidate_id,binding_digest):
    if (not all(isinstance(v,str) and _RUN.fullmatch(v) for v in (run_id,candidate_id))
            or not isinstance(binding_digest,str) or not _SHA.fullmatch(binding_digest)):
        raise GoalContractError('INVALID_EXECUTION_REQUEST')
    if _plain_under(root/'goal-runs'/(run_id+'.json'),root).exists():
        raise GoalContractError('IMPORTED_RUN_NOT_EXECUTABLE')
    location=os.environ.get('DEPLOOM_MCP_EXECUTION_MANIFEST','')
    expected=os.environ.get('DEPLOOM_MCP_EXECUTION_MANIFEST_SHA256','')
    if not location or not _SHA.fullmatch(expected):raise GoalContractError('EXECUTION_REGISTRY_NOT_CONFIGURED')
    if not Path(location).is_absolute():raise GoalContractError('INVALID_EXECUTION_REGISTRY_PATH')
    path=_plain_under(Path(location),root)
    if path.stat().st_size>1024*1024:raise GoalContractError('EXECUTION_REGISTRY_TOO_LARGE')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:raise GoalContractError('EXECUTION_REGISTRY_CHANGED')
    manifest=_json(raw,1024*1024)
    if (set(manifest)!={'schemaVersion','entries'} or type(manifest['schemaVersion']) is not int
            or manifest['schemaVersion']!=1 or not isinstance(manifest['entries'],list) or len(manifest['entries'])>64):
        raise GoalContractError('INVALID_EXECUTION_REGISTRY')
    matches=[e for e in manifest['entries'] if isinstance(e,dict) and e.get('runId')==run_id and e.get('candidateId')==candidate_id]
    if len(matches)!=1:raise GoalContractError('EXECUTION_CANDIDATE_NOT_REGISTERED')
    e=matches[0]
    fields={'runId','candidateId','projectPath','goal','binding','profile','policy','authorization','status','deadlineUTC',
            'deltaAuthorization','candidateFiles','finalAuthorization'}
    if set(e)!=fields or e['status'] not in {'OPEN','CLOSED'}:
        raise GoalContractError('HISTORICAL_EVIDENCE_INSUFFICIENT')
    goal=validate_goal(e['goal']);binding=e['binding'];profile=e['profile'];policy=e['policy']
    if goal['goalKind']!='functional-maintenance':raise GoalContractError('HISTORICAL_EVIDENCE_INSUFFICIENT')
    assert_current_binding(binding,goal,binding.get('identity',{}))
    if binding['bindingDigest']!=binding_digest:raise GoalContractError('STALE_OR_INVALID_GOAL_BINDING')
    if (not isinstance(policy,dict) or canonical_digest(policy)!=goal['policyDigest']
            or policy.get('hardPolicyRequired') is not False):raise GoalContractError('POLICY_BINDING_MISMATCH')
    fields={'schemaVersion','commands','requiredChecks','projectChecks','verificationPurpose','checkBindings','admission'}
    if (not isinstance(profile,dict) or set(profile)!=fields or type(profile['schemaVersion']) is not int
            or profile['schemaVersion']!=2 or profile['projectChecks']!='strict'
            or profile['verificationPurpose']!='intermediate-candidate'
            or not isinstance(profile['requiredChecks'],list) or any(not isinstance(v,str) for v in profile['requiredChecks'])
            or sorted(profile['requiredChecks'])!=sorted(goal['requiredChecks'])
            or not isinstance(profile['commands'],list) or not 1<=len(profile['commands'])<=64
            or any(not isinstance(v,str) or not v.strip() or len(v)>4096 for v in profile['commands'])):
        raise GoalContractError('INVALID_EXECUTION_PROFILE')
    delta=e['deltaAuthorization']
    fields={'schemaVersion','operation','origin','candidateDigest','deltaDigest','bindingDigest','profileDigest',
            'sourceSnapshotKey','transitiveScope','stateRootDigest'}
    if (not isinstance(delta,dict) or set(delta)!=fields or type(delta['schemaVersion']) is not int
            or delta['schemaVersion']!=1 or delta['operation']!='apply-private-candidate'
            or delta['origin']!='PREAPPROVED_HOST_POLICY'
            or delta['transitiveScope'] not in {'ROOT_TARGETS_ONLY','EXACT_HOST_ENUMERATED'}
            or any(not isinstance(delta[k],str) or not _SHA.fullmatch(delta[k])
                   for k in ('candidateDigest','deltaDigest','bindingDigest','profileDigest','stateRootDigest'))
            or delta['bindingDigest']!=binding_digest or delta['profileDigest']!=canonical_digest(profile)
            or delta['stateRootDigest']!=canonical_digest({'hostStateRoot':str(root.resolve())})
            or not isinstance(delta['sourceSnapshotKey'],str) or not 1<=len(delta['sourceSnapshotKey'])<=128):
        raise GoalContractError('INVALID_DELTA_AUTHORIZATION')
    files=e['candidateFiles'];source_paths={row['path'] for row in goal.get('sourceRepairAllowlist',[])}
    if (not isinstance(files,dict) or not {'manifest','lock'}.issubset(files)
            or set(files)-{'manifest','lock','sources'} or not isinstance(files.get('sources',{}),dict)
            or set(files.get('sources',{}))!=source_paths):
        raise GoalContractError('INVALID_HOST_CANDIDATE_FILES')
    for value in [files['manifest'],files['lock'],*files.get('sources',{}).values()]:
        if not isinstance(value,str) or not value or len(value)>512 or Path(value).is_absolute() or '..' in Path(value).parts:
            raise GoalContractError('INVALID_HOST_CANDIDATE_FILES')
    from deploom_mcp.goal_check_bindings import validate_check_bindings,validate_verification_context
    validate_check_bindings(profile,goal['requiredChecks']);validate_verification_context(goal,profile,binding['identity'])
    if e['authorization']!={'operation':OPERATION,'bindingDigest':binding['bindingDigest'],
            'profileDigest':canonical_digest(profile),'targetMapDigest':canonical_digest(goal['exactTargets'])}:
        raise GoalContractError('EXECUTION_AUTHORIZATION_BINDING_MISMATCH')
    if not isinstance(e['projectPath'],str) or not Path(e['projectPath']).is_absolute():
        raise GoalContractError('INVALID_PROJECT_PATH')
    deadline=datetime.fromisoformat(e['deadlineUTC'])
    if deadline.tzinfo is None:raise GoalContractError('HISTORICAL_TIME_UNPROVEN')
    cap=e['finalAuthorization']
    if (not isinstance(cap,dict) or type(cap.get('schemaVersion')) is not int or cap['schemaVersion']!=1
            or not isinstance(cap.get('attemptId'),str) or not _RUN.fullmatch(cap['attemptId'])
            or type(cap.get('budgetSeconds')) is not int or not 5<=cap['budgetSeconds']<=600):
        raise GoalContractError('INVALID_FINAL_AUTHORIZATION')
    return e,expected


def load_historical_context(run_id,candidate_id,binding_digest):
    root=configured_state_root()
    e,registry_digest=_entry(root,run_id,candidate_id,binding_digest)
    cap=e['finalAuthorization'];approval=e['deltaAuthorization'];profile=final_workspace_profile(e['profile'])
    key=canonical_digest({'runId':run_id,'candidateId':candidate_id})
    receipt=_read(root/'delta-trial-attempts'/(key+'.json'),root,128*1024)
    context=HistoricalReadContext(_bytes(e),registry_digest,_bytes(receipt),_bytes(profile),
        canonical_digest(cap),cap['attemptId'],cap['budgetSeconds'])
    reg=context.registration
    from deploom_mcp.goal_execution_registry import require_registered_execution_class
    from deploom_mcp.goal_verifier_bridge import scoped_profile_requirements_complete
    if (require_registered_execution_class(reg)!='PREAPPROVED_HOST_POLICY'
            or receipt.get('status')!='HOST_AUTHORIZED_SCOPED_CANDIDATE_PROFILE_ACCEPTED'
            or receipt.get('candidateProfileAccepted') is not True or receipt.get('goalChecksComplete') is not True
            or receipt.get('checkpointStatus')!='NOT_PUBLISHED'
            or canonical_digest(receipt)!=cap.get('acceptedReceiptDigest')
            or not scoped_profile_requirements_complete(receipt,reg)):
        raise GoalContractError('FINAL_HISTORICAL_RECEIPT_UNPROVEN')
    wanted={'schemaVersion':1,'operation':FINAL_OPERATION,'origin':'PREAPPROVED_HOST_POLICY',
        'attemptId':cap['attemptId'],'budgetSeconds':cap['budgetSeconds'],
        'deadlineUTC':datetime.fromtimestamp(reg.deadline,timezone.utc).isoformat(),
        'bindingDigest':binding_digest,'goalDigest':reg.binding['goalDigest'],
        'historicalProfileDigest':canonical_digest(reg.profile),'finalProfileDigest':canonical_digest(profile),'finalProfile':profile,
        'allowedChangeScopeDigest':reg.profile['admission']['allowedChangeScopeDigest'],
        'policyDigest':reg.goal['policyDigest'],'candidateDigest':approval['candidateDigest'],'deltaDigest':approval['deltaDigest'],
        'baselineSourceSnapshotKey':approval['sourceSnapshotKey'],'acceptedReceiptDigest':canonical_digest(receipt),
        'implementationDigest':reg.binding['identity']['implementationDigest'],
        'toolchainDigest':reg.binding['identity']['toolchainDigest'],
        'stateRootDigest':canonical_digest({'hostStateRoot':str(root.resolve())}),
        'authorizedSourceWrites':'EXACT_APPROVED_REPLACE_EXISTING',
        'authorizedGeneratedMaterialization':{'mode':'STAGE_PRESERVE_AND_SWITCH','dependencyRoot':'node_modules',
            'existingTree':'PRESERVE_ORDINARY_NO_LINKS'},
        'externalWriterPolicy':'WINDOWS_NTFS_HELD_IDENTITIES_NO_OVERWRITE_RECOVERY'}
    if cap!=wanted:raise GoalContractError('FINAL_AUTHORIZATION_BINDING_MISMATCH')
    attempt=canonical_digest({'runId':run_id,'candidateId':candidate_id,'attemptId':context.attempt_id})
    envelope=_read(root/'final-apply-attempts'/attempt/'transaction.json',root)
    tx=validate_terminal_record(envelope,context);started=tx['startedAt']
    lineage=validate_recorded_lineage(root,reg.goal,reg.binding,reg.policy,reg.profile,started)
    return context,lineage


def validate_terminal_record(envelope,context):
    reg=context.registration;run_id=reg.run_id;candidate_id=reg.candidate_id
    binding_digest=reg.binding['bindingDigest'];registry_digest=context.registry_digest
    attempt=canonical_digest({'runId':run_id,'candidateId':candidate_id,'attemptId':context.attempt_id})
    profile=context.final_profile;tx=envelope.get('payload',{})
    if (set(envelope)!={'schemaVersion','payload','sha256'} or type(envelope['schemaVersion']) is not int
            or envelope['schemaVersion']!=1 or envelope['sha256']!=canonical_digest(tx)
            or tx.get('phase')!='TERMINAL_PASS' or tx.get('registryDigest')!=registry_digest
            or tx.get('authorizationDigest')!=context.authorization_digest
            or tx.get('bindingDigest')!=binding_digest or tx.get('profileDigest')!=canonical_digest(profile)
            or tx.get('attemptKey')!=attempt or tx.get('attemptId')!=context.attempt_id
            or tx.get('runId')!=run_id or tx.get('candidateId')!=candidate_id
            or tx.get('project')!=reg.project_path or tx.get('budgetSeconds')!=context.budget_seconds):
        raise GoalContractError('FINAL_COMPLETED_CHECKPOINT_REQUIRED')
    started=tx.get('startedAt');deadline=tx.get('deadline');now=datetime.now(timezone.utc).timestamp()
    if (type(started) not in (int,float) or type(deadline) not in (int,float)
            or not math.isfinite(started) or not math.isfinite(deadline)
            or not started<=now or not started<deadline or deadline!=min(reg.deadline,started+context.budget_seconds)):
        raise GoalContractError('HISTORICAL_TIME_UNPROVEN')
    return tx


def validate_historical_pointer(pointer):
    from deploom_mcp.goal_final_checkpoint import _typed_pointer
    if not _typed_pointer(pointer):
        raise GoalContractError('HISTORICAL_LATEST_POINTER_INVALID')


def read_historical_checkpoint(run_id,candidate_id,binding_digest):
    context,lineage=load_historical_context(run_id,candidate_id,binding_digest)
    from deploom_mcp.goal_final_checkpoint import inspect_checkpoint_evidence,_paths
    _,record,cp,_,_=inspect_checkpoint_evidence(context)
    # Latest syntax is a separate fail-closed guard, never evidence of this
    # historical checkpoint being current. A valid successor does not erase it.
    pointer=_read(_paths(context)[2]/'current.json',configured_state_root(),128*1024)
    validate_historical_pointer(pointer)
    return {'ok':True,'mode':'historical','status':'HISTORICAL_RECORD_VERIFIED',
        'recordIntegrity':'VERIFIED','historicalSelectedProfileBinding':'VERIFIED',
        'recordedPhase':record['phase'],'checkpointId':cp['checkpointId'],
        'checkpointDigest':canonical_digest(cp),'sourceSnapshotKey':cp['sourceSnapshotKey'],
        'attemptId':context.attempt_id,
        'recordedImplementationDigest':context.registration.binding['identity']['implementationDigest'],
        'recordedToolchainDigest':context.registration.binding['identity']['toolchainDigest'],
        'currentVerification':'NOT_EVALUATED','currentWorkspaceVerified':None,'executionReady':False,
        'fullAcceptance':False,'wholeDepLoomGoalComplete':False,
        'trustBoundary':'OPERATOR_CONTROLLED_STORE_NOT_CRYPTOGRAPHIC_ATTESTATION',
        'mathematicalModelReevaluation':'NOT_PERFORMED',
        **({'selectionLineage':lineage} if lineage is not None else {})}