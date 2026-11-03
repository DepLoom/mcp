"""Explicit host resource class; no new execution or verification authority."""
from dataclasses import dataclass
from deploom_mcp.goal_contract import GoalContractError, canonical_digest


@dataclass(frozen=True)
class FinalResourceSpec:
    final_seconds: int = 1800
    worker_seconds: int = 1200
    return_seconds: int = 60

    def as_policy(self):
        return {'schemaVersion': 2, 'resourceClass': 'LARGE_PHYSICAL_TREE_CURRENT',
                'finalMaxSeconds': self.final_seconds, 'workerMaxSeconds': self.worker_seconds,
                'returnHeadroomSeconds': self.return_seconds}


def parse_final_resource_policy(policy):
    if not isinstance(policy, dict):
        raise GoalContractError('INVALID_EXECUTION_POLICY')
    if 'finalResources' not in policy:
        return None
    value = policy['finalResources']
    expected = FinalResourceSpec().as_policy()
    if (not isinstance(value, dict) or set(value) != set(expected)
            or any(type(value[key]) is not type(wanted) or value[key] != wanted
                   for key, wanted in expected.items())):
        raise GoalContractError('INVALID_FINAL_RESOURCE_POLICY')
    proof = policy.get('currentProof')
    if (not isinstance(proof, dict) or set(proof) != {'schemaVersion', 'scope', 'maxSeconds'}
            or type(proof.get('schemaVersion')) is not int or proof['schemaVersion'] != 2
            or proof.get('scope') != 'CURRENT_PHYSICAL_READ'
            or type(proof.get('maxSeconds')) is not int or not 1 <= proof['maxSeconds'] <= 300):
        raise GoalContractError('FINAL_RESOURCE_CURRENT_PROOF_REQUIRED')
    return FinalResourceSpec()


def registered_final_resource_policy(registration):
    policy = getattr(registration, 'policy', {})
    spec = parse_final_resource_policy(policy)
    if spec is None:
        return None
    if (getattr(registration, 'delta_authorization', None) or {}).get('origin') != 'PREAPPROVED_HOST_POLICY':
        raise GoalContractError('FINAL_RESOURCE_HOST_POLICY_REQUIRED')
    digest = canonical_digest(policy)
    if (registration.goal.get('policyDigest') != digest
            or registration.binding.get('identity', {}).get('policyDigest') != digest):
        raise GoalContractError('FINAL_RESOURCE_POLICY_BINDING_MISMATCH')
    return spec
