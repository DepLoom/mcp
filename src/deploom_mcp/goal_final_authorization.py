"""Host-pinned final capability, distinct from historical private acceptance.

These code-only grants accept identifiers, never caller paths/commands/proofs.
The final transaction owner must additionally guard all live bytes, process
closure, checkpoint publication and same-attempt recovery before any success.
"""
from __future__ import annotations
import copy
from dataclasses import dataclass
from datetime import datetime,timezone
from pathlib import Path
import weakref

from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_execution_registry import (load_host_registration,
    require_registered_execution_class,configured_state_root,OPERATION)
from deploom_mcp.goal_report import _RUN,_SHA,_plain_under
from deploom_mcp.goal_delta_trial import _json

FINAL_OPERATION='apply-final-registered-candidate'
_GRANTED=weakref.WeakKeyDictionary()


def final_workspace_profile(profile):
    """Exact derived live scope; cannot change purpose/outcomes/commands/oracles."""
    value=copy.deepcopy(profile)
    if not value.get('admission'):raise GoalContractError('FINAL_NORMATIVE_PROFILE_REQUIRED')
    scopes={'ISOLATED_NPM_V2_CANDIDATE':'REGISTERED_CURRENT_NPM_V2_WORKSPACE',
        'ISOLATED_NPM_V3_CANDIDATE':'REGISTERED_CURRENT_NPM_V3_WORKSPACE'}
    if value['admission'].get('scope') not in scopes:raise GoalContractError('FINAL_NORMATIVE_SCOPE_UNSUPPORTED')
    value['admission']['scope']=scopes[value['admission']['scope']]
    for row in value['checkBindings']:
        row['normative']['allowedInputs']['source']='SEALED_REGISTERED_WORKSPACE'
    return value


@dataclass(frozen=True)
class FinalRegistration:
    registration: object
    accepted_receipt: dict
    final_profile: dict
    authorization_digest: str
    attempt_id: str
    budget_seconds: int
    read_only: bool=False


class _FinalGrant:
    __slots__=('__weakref__',)


def load_final_grant(run_id,candidate_id,binding_digest,authorization,*,_completed_read=False):
    if authorization!=FINAL_OPERATION:
        raise GoalContractError('EXPLICIT_FINAL_APPLY_AUTHORIZATION_REQUIRED')
    if type(_completed_read) is not bool:raise GoalContractError('INVALID_FINAL_READ_MODE')
    registration=load_host_registration(run_id,candidate_id,binding_digest,OPERATION,allow_expired_read=_completed_read)
    if require_registered_execution_class(registration)!='PREAPPROVED_HOST_POLICY':
        raise GoalContractError('FINAL_APPLY_REQUIRES_NORMAL_HOST_AUTHORIZATION')
    cap=registration.final_authorization
    if not isinstance(cap,dict):raise GoalContractError('FINAL_APPLY_AUTHORIZATION_UNAVAILABLE')
    from deploom_mcp.goal_final_resource_policy import registered_final_resource_policy
    resources=registered_final_resource_policy(registration)
    final_ceiling=resources.final_seconds if resources else 600
    if (type(cap.get('schemaVersion')) is not int or cap.get('schemaVersion')!=1
            or not isinstance(cap.get('attemptId'),str) or not _RUN.fullmatch(cap['attemptId'])
            or type(cap.get('budgetSeconds')) is not int or not 5<=cap['budgetSeconds']<=final_ceiling
            or not isinstance(cap.get('acceptedReceiptDigest'),str)
            or not _SHA.fullmatch(cap['acceptedReceiptDigest'])):
        raise GoalContractError('INVALID_FINAL_AUTHORIZATION')
    from deploom_mcp.goal_verifier_bridge import trial_ledger_path,scoped_profile_requirements_complete
    root=configured_state_root().resolve();path=_plain_under(trial_ledger_path(registration),root)
    receipt=_json(path.read_bytes(),128*1024)
    if (receipt.get('status')!='HOST_AUTHORIZED_SCOPED_CANDIDATE_PROFILE_ACCEPTED'
            or receipt.get('candidateProfileAccepted') is not True
            or receipt.get('goalChecksComplete') is not True
            or receipt.get('checkpointStatus')!='NOT_PUBLISHED'
            or canonical_digest(receipt)!=cap['acceptedReceiptDigest']
            or not scoped_profile_requirements_complete(receipt,registration)):
        raise GoalContractError('FINAL_HISTORICAL_RECEIPT_UNPROVEN')
    profile=final_workspace_profile(registration.profile);approval=registration.delta_authorization
    wanted={'schemaVersion':1,'operation':FINAL_OPERATION,'origin':'PREAPPROVED_HOST_POLICY',
        'attemptId':cap['attemptId'],'budgetSeconds':cap['budgetSeconds'],
        'deadlineUTC':datetime.fromtimestamp(registration.deadline,timezone.utc).isoformat(),
        'bindingDigest':binding_digest,'goalDigest':registration.binding['goalDigest'],
        'historicalProfileDigest':canonical_digest(registration.profile),
        'finalProfileDigest':canonical_digest(profile),'finalProfile':profile,
        'allowedChangeScopeDigest':registration.profile['admission']['allowedChangeScopeDigest'],
        'policyDigest':registration.goal['policyDigest'],
        'candidateDigest':approval['candidateDigest'],'deltaDigest':approval['deltaDigest'],
        'baselineSourceSnapshotKey':approval['sourceSnapshotKey'],
        'acceptedReceiptDigest':canonical_digest(receipt),
        'implementationDigest':registration.binding['identity']['implementationDigest'],
        'toolchainDigest':registration.binding['identity']['toolchainDigest'],
        'stateRootDigest':canonical_digest({'hostStateRoot':str(root)}),
        'authorizedSourceWrites':'EXACT_APPROVED_REPLACE_EXISTING',
        'authorizedGeneratedMaterialization':{'mode':'STAGE_PRESERVE_AND_SWITCH',
            'dependencyRoot':'node_modules','existingTree':'PRESERVE_ORDINARY_NO_LINKS'},
        'externalWriterPolicy':'WINDOWS_NTFS_HELD_IDENTITIES_NO_OVERWRITE_RECOVERY'}
    if cap!=wanted:raise GoalContractError('FINAL_AUTHORIZATION_BINDING_MISMATCH')
    from deploom_mcp.goal_worker import prepare_goal_plan
    runtime_subject=Path(registration.project_path)
    attempt_key=canonical_digest({'runId':run_id,'candidateId':candidate_id,'attemptId':cap['attemptId']})
    attempt_root=_plain_under(root/'final-apply-attempts'/attempt_key,root)
    if (attempt_root/'transaction.json').exists():
        # A recoverable move can leave a manifest temporarily absent. Observe
        # current implementation/toolchain against the exact sealed baseline;
        # the final transaction separately validates actual partial live bytes.
        from deploom_mcp.goal_delta_trial import _core
        snapshot=_core().open_source_snapshot(attempt_root/'baseline-source',
            expected_key=approval['sourceSnapshotKey'],timeout_seconds=10)
        runtime_subject=snapshot.project_path
    current=prepare_goal_plan(runtime_subject,registration.goal)
    if (not current.get('ok') or any(current['binding']['identity'][key]!=registration.binding['identity'][key]
            for key in ('implementationDigest','toolchainDigest','policyDigest'))):
        raise GoalContractError('FINAL_IMPLEMENTATION_OR_TOOLCHAIN_CHANGED')
    if _completed_read:
        envelope=_json((attempt_root/'transaction.json').read_bytes(),8*1024*1024)
        payload=envelope.get('payload',{})
        if (envelope.get('sha256')!=canonical_digest(payload) or payload.get('phase')!='TERMINAL_PASS'
                or payload.get('authorizationDigest')!=canonical_digest(cap)):
            raise GoalContractError('FINAL_COMPLETED_CHECKPOINT_REQUIRED')
    grant=_FinalGrant()
    _GRANTED[grant]=FinalRegistration(registration,receipt,profile,
        canonical_digest(cap),cap['attemptId'],cap['budgetSeconds'],_completed_read)
    return grant


def consume_final_grant(grant):
    """A fresh call may recover a durable attempt; it cannot issue a new attempt."""
    if not isinstance(grant,_FinalGrant):raise GoalContractError('OPAQUE_FINAL_GRANT_REQUIRED')
    value=_GRANTED.pop(grant,None)
    if value is None:raise GoalContractError('OPAQUE_FINAL_GRANT_REQUIRED')
    return value
