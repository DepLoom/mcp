"""Final MCP parent reopens the real durable checkpoint after worker completion."""
import json,os,sys,time
from pathlib import Path
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_final_authorization import load_final_grant,consume_final_grant
from deploom_mcp.goal_final_checkpoint import read_final_checkpoint
from deploom_mcp.process_runner import run_process_async
from deploom_mcp.goal_current_proof_budget import registered_current_proof_spec
from deploom_mcp.goal_current_proof_process import PendingCurrentReplay,supervised_current_read,final_worker_reservation,terminal_read_identity

def _completed_replay(run_id,candidate_id,binding_digest,authorization):
    """Probe is read-only; only the existing authoritative reader grants success.

    A missing/nonterminal journal retains the ordinary permission/deadline path.
    Corrupt or foreign records never fall back to execution. No writer lease,
    receipt publication or worker is created for an existing terminal result.
    """
    from deploom_mcp.goal_final_authorization import FINAL_OPERATION
    from deploom_mcp.goal_execution_registry import load_host_registration,configured_state_root,OPERATION
    from deploom_mcp.goal_report import _RUN,_plain_under
    from deploom_mcp.goal_delta_trial import _json
    if authorization!=FINAL_OPERATION:
        raise GoalContractError('EXPLICIT_FINAL_APPLY_AUTHORIZATION_REQUIRED')
    reg=load_host_registration(run_id,candidate_id,binding_digest,OPERATION,allow_expired_read=True)
    cap=reg.final_authorization
    if not isinstance(cap,dict) or not isinstance(cap.get('attemptId'),str) or not _RUN.fullmatch(cap['attemptId']):
        raise GoalContractError('FINAL_APPLY_AUTHORIZATION_UNAVAILABLE')
    key=canonical_digest({'runId':run_id,'candidateId':candidate_id,'attemptId':cap['attemptId']})
    root=configured_state_root().resolve()
    path=_plain_under(root/'final-apply-attempts'/key/'transaction.json',root)
    if not path.exists():return None
    envelope=_json(path.read_bytes(),8*1024*1024)
    if not isinstance(envelope,dict):raise GoalContractError('FINAL_TRANSACTION_JOURNAL_UNPROVEN')
    record=envelope.get('payload')
    if (set(envelope)!={'schemaVersion','payload','sha256'} or type(envelope['schemaVersion']) is not int
            or envelope['schemaVersion']!=1 or not isinstance(record,dict)
            or envelope['sha256']!=canonical_digest(record)):
        raise GoalContractError('FINAL_TRANSACTION_JOURNAL_UNPROVEN')
    if (record.get('attemptKey')!=key or record.get('attemptId')!=cap['attemptId']
            or record.get('runId')!=run_id or record.get('candidateId')!=candidate_id
            or record.get('bindingDigest')!=binding_digest
            or record.get('authorizationDigest')!=canonical_digest(cap)):
        raise GoalContractError('FINAL_TRANSACTION_IDENTITY_CHANGED')
    phase=record.get('phase')
    if phase!='TERMINAL_PASS':
        if phase not in {'INTENT_DURABLE','SOURCE_DELIVERED','DEPENDENCIES_STAGED',
                'DEPENDENCIES_READY','LIVE_VERIFIED','SNAPSHOT_DURABLE','POINTER_COMMITTED'}:
            raise GoalContractError('FINAL_TRANSACTION_PHASE_UNPROVEN')
        return None
    if registered_current_proof_spec(reg) is not None:
        identity={'attemptId':record['attemptId'],'authorizationDigest':record['authorizationDigest'],
            'journalDigest':envelope['sha256'],'checkpointId':record.get('checkpointId'),
            'checkpointDigest':record.get('checkpointDigest')}
        return PendingCurrentReplay(reg,terminal_read_identity(reg,identity))
    current=read_final_checkpoint(run_id,candidate_id,binding_digest)
    if not current.get('currentWorkspaceVerified') or not current.get('isCurrentIncumbent'):
        raise GoalContractError('FINAL_CURRENT_WORKSPACE_CONTINUITY_UNPROVEN')
    return current


async def apply_goal_candidate(run_id,candidate_id,expected_binding_digest,authorization=''):
    try:
        completed=_completed_replay(run_id,candidate_id,expected_binding_digest,authorization)
        if isinstance(completed,PendingCurrentReplay):
            current=await supervised_current_read(run_id,candidate_id,expected_binding_digest,completed.registration,expected_terminal=completed.terminal_identity)
            if not current.get('currentWorkspaceVerified') or not current.get('isCurrentIncumbent'):
                raise GoalContractError('FINAL_CURRENT_WORKSPACE_CONTINUITY_UNPROVEN')
            return current
        if completed is not None:return completed
        context=consume_final_grant(load_final_grant(run_id,candidate_id,expected_binding_digest,authorization))
        reservation=final_worker_reservation(context,time.time())
        worker_timeout=reservation['workerTimeout'] if reservation is not None else min(600,context.registration.deadline-time.time())
        process=await run_process_async([sys.executable,'-B','-m','deploom_mcp.goal_final_worker','apply',run_id,candidate_id,expected_binding_digest,authorization],
            cwd=str(Path(__file__).parent),env=dict(os.environ),timeout=worker_timeout,max_output_bytes=65536)
        if process.timed_out:return {'ok':False,'error':{'code':'FINAL_WORKER_TIMEOUT'},'fullAcceptance':False}
        payload=json.loads(process.stdout)
        if not isinstance(payload,dict):raise GoalContractError('FINAL_WORKER_UNCONFIRMED')
        if process.returncode or payload.get('ok') is not True:return {'ok':False,'error':{'code':payload.get('error',{}).get('code','FINAL_WORKER_UNCONFIRMED')},'fullAcceptance':False}
        if not isinstance(payload.get('result'),dict):raise GoalContractError('FINAL_PARENT_RECEIPT_MISMATCH')
        # Pinned registry and owning physical gates, not the worker's PASS string.
        if reservation is None:current=read_final_checkpoint(run_id,candidate_id,expected_binding_digest)
        else:current=await supervised_current_read(run_id,candidate_id,expected_binding_digest,context.registration,original_deadline_utc=reservation['originalDeadlineUTC'])
        if canonical_digest(payload['result'])!=payload['resultDigest'] or payload['resultDigest']!=canonical_digest(current):
            raise GoalContractError('FINAL_PARENT_RECEIPT_MISMATCH')
        if not current['currentWorkspaceVerified']:raise GoalContractError('FINAL_CURRENT_WORKSPACE_CONTINUITY_UNPROVEN')
        return current
    except GoalContractError as exc:return {'ok':False,'error':{'code':exc.code},'fullAcceptance':False}
    except (OSError,ValueError,KeyError,TypeError,RuntimeError):return {'ok':False,'error':{'code':'FINAL_OPERATION_UNCONFIRMED'},'fullAcceptance':False}

async def get_goal_checkpoint(run_id,candidate_id,expected_binding_digest,mode='current'):
    try:
        if mode=='historical':
            from deploom_mcp.goal_historical_checkpoint import read_historical_checkpoint
            return read_historical_checkpoint(run_id,candidate_id,expected_binding_digest)
        if mode!='current':raise GoalContractError('INVALID_CHECKPOINT_READ_MODE')
        from deploom_mcp.goal_execution_registry import load_host_registration,OPERATION
        registration=load_host_registration(run_id,candidate_id,expected_binding_digest,OPERATION,allow_expired_read=True)
        if registered_current_proof_spec(registration) is not None:
            return await supervised_current_read(run_id,candidate_id,expected_binding_digest,registration)
        return read_final_checkpoint(run_id,candidate_id,expected_binding_digest)
    except GoalContractError as exc:return {'ok':False,'error':{'code':exc.code},'fullAcceptance':False}
    except (OSError,ValueError,KeyError,TypeError,RuntimeError):return {'ok':False,'error':{'code':'FINAL_CHECKPOINT_UNCONFIRMED'},'fullAcceptance':False}
