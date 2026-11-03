"""Recorded choice provenance only; no solver, fresh-plan check or execution grant.

The configured host store is trusted, not cryptographically attested. Legacy
issuance records have neither issuedAt nor a serialized model. Their hash-bound
assignment is an owner record, never a new mathematical optimality proof.
"""
from __future__ import annotations
import math
from datetime import datetime, timezone
from deploom_mcp.goal_contract import GoalContractError, bind_goal, canonical_digest
from deploom_mcp.goal_choice_contract import validate_choice_goal
from deploom_mcp.goal_choice_lineage import derive
from deploom_mcp.goal_delta_trial import _json
from deploom_mcp.goal_report import _plain_under, _SHA


def _now():
    return datetime.now(timezone.utc)


def _artifact(root, folder, digest):
    if not isinstance(digest, str) or not _SHA.fullmatch(digest):
        raise GoalContractError('INVALID_CHOICE_DIGEST')
    import hashlib
    path = _plain_under(root / folder / (digest + '.json'), root)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise GoalContractError('CHOICE_ARTIFACT_TOO_LARGE')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise GoalContractError('CHOICE_ARTIFACT_CHANGED')
    return _json(raw, 8 * 1024 * 1024)


def validate_recorded_lineage(root, goal, binding, policy, profile, transaction_started_at):
    """Validate frozen record joins, not current Core/model/metadata suitability."""
    wrapper = policy.get('coupledChoice')
    if 'coupledChoice' not in policy:
        return None
    fields = {'schemaVersion', 'selectionDigest', 'parentGoalDigest',
              'parentBindingDigest', 'metadataDigest', 'issuanceDigest'}
    if (not isinstance(wrapper, dict) or set(wrapper) != fields
            or type(wrapper['schemaVersion']) is not int or wrapper['schemaVersion'] != 1):
        raise GoalContractError('INVALID_COUPLED_LINEAGE')
    receipt = _artifact(root, 'choice-results', wrapper['selectionDigest'])
    if set(receipt) != {'schemaVersion', 'kind', 'parentBinding', 'currentRoots',
                        'assignment', 'score', 'modelDigest', 'status', 'backend'}:
        raise GoalContractError('INVALID_SELECTION_RECEIPT')
    parent_binding = receipt['parentBinding']
    parent = validate_choice_goal(parent_binding['goal'])
    if bind_goal(parent, parent_binding['identity']) != parent_binding:
        raise GoalContractError('INVALID_PARENT_BINDING')
    if (type(receipt['schemaVersion']) is not int or receipt['schemaVersion'] != 1
            or receipt['kind'] != 'HOST_PRODUCED_ROOT_CHOICE_V1'
            or receipt['status'] != 'optimal' or receipt['backend'] != 'z3'):
        raise GoalContractError('SELECTION_NOT_FULL_OPTIMAL')
    from deploom_mcp.goal_choice_issuance import validate_issuance
    # Strict duplicate-key decoding precedes the existing owner schema validator.
    path = _plain_under(root / 'choice-issuance' / (wrapper['selectionDigest'] + '.json'), root)
    issuance = _json(path.read_bytes(), 256 * 1024)
    if (canonical_digest(issuance) != wrapper['issuanceDigest']
            or validate_issuance(root, receipt, wrapper['selectionDigest']) != wrapper['issuanceDigest']):
        raise GoalContractError('CORE_SELECTION_ISSUANCE_CHANGED')
    inputs = _artifact(root, 'choice-inputs', parent['metadataDigest'])
    if (set(inputs) != {'schemaVersion', 'kind', 'createdUTC', 'expiresUTC', 'basePolicy', 'records'}
            or type(inputs['schemaVersion']) is not int or inputs['schemaVersion'] != 1
            or inputs['kind'] != 'HOST_PINNED_ROOT_METADATA_V1'
            or not isinstance(inputs['basePolicy'], dict) or 'coupledChoice' in inputs['basePolicy']
            or inputs['basePolicy'].get('hardPolicyRequired') is not False
            or canonical_digest(inputs['basePolicy']) != parent['policyDigest']
            or not isinstance(inputs['records'], dict)):
        raise GoalContractError('INVALID_CHOICE_INPUT_BINDING')
    try:
        start = datetime.fromisoformat(inputs['createdUTC'])
        end = datetime.fromisoformat(inputs['expiresUTC'])
        if (type(transaction_started_at) not in (int, float) or not math.isfinite(transaction_started_at)
                or start.tzinfo is None or end.tzinfo is None):
            raise ValueError()
        observed = datetime.fromtimestamp(transaction_started_at, timezone.utc)
        now = _now()
        if not start <= observed < end or not observed <= now or not 0 < (end-start).total_seconds() <= 86400:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise GoalContractError('HISTORICAL_METADATA_TIME_UNPROVEN') from None
    parent_profile = None
    if 'verificationContext' in parent:
        parent_profile = _artifact(root, 'choice-profiles', parent['verificationContext']['profileDigest'])
    derived = derive(receipt, inputs, wrapper['selectionDigest'], wrapper['issuanceDigest'], parent_profile)
    if (derived['policy'] != policy or derived['goal'] != goal or derived['binding'] != binding
            or ('executionProfile' in derived and derived['executionProfile'] != profile)):
        raise GoalContractError('PARENT_TO_EXACT_LINEAGE_MISMATCH')
    return {'selectionDigest': wrapper['selectionDigest'], 'issuanceDigest': wrapper['issuanceDigest'],
            'metadataDigest': wrapper['metadataDigest'], 'parentBindingDigest': parent_binding['bindingDigest'],
            'recordedModelDigest': receipt['modelDigest'],
            'mathematicalModelReevaluation': 'NOT_PERFORMED', 'originalIssuanceTime': 'NOT_RECORDED',
            'issuanceTimeMetadataValidity': 'NOT_ESTABLISHED',
            'transactionTimeMetadataWindow': 'MATCHES_RECORDED_WINDOW',
            'metadataFreshness': 'EXPIRED' if now >= end else 'WITHIN_RECORDED_WINDOW'}