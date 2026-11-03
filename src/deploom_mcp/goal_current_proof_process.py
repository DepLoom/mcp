"""Killable independent physical reader and pre-mutation time reservation.

No project commands, writer lease, publication or renewed capability is created.
The child reopens the pinned host registry and the owning sealed evidence path.
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.goal_current_proof_budget import (
    CurrentProofBudget, registered_current_proof_spec, reserved_worker_timeout,
)
from deploom_mcp.process_runner import run_process_async


@dataclass(frozen=True)
class PendingCurrentReplay:
    registration: object
    terminal_identity: dict | None = None


def terminal_read_identity(registration, expected=None):
    """Pin the exact sealed terminal observation across the process boundary."""
    from deploom_mcp.goal_execution_registry import configured_state_root
    from deploom_mcp.goal_report import _plain_under
    from deploom_mcp.goal_delta_trial import _json
    cap=registration.final_authorization
    from deploom_mcp.goal_report import _RUN
    if not isinstance(cap,dict) or not isinstance(cap.get('attemptId'),str) or not _RUN.fullmatch(cap['attemptId']):
        raise GoalContractError('FINAL_TERMINAL_READ_IDENTITY_CHANGED')
    key=canonical_digest({'runId':registration.run_id,'candidateId':registration.candidate_id,'attemptId':cap['attemptId']})
    root=configured_state_root().resolve()
    path=_plain_under(root/'final-apply-attempts'/key/'transaction.json',root)
    envelope=_json(path.read_bytes(),8*1024*1024)
    if not isinstance(envelope,dict):raise GoalContractError('FINAL_TERMINAL_READ_IDENTITY_CHANGED')
    record=envelope.get('payload')
    if (set(envelope)!={'schemaVersion','payload','sha256'} or type(envelope['schemaVersion']) is not int
            or envelope['schemaVersion']!=1 or not isinstance(record,dict)
            or envelope['sha256']!=canonical_digest(record) or record.get('phase')!='TERMINAL_PASS'
            or record.get('runId')!=registration.run_id or record.get('candidateId')!=registration.candidate_id
            or record.get('attemptKey')!=key or record.get('attemptId')!=cap['attemptId']
            or record.get('bindingDigest')!=registration.binding['bindingDigest']
            or record.get('authorizationDigest')!=canonical_digest(cap)):
        raise GoalContractError('FINAL_TERMINAL_READ_IDENTITY_CHANGED')
    identity={'attemptId':record['attemptId'],'authorizationDigest':record['authorizationDigest'],
              'journalDigest':envelope['sha256'],'checkpointId':record.get('checkpointId'),
              'checkpointDigest':record.get('checkpointDigest')}
    from deploom_mcp.goal_report import _SHA
    if any(not isinstance(identity[k],str) or not _SHA.fullmatch(identity[k])
           for k in ('authorizationDigest','journalDigest','checkpointId','checkpointDigest')):
        raise GoalContractError('FINAL_TERMINAL_READ_IDENTITY_CHANGED')
    if expected is not None and (not isinstance(expected,dict) or identity!=expected):
        raise GoalContractError('FINAL_TERMINAL_READ_IDENTITY_CHANGED')
    return identity


def assert_terminal_read_result(result,expected):
    if (result.get('attemptId')!=expected['attemptId']
            or result.get('checkpointId')!=expected['checkpointId']
            or result.get('checkpointDigest')!=expected['checkpointDigest']):
        raise GoalContractError('FINAL_TERMINAL_READ_IDENTITY_CHANGED')


async def supervised_current_read(run_id, candidate_id, binding_digest, registration,
                                  *, original_deadline_utc=None,expected_terminal=None):
    spec = registered_current_proof_spec(registration)
    if spec is None:
        raise GoalContractError('CURRENT_PROOF_SPEC_REQUIRED')
    budget = CurrentProofBudget(spec, original_deadline_utc=original_deadline_utc)
    deadline = budget.wall_deadline()
    command=[sys.executable, '-B', '-m', 'deploom_mcp.goal_final_worker',
             'read-budgeted', run_id, candidate_id, binding_digest, repr(deadline)]
    if expected_terminal is not None:
        terminal_read_identity(registration,expected_terminal)
        command.append(json.dumps(expected_terminal,sort_keys=True,separators=(',',':')))
    process = await run_process_async(
        command,
        cwd=str(Path(__file__).parent), env=dict(os.environ),
        timeout=budget.remaining(), max_output_bytes=65536)
    budget.remaining()
    if process.timed_out:
        raise GoalContractError('CURRENT_PROOF_PROCESS_TIMEOUT')
    payload = json.loads(process.stdout)
    if not isinstance(payload,dict) or process.returncode or payload.get('ok') is not True:
        raise GoalContractError('CURRENT_PROOF_PROCESS_UNCONFIRMED')
    result = payload.get('result')
    if not isinstance(result, dict) or canonical_digest(result) != payload.get('resultDigest'):
        raise GoalContractError('CURRENT_PROOF_RECEIPT_MISMATCH')
    if expected_terminal is not None:
        terminal_read_identity(registration,expected_terminal)
        assert_terminal_read_result(result,expected_terminal)
    budget.remaining()
    return result


def final_worker_reservation(context, now):
    """Bind reservation to original operation and any existing durable attempt."""
    from deploom_mcp.goal_execution_registry import configured_state_root
    from deploom_mcp.goal_report import _plain_under
    from deploom_mcp.goal_delta_trial import _json
    reg = context.registration
    spec = registered_current_proof_spec(reg)
    if spec is None:
        return None
    deadlines = [reg.deadline, now + context.budget_seconds]
    key = canonical_digest({'runId': reg.run_id, 'candidateId': reg.candidate_id,
                            'attemptId': context.attempt_id})
    root = configured_state_root().resolve()
    path = _plain_under(root / 'final-apply-attempts' / key / 'transaction.json', root)
    if path.exists():
        envelope = _json(path.read_bytes(), 8 * 1024 * 1024)
        record = envelope.get('payload')
        if (set(envelope) != {'schemaVersion', 'payload', 'sha256'}
                or type(envelope['schemaVersion']) is not int or envelope['schemaVersion'] != 1
                or not isinstance(record, dict) or canonical_digest(record) != envelope['sha256']
                or record.get('attemptKey') != key or record.get('attemptId') != context.attempt_id
                or record.get('runId') != reg.run_id or record.get('candidateId') != reg.candidate_id
                or record.get('bindingDigest') != reg.binding['bindingDigest']
                or record.get('authorizationDigest') != context.authorization_digest
                or type(record.get('startedAt')) not in (int, float)
                or not math.isfinite(record['startedAt'])
                or type(record.get('deadline')) not in (int, float)
                or record['deadline'] != min(reg.deadline, record['startedAt'] + context.budget_seconds)):
            raise GoalContractError('FINAL_TRANSACTION_IDENTITY_CHANGED')
        deadlines.append(record['deadline'])
    from deploom_mcp.goal_final_resource_policy import registered_final_resource_policy
    resources = registered_final_resource_policy(reg)
    timeout = reserved_worker_timeout(spec, now=now, original_deadlines=deadlines,
        worker_ceiling=resources.worker_seconds if resources else 600,
        return_headroom_seconds=resources.return_seconds if resources else 60)
    return {'workerTimeout': timeout, 'originalDeadlineUTC': min(deadlines)}
