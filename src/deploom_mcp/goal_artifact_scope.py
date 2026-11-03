"""Pinned host permission for one held-artifact copy; declared scope, not proof.

The existing frozen owner still performs fresh strict installation, full physical
instance/edge checks and every pinned impact check. This helper grants no apply
capability, metadata trust, currentness or successful execution on its own.
"""
from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.goal_check_bindings import validate_check_bindings, validate_normative_admission


def declared_host_artifact_scope(before, after, goal, policy, profile):
    if not isinstance(policy, dict) or 'heldArtifactPreservation' not in policy:
        return None
    if canonical_digest(policy) != goal['policyDigest']:
        raise GoalContractError('ARTIFACT_HOST_POLICY_BINDING_MISMATCH')
    from deploom_mcp.goal_execution_registry import FROZEN_ROUTE
    if (policy.get('transitiveChangePolicy') != 'EXACT_HOST_ENUMERATED'
            or policy.get('privateVerificationRoute') != FROZEN_ROUTE
            or policy.get('sharedInstanceTransitions') is not None):
        raise GoalContractError('ARTIFACT_FROZEN_EXACT_HOST_SCOPE_REQUIRED')
    if not isinstance(profile, dict):
        raise GoalContractError('ARTIFACT_IMPACT_PROFILE_REQUIRED')
    validate_check_bindings(profile, goal['requiredChecks'])
    admission = validate_normative_admission(profile, goal['requiredChecks'])
    from deploom_mcp.goal_check_bindings import admission_scope_digest
    if (admission is None or admission['scope'] != 'ISOLATED_NPM_V2_CANDIDATE'
            or admission['allowedChangeScopeDigest'] != admission_scope_digest(goal)):
        raise GoalContractError('ARTIFACT_FULL_TREE_ADMISSION_REQUIRED')
    permission = policy['heldArtifactPreservation']
    fields = {'schemaVersion', 'mode', 'beforeLockDigest', 'afterLockDigest',
              'preservations', 'impactCheckId', 'impactOracleSha256'}
    if not isinstance(permission, dict) or set(permission) != fields:
        raise GoalContractError('INVALID_HOST_ARTIFACT_PRESERVATION')
    # Goal binds policy and exact lock digests. Deriving these comparison-only
    # inputs avoids a recursive goalDigest -> policyDigest -> goalDigest claim.
    contract = {k: permission[k] for k in fields - {'impactCheckId', 'impactOracleSha256'}}
    try:
        old_root = before['packages']['']; new_root = after['packages']['']
        contract.update(beforeManifestDigest=canonical_digest(old_root),
                        afterManifestDigest=canonical_digest(new_root),
                        goalDigest=canonical_digest(goal))
    except (KeyError, TypeError) as exc:
        raise GoalContractError('INVALID_HOST_ARTIFACT_PRESERVATION') from exc
    from deploom_mcp.goal_lock_scope import evaluate_npm_artifact_preservation, _instance_identity
    comparison = evaluate_npm_artifact_preservation(before, after, goal, contract,
        before_manifest=old_root, after_manifest=new_root)
    if comparison['status'] != 'DECLARED_HELD_ARTIFACTS_PRESERVED_ONLY':
        raise GoalContractError('ARTIFACT_PRESERVATION_UNCONFIRMED', comparison['reason'])
    rows = [r for r in profile['checkBindings'] if r['checkId'] == permission['impactCheckId']]
    if len(rows) != 1:
        raise GoalContractError('ARTIFACT_IMPACT_CHECK_REQUIRED')
    row = rows[0]; oracle = row.get('oracle', {})
    subjects = set(row.get('normative', {}).get('purpose', {}).get('subjectPackages', []))
    owners = {_instance_identity(e['consumer'], before['packages'][e['consumer']])
              for e in comparison['affectedEdges']}
    packages = {e['package'] for e in comparison['affectedEdges']}
    if (row['kind'] != 'command' or oracle.get('mode') != 'INLINE_NODE_V1'
            or oracle.get('sha256') != permission['impactOracleSha256']
            or not (owners | packages).issubset(subjects)):
        raise GoalContractError('ARTIFACT_IMPACT_CHECK_BINDING_MISMATCH')
    return {'status': 'DECLARED_LOCK_CLOSURE_ONLY', 'necessityVerified': False,
            'missingEvidence': list(comparison['missingEvidence']),
            'changedInstances': comparison['changedInstances'],
            'compatibility': 'NOT_VERIFIED', 'sharedRewritePolicy': 'EXACT_HOST_ENUMERATED',
            'authorizedSharedInstances': [], 'declaredArtifactPreservation': comparison}
