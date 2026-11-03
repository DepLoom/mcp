"""Selected-goal checkpoint publication after actual current-workspace checks.

This is not iterative-controller state or a whole-product certification. Durable
source snapshots and native file/tree observations remain at their Core owners.
"""
from __future__ import annotations
import hashlib,json,os,shutil,time
from pathlib import Path
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_report import _plain_under
from deploom_mcp.goal_delta_trial import _json,_core
from deploom_mcp.goal_final_authorization import load_final_grant,consume_final_grant,FINAL_OPERATION
from deploom_mcp.goal_final_transaction import FinalApplyTransaction
from deploom_mcp.goal_final_materialization import compiled_obligations,materialize_dependencies,verify_live_workspace
from deploom_mcp.goal_windows_file_swap import observe_swap
from deploom_mcp.goal_windows_tree_swap import observe_delivered_tree


def current_environment_matches(observation,policy=None):
    from deploom_mcp.goal_execution_environment import environment_matches
    return environment_matches(policy or {},observation)

def live_complete(context,observation,plan,project,tap_receipt=None):
    from deploom_mcp.goal_workspace_observation import workspace_profile_complete
    try:
        expected=context.accepted_receipt['verificationObservation']['candidateVerifiedSourceSnapshotKey']
        from deploom_mcp.goal_execution_environment import environment_observation_matches_contract
        from deploom_mcp.goal_tap_nyc_execution import receipt_matches
        from deploom_mcp.goal_generated_outputs import observation_matches
        return (observation_matches(context.registration.policy,context.final_profile,observation)
            and environment_observation_matches_contract(context.registration.policy,observation)
            and workspace_profile_complete(context.final_profile,expected,observation,plan,project)
            and receipt_matches(context.registration.policy,context.final_profile,'final',project,observation,tap_receipt))
    except (KeyError,TypeError,ValueError,AttributeError):return False

def current_state_matches(context,record,project,*,_trace=None,_budget=None):
    if _budget is not None:_budget.remaining()
    if _trace is not None:_trace.mark('environment','BEGIN')
    if not current_environment_matches(record['liveObservation'],context.registration.policy):
        if _trace is not None:_trace.mark('environment',False)
        return False
    if _trace is not None:_trace.mark('environment',True)
    from deploom_mcp.goal_tap_nyc_execution import physically_current
    if _trace is not None:_trace.mark('tap','BEGIN')
    result=physically_current(context.registration.policy,context.final_profile,project,record['liveObservation'],
        record.get('tapInputObservation'),deadline=_budget.wall_deadline() if _budget is not None else time.time()+20)
    if _budget is not None:_budget.remaining()
    if _trace is not None:_trace.mark('tap',result)
    if not result:return False
    from deploom_mcp.goal_generated_outputs import physically_current as generated_current
    declared='standaloneOutputSpec' in context.registration.policy
    if declared and _trace is not None:_trace.mark('generated_outputs','BEGIN')
    generated=generated_current(context.registration.policy,context.final_profile,project,record['liveObservation'],
        deadline=_budget.wall_deadline() if _budget is not None else time.time()+20)
    if _budget is not None:_budget.remaining()
    if declared and _trace is not None:_trace.mark('generated_outputs',generated)
    return generated


def tap_checkpoint_identity(record):
    receipt=record.get('tapInputObservation')
    from deploom_mcp.goal_generated_outputs import checkpoint_identity
    return {**({} if receipt is None else {'tapInputObservationDigest':canonical_digest(receipt)}),**checkpoint_identity(record)}


def _read(path,limit=8*1024*1024):return _json(Path(path).read_bytes(),limit)


def _paths(context):
    from deploom_mcp.goal_execution_registry import configured_state_root
    root=configured_state_root().resolve();reg=context.registration;project=Path(reg.project_path).resolve()
    key=canonical_digest({'runId':reg.run_id,'candidateId':reg.candidate_id,'attemptId':context.attempt_id})
    directory=_plain_under(root/'final-apply-attempts'/key,root)
    state=_plain_under(root/'final-goal-projects'/canonical_digest({'project':str(project)}),root)
    return project,directory,state


def _typed_pointer(value):
    """A checkpoint ID alone is not the published generation/attempt identity."""
    from deploom_mcp.goal_report import _SHA
    return (isinstance(value,dict)
        and set(value)=={'schemaVersion','checkpointId','checkpointDigest','generation','attemptKey'}
        and type(value['schemaVersion']) is int and value['schemaVersion']==1
        and type(value['generation']) is int and value['generation']>=1
        and all(isinstance(value[key],str) and _SHA.fullmatch(value[key])
            for key in ('checkpointId','checkpointDigest','attemptKey')))


def validate_existing_incumbent(pointer,state,root,core):
    """Check the previous pointer against its separate terminal owner artifacts."""
    if pointer is None:return
    if not _typed_pointer(pointer):raise GoalContractError('FINAL_PREVIOUS_INCUMBENT_UNPROVEN')
    cp=_read(_plain_under(state/'checkpoints'/(pointer['checkpointId']+'.json'),root))
    previous=_plain_under(root/'final-apply-attempts'/pointer['attemptKey'],root)
    envelope=_read(previous/'transaction.json');record=envelope.get('payload',{})
    if (envelope.get('sha256')!=canonical_digest(record) or record.get('phase')!='TERMINAL_PASS'
            or record.get('publicationTarget')!=pointer or not _typed_pointer(record.get('publicationTarget'))
            or record.get('attemptKey')!=pointer['attemptKey'] or record.get('checkpointId')!=pointer['checkpointId']
            or record.get('checkpointDigest')!=pointer['checkpointDigest']
            or record.get('checkpointPayload')!=cp or canonical_digest(cp)!=pointer['checkpointDigest']
            or type(cp.get('schemaVersion')) is not int or cp['schemaVersion']!=1
            or cp.get('kind')!='GOAL_FINAL_CHECKPOINT_V1' or cp.get('status')!='VERIFIED_SELECTED_PROFILE'
            or cp.get('checkpointId')!=pointer['checkpointId'] or cp.get('attemptKey')!=pointer['attemptKey']
            or cp.get('authorizationDigest')!=record.get('authorizationDigest')
            or cp.get('sourceSnapshotKey')!=record.get('liveObservation',{}).get('sourceSnapshotKey')
            or cp.get('tapInputObservationDigest')!=tap_checkpoint_identity(record).get('tapInputObservationDigest')
            or cp.get('generatedOutputObservationDigest')!=tap_checkpoint_identity(record).get('generatedOutputObservationDigest')):
        raise GoalContractError('FINAL_PREVIOUS_INCUMBENT_UNPROVEN')
    snapshot=core.open_source_snapshot(previous/'verified-source',expected_key=cp['sourceSnapshotKey'],timeout_seconds=20)
    core.validate_source_snapshot(snapshot,timeout_seconds=20)


def _publication_target(context,record,checkpoint_id,checkpoint):
    """Reconstruct the owner-published target without granting write authority."""
    reg=context.registration
    key=canonical_digest({'runId':reg.run_id,'candidateId':reg.candidate_id,'attemptId':context.attempt_id})
    parent=record.get('incumbent')
    if parent is not None and not _typed_pointer(parent):
        raise GoalContractError('FINAL_PUBLICATION_IDENTITY_UNPROVEN')
    expected={'schemaVersion':1,'checkpointId':checkpoint_id,'checkpointDigest':canonical_digest(checkpoint),
        'generation':(parent['generation'] if parent is not None else 0)+1,'attemptKey':key}
    target=record.get('publicationTarget')
    if (not _typed_pointer(target) or target!=expected
            or type(checkpoint.get('schemaVersion')) is not int or checkpoint['schemaVersion']!=1
            or record.get('attemptKey')!=key or record.get('attemptId')!=context.attempt_id
            or record.get('runId')!=reg.run_id or record.get('candidateId')!=reg.candidate_id
            or checkpoint.get('attemptKey')!=key or checkpoint.get('attemptId')!=context.attempt_id
            or checkpoint.get('goalDigest')!=reg.binding['goalDigest']
            or checkpoint.get('parentCheckpointId')!=(parent or {}).get('checkpointId')
            or record.get('checkpointPayload')!=checkpoint):
        raise GoalContractError('FINAL_PUBLICATION_IDENTITY_UNPROVEN')
    return expected


def inspect_checkpoint_evidence(context,*,completed=True):
    """Validate sealed owner evidence; does not observe the current workspace."""
    project,directory,state=_paths(context);envelope=_read(directory/'transaction.json');record=envelope['payload']
    if envelope['sha256']!=canonical_digest(record) or (completed and record['phase']!='TERMINAL_PASS'):
        raise GoalContractError('FINAL_CHECKPOINT_RECORD_UNPROVEN')
    if record['authorizationDigest']!=context.authorization_digest:raise GoalContractError('FINAL_CHECKPOINT_AUTHORIZATION_CHANGED')
    checkpoint_id=record['checkpointId']
    from deploom_mcp.goal_report import _SHA
    if not isinstance(checkpoint_id,str) or not _SHA.fullmatch(checkpoint_id):raise GoalContractError('INVALID_FINAL_CHECKPOINT_ID')
    cp=_read(state/'checkpoints'/(checkpoint_id+'.json'))
    if (canonical_digest(cp)!=record['checkpointDigest'] or cp['kind']!='GOAL_FINAL_CHECKPOINT_V1'
            or cp['checkpointId']!=checkpoint_id or cp['status']!='VERIFIED_SELECTED_PROFILE'
            or cp['bindingDigest']!=context.registration.binding['bindingDigest']
            or cp['authorizationDigest']!=context.authorization_digest
            or cp['finalProfileDigest']!=canonical_digest(context.final_profile)
            or cp['sourceSnapshotKey']!=record['liveObservation']['sourceSnapshotKey']
            or cp['liveObservationDigest']!=canonical_digest(record['liveObservation'])
            or cp.get('tapInputObservationDigest')!=tap_checkpoint_identity(record).get('tapInputObservationDigest')
            or cp.get('generatedOutputObservationDigest')!=tap_checkpoint_identity(record).get('generatedOutputObservationDigest')):
        raise GoalContractError('FINAL_CHECKPOINT_IDENTITY_UNPROVEN')
    core=_core()
    from deploom_mcp.goal_verifier_bridge import _candidate_bytes
    from deploom_mcp.goal_delta_trial import describe_exact_delta
    from deploom_mcp.goal_final_materialization import compile_bound_obligations
    baseline=core.open_source_snapshot(directory/'baseline-source',expected_key=context.registration.delta_authorization['sourceSnapshotKey'],timeout_seconds=20)
    proposal=describe_exact_delta(context.registration,baseline,*_candidate_bytes(context.registration))
    plan=compile_bound_obligations(context.registration,baseline,proposal)
    if (proposal.candidate_digest!=cp['candidateDigest'] or proposal.delta_digest!=cp['deltaDigest']
            or not live_complete(context,record['liveObservation'],plan,project,record.get('tapInputObservation'))):
        raise GoalContractError('FINAL_LIVE_PROFILE_UNPROVEN')
    snapshot=core.open_source_snapshot(directory/'verified-source',expected_key=cp['sourceSnapshotKey'],timeout_seconds=20)
    core.validate_source_snapshot(snapshot,timeout_seconds=20)
    pointer=_read(state/'current.json',128*1024)
    validate_existing_incumbent(record.get('incumbent'),state,directory.parent.parent,core)
    target=_publication_target(context,record,checkpoint_id,cp)
    incumbent=_typed_pointer(pointer) and pointer==target
    return project,record,cp,incumbent,core


def inspect_checkpoint(context,*,completed=True,_diagnostics=True,_budget=None):
    # Internal process-owned budget, never an MCP request parameter. Legacy
    # registrations keep the original reader until supervised v1 is selected.
    if _budget is not None:_budget.remaining()
    project,record,cp,incumbent,core=inspect_checkpoint_evidence(context,completed=completed)
    checkpoint_id=cp['checkpointId']
    from deploom_mcp.goal_current_read_trace import CurrentReadTrace
    trace=CurrentReadTrace(enabled=_diagnostics)
    current=False
    try:
        deadline=_budget.wall_deadline() if _budget is not None else time.time()+20
        trace.mark('incumbent',incumbent)
        trace.mark('source','BEGIN' if incumbent else 'SKIPPED')
        source_args={'remaining_budget':_budget.remaining} if _budget is not None else {}
        if incumbent and core.source_snapshot_fingerprint(project,**source_args)==cp['sourceSnapshotKey']:
            if _budget is not None:deadline=_budget.wall_deadline()
            trace.mark('source',True)
            trace.mark('swaps','BEGIN')
            for move in record['windowsMoves'].values():
                if observe_swap(move,deadline=deadline,completed_read=True)!='DELIVERED':raise GoalContractError('FINAL_SOURCE_NOT_CURRENT')
            if _budget is not None:deadline=_budget.wall_deadline()
            trace.mark('swaps',True)
            trace.mark('tree','BEGIN')
            tree_args={'_budget':_budget} if _budget is not None else {}
            observe_delivered_tree(record['dependencyTreeMove'],deadline=deadline,**tree_args)
            if _budget is not None:_budget.remaining()
            trace.mark('tree',True)
            trace.mark('toolchain','BEGIN')
            node=shutil.which('node');npm=Path(node).parent/'node_modules/npm/bin/npm-cli.js' if node else None
            tools=record['liveObservation']['materializedSourceOrigin']['toolchain']
            if not node or not npm.is_file() or tools!={'nodeSha256':hashlib.sha256(Path(node).read_bytes()).hexdigest(),'npmCliSha256':hashlib.sha256(npm.read_bytes()).hexdigest()}:
                raise GoalContractError('FINAL_TOOLCHAIN_NOT_CURRENT')
            trace.mark('toolchain',True)
            trace.mark('environment_and_tap','BEGIN')
            budget_args={'_budget':_budget} if _budget is not None else {}
            current=current_state_matches(context,record,project,_trace=trace,**budget_args)
            if _budget is not None:
                _budget.remaining()
                if current:
                    from deploom_mcp.goal_current_proof_boundary import revalidate_current_boundary
                    current=revalidate_current_boundary(context,record,project,cp,core,_budget,trace)
            trace.mark('environment_and_tap',current)
        elif incumbent:trace.mark('source',False)
    except (OSError,RuntimeError,KeyError,TypeError,ValueError) as exc:
        current=False
        trace.exception(exc)
    trace.mark('verdict',current)
    # Diagnostic persistence is outside every original current-check deadline.
    # It never supplies authority or changes the canonical checkpoint response.
    trace.persist(context,cp)
    if _budget is not None:
        try:_budget.remaining()
        except GoalContractError:current=False
    return {'ok':True,'status':'VERIFIED_CHECKPOINT_PUBLISHED','checkpointId':checkpoint_id,
        'checkpointDigest':canonical_digest(cp),'checkpointStatus':'VERIFIED_PUBLISHED',
        'checkpointVerifiedHistorical':True,'isCurrentIncumbent':incumbent,'currentWorkspaceVerified':current,
        'candidateProfileAccepted':True,'goalChecksComplete':True,'scope':'REGISTERED_WORKSPACE_SELECTED_GOAL_PROFILE',
        'sourceSnapshotKey':cp['sourceSnapshotKey'],'attemptId':context.attempt_id,
        'fullAcceptance':False,'wholeDepLoomGoalComplete':False,'runtimeScope':'HOST_PINNED_SELECTED_CHECKS',
        'externalInputClosure':'HOST_DECLARED_NOT_ENFORCED','payloadAuthenticity':'NOT_ATTESTED', **({'selectionLineage':cp['selectionLineage']} if 'selectionLineage' in cp else {})}


def publish_checkpoint(tx,plan):
    from verification_stage_checkpoint import write_atomic
    observation=tx.record['liveObservation']
    if not live_complete(tx.context,observation,plan,tx.project,tx.record.get('tapInputObservation')):raise GoalContractError('FINAL_LIVE_PROFILE_UNPROVEN')
    if not current_state_matches(tx.context,tx.record,tx.project):raise GoalContractError('FINAL_LIVE_ENVIRONMENT_CHANGED')
    tx.guard_source();observe_delivered_tree(tx.record['dependencyTreeMove'],deadline=tx.record['deadline'])
    destination=tx.directory/'verified-source';core=tx.core
    if destination.exists():snapshot=core.open_source_snapshot(destination,expected_key=observation['sourceSnapshotKey'],timeout_seconds=tx.remaining())
    else:
        # Reclaiming another run's orphan data is not publication proof work.
        # The stock capture still validates all bytes and cleans its own temp.
        with core.defer_orphan_snapshot_maintenance():
            snapshot=core.capture_durable_source_snapshot(tx.project,destination,timeout_seconds=tx.remaining())
    if snapshot.key!=observation['sourceSnapshotKey']:raise GoalContractError('FINAL_VERIFIED_SNAPSHOT_MISMATCH')
    core.open_source_snapshot(destination,expected_key=snapshot.key,timeout_seconds=tx.remaining())
    tx.guard_source();observe_delivered_tree(tx.record['dependencyTreeMove'],deadline=tx.record['deadline'])
    tx.save('SNAPSHOT_DURABLE');tx.cut('SNAPSHOT_DURABLE')
    tap_identity=tap_checkpoint_identity(tx.record)
    checkpoint_id=canonical_digest({'attemptKey':tx.key,'sourceKey':snapshot.key,'liveObservationDigest':canonical_digest(observation),**tap_identity})
    parent=tx.record['incumbent'];generation=(parent or {}).get('generation',0)+1
    cp={'schemaVersion':1,'kind':'GOAL_FINAL_CHECKPOINT_V1','checkpointId':checkpoint_id,
        'status':'VERIFIED_SELECTED_PROFILE','sourceSnapshotKey':snapshot.key,'attemptId':tx.context.attempt_id,
        'attemptKey':tx.key,'parentCheckpointId':(parent or {}).get('checkpointId'),
        'bindingDigest':tx.registration.binding['bindingDigest'],'goalDigest':tx.registration.binding['goalDigest'],
        'finalProfileDigest':canonical_digest(tx.context.final_profile),'authorizationDigest':tx.context.authorization_digest,
        'candidateDigest':tx.proposal.candidate_digest,'deltaDigest':tx.proposal.delta_digest,
        'liveObservationDigest':canonical_digest(observation),**tap_identity,'registeredProfileScope':tx.context.final_profile['admission']['scope'],
        'originalDeadline':tx.record['deadline'],'spentSeconds':max(0,time.time()-tx.record['startedAt']),
        'fullAcceptance':False,'historicalSelectedProfileOnly':True}
    if 'coupledChoice' in tx.registration.policy:
        cp['selectionLineage']=dict(tx.registration.policy['coupledChoice'])
    frozen=tx.record.get('checkpointPayload')
    if frozen is not None:
        if {k:v for k,v in frozen.items() if k!='spentSeconds'}!={k:v for k,v in cp.items() if k!='spentSeconds'}:
            raise GoalContractError('FINAL_PUBLICATION_INTENT_CHANGED')
        cp=frozen
    cp_path=tx.project_state/'checkpoints'/(checkpoint_id+'.json');pointer_path=tx.project_state/'current.json'
    target={'schemaVersion':1,'checkpointId':checkpoint_id,'checkpointDigest':canonical_digest(cp),'generation':generation,'attemptKey':tx.key}
    validate_existing_incumbent(parent,tx.project_state,tx.root,core)
    current=_read(pointer_path,128*1024) if pointer_path.exists() else None
    if current not in (parent,target):raise GoalContractError('FINAL_INCUMBENT_CHANGED')
    if cp_path.exists() and _read(cp_path)!=cp:raise GoalContractError('FINAL_CHECKPOINT_ARTIFACT_CHANGED')
    tx.record.update(checkpointId=checkpoint_id,checkpointDigest=canonical_digest(cp),publicationTarget=target,checkpointPayload=cp)
    tx.save();tx.cut('PUBLICATION_INTENT')
    if not cp_path.exists():write_atomic(cp_path,cp)
    tx.check_permission();core.open_source_snapshot(destination,expected_key=snapshot.key,timeout_seconds=tx.remaining())
    tx.guard_source();observe_delivered_tree(tx.record['dependencyTreeMove'],deadline=tx.record['deadline'])
    validate_existing_incumbent(parent,tx.project_state,tx.root,core)
    current=_read(pointer_path,128*1024) if pointer_path.exists() else None
    if current not in (parent,target):raise GoalContractError('FINAL_INCUMBENT_CHANGED')
    if not current_state_matches(tx.context,tx.record,tx.project):raise GoalContractError('FINAL_LIVE_ENVIRONMENT_CHANGED')
    if current!=target:write_atomic(pointer_path,target)
    tx.save('POINTER_COMMITTED');tx.cut('POINTER_COMMITTED')
    tx.record.update(currentWorkspaceVerified=True,checkpointStatus='VERIFIED_PUBLISHED')
    tx.save('TERMINAL_PASS')


def inspect_registered_checkpoint(context,*,completed=True,original_deadline_utc=None,expected_terminal=None):
    from deploom_mcp.goal_current_proof_budget import registered_current_proof_spec,CurrentProofBudget
    spec=registered_current_proof_spec(context.registration)
    if spec is None:
        if expected_terminal is not None:raise GoalContractError('CURRENT_PROOF_SPEC_REQUIRED')
        return inspect_checkpoint(context,completed=completed)
    budget=CurrentProofBudget(spec,original_deadline_utc=original_deadline_utc)
    from deploom_mcp.goal_current_proof_process import terminal_read_identity,assert_terminal_read_result
    if completed:expected_terminal=terminal_read_identity(context.registration,expected_terminal)
    elif expected_terminal is not None:raise GoalContractError('FINAL_COMPLETED_CHECKPOINT_REQUIRED')
    # Authoritative reads do not synchronously persist diagnostic files.
    result=inspect_checkpoint(context,completed=completed,_budget=budget,_diagnostics=False)
    if expected_terminal is not None:
        terminal_read_identity(context.registration,expected_terminal)
        assert_terminal_read_result(result,expected_terminal)
    if result.get('currentWorkspaceVerified'):
        # Reopen the owning sealed evidence and incumbent pointer at closure.
        # A terminal journal alone does not pin the separate current pointer.
        _,_,current_cp,incumbent,_=inspect_checkpoint_evidence(context,completed=completed)
        if (current_cp['checkpointId']!=result['checkpointId']
                or canonical_digest(current_cp)!=result['checkpointDigest']):
            raise GoalContractError('FINAL_CHECKPOINT_IDENTITY_UNPROVEN')
        if not incumbent:
            result={**result,'isCurrentIncumbent':False,'currentWorkspaceVerified':False}
    budget.remaining()
    return result


def execute_final_candidate(run_id,candidate_id,binding_digest,authorization,*,_fault_cutpoint=None):
    from deploom_mcp.goal_verifier_bridge import _candidate_bytes
    grant=load_final_grant(run_id,candidate_id,binding_digest,authorization)
    with FinalApplyTransaction(grant,fault_cutpoint=_fault_cutpoint) as tx:
        context=tx.context
        if tx.record is not None and tx.record['phase']=='TERMINAL_PASS':return inspect_registered_checkpoint(context,original_deadline_utc=tx.record['deadline'])
        tx.start(_candidate_bytes(tx.registration))
        if tx.record['phase']=='POINTER_COMMITTED':
            confirmed=inspect_registered_checkpoint(context,completed=False,original_deadline_utc=tx.record['deadline'])
            if not confirmed['currentWorkspaceVerified']:raise GoalContractError('FINAL_CURRENT_STATE_UNPROVEN')
            tx.record.update(currentWorkspaceVerified=True,checkpointStatus='VERIFIED_PUBLISHED');tx.save('TERMINAL_PASS')
        else:
            tx.deliver_source();plan=compiled_obligations(tx)
            if tx.record['phase'] not in {'LIVE_VERIFIED','SNAPSHOT_DURABLE'}:
                materialize_dependencies(tx);verify_live_workspace(tx,plan)
            publish_checkpoint(tx,plan)
    return inspect_registered_checkpoint(context,original_deadline_utc=tx.record['deadline'])


def read_final_checkpoint(run_id,candidate_id,binding_digest,*,_proof_deadline_utc=None,_expected_terminal=None):
    context=consume_final_grant(load_final_grant(run_id,candidate_id,binding_digest,FINAL_OPERATION,_completed_read=True))
    return inspect_registered_checkpoint(context,original_deadline_utc=_proof_deadline_utc,expected_terminal=_expected_terminal)
