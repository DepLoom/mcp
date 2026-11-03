"""Adapter from pinned host policy/check profile to Core exact shared scope.

Only goal_delta_trial supplies these inputs from the trusted host registry. No
MCP request field can grant shared writes. A successful comparison is not proof.
"""
import sys
from deploom_mcp.goal_contract import GoalContractError,canonical_digest,_roots
from deploom_mcp.goal_check_bindings import validate_check_bindings
from deploom_mcp.distribution import resolve_core_dir

def validate_host_shared_scope(before,after,goal,policy,profile):
    if policy is None:return set()
    if not isinstance(policy,dict) or canonical_digest(policy)!=goal['policyDigest']:
        raise GoalContractError('SHARED_HOST_POLICY_BINDING_MISMATCH')
    contract=policy.get('sharedInstanceTransitions')
    if contract is None:return set()
    if policy.get('transitiveChangePolicy')!='EXACT_HOST_ENUMERATED':
        raise GoalContractError('SHARED_EXACT_HOST_AUTHORIZATION_REQUIRED')
    if not isinstance(profile,dict):raise GoalContractError('SHARED_IMPACT_PROFILE_REQUIRED')
    validate_check_bindings(profile,goal['requiredChecks'])
    sys.path.insert(0,str(resolve_core_dir()))
    from npm_shared_transition import validate_shared_transitions,SharedTransitionError
    held=set(_roots(before['packages']['']))-set(goal['exactTargets'])
    try:paths=validate_shared_transitions(before,after,goal['exactTargets'],held,
        set(goal['immutablePackages'])|set(goal['excludedPackages']),contract)
    except SharedTransitionError as exc:raise GoalContractError(str(exc)) from exc
    for transition in contract['transitions']:
        matches=[row for row in profile.get('checkBindings',[]) if row.get('checkId')==transition['impactCheckId']]
        if len(matches)!=1:raise GoalContractError('SHARED_IMPACT_CHECK_REQUIRED')
        row=matches[0];oracle=row.get('oracle',{});subjects=set(row.get('normative',{}).get('purpose',{}).get('subjectPackages',[]))
        owners={path.removeprefix('node_modules/') for path in transition['heldOwners']}
        if (row.get('kind')!='command' or oracle.get('mode')!='INLINE_NODE_V1'
                or oracle.get('sha256')!=transition['impactOracleSha256']
                or not (owners|{transition['instance'].removeprefix('node_modules/')}).issubset(subjects)):
            raise GoalContractError('SHARED_IMPACT_CHECK_BINDING_MISMATCH')
    return paths
