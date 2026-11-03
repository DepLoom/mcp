"""Rebind profile identities for the explicit parent-to-exact policy transition.

Commands, check mappings and normative requirements are immutable. Only the
admission scope digest and baseline identity hashes change by construction.
"""
import copy
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_check_bindings import (admission_scope_digest,initial_verification_context,
    validate_check_bindings,validate_verification_context)

def load_parent_profile(root,parent,identity):
    if 'verificationContext' not in parent:return None
    from deploom_mcp.goal_choice_lineage import _artifact
    profile=_artifact(root,'choice-profiles',parent['verificationContext']['profileDigest'])
    validate_check_bindings(profile,parent['requiredChecks'])
    validate_verification_context(parent,profile,identity)
    return profile

def rebind_profile(parent,parent_identity,child,child_identity,profile):
    if profile is None:raise GoalContractError('COUPLED_PARENT_PROFILE_REQUIRED')
    validate_check_bindings(profile,parent['requiredChecks'])
    validate_verification_context(parent,profile,parent_identity)
    derived=copy.deepcopy(profile)
    if 'admission' in derived:
        derived['admission']['allowedChangeScopeDigest']=admission_scope_digest(child)
    value=copy.deepcopy(child)
    value['verificationContext']=initial_verification_context(derived,child_identity)
    validate_verification_context(value,derived,child_identity)
    return value,derived

def recover_parent_profile(parent,derived):
    if not isinstance(derived,dict):raise GoalContractError('COUPLED_PARENT_PROFILE_REQUIRED')
    original=copy.deepcopy(derived)
    if 'admission' in original:
        original['admission']['allowedChangeScopeDigest']=admission_scope_digest(parent)
    if canonical_digest(original)!=parent['verificationContext']['profileDigest']:
        raise GoalContractError('COUPLED_PROFILE_REQUIREMENTS_CHANGED')
    return original
