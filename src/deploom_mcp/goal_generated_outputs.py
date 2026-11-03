"""Host-policy join for Core standalone output observations; no execution grant.

Private/final owners still validate the full Core command/tree/source evidence.
This adapter binds output observations and independently checks their physical
current state. It cannot convert imported JSON into verification authority.
"""
from __future__ import annotations
import hashlib,os
from pathlib import Path
from deploom_mcp.goal_contract import GoalContractError,canonical_digest

POLICY_KEY='standaloneOutputSpec'
PROFILE='standalone-js-cli-output-v1'
KIND='STANDALONE_JS_CLI_OUTPUT_V1'
# Actual project child mapping, not just a fingerprint projection. Core adds its
# fixed CI/frozen npm settings afterwards. Host MCP/auth/config keys stay out.
PROJECT_ENV_NAMES=frozenset({'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','PROCESSOR_ARCHITECTURE',
    'PROCESSOR_ARCHITEW6432','NUMBER_OF_PROCESSORS','OS','TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA'})

def project_host_environment(host):
    return {k:v for k,v in host.items() if k.upper() in PROJECT_ENV_NAMES}

def _expect(value,code):
    if not value:raise GoalContractError('STANDALONE_OUTPUT_'+code)

def validate_host_policy(policy,profile,project):
    spec=policy.get(POLICY_KEY)
    selected=os.environ.get('DEPLOOM_SOURCE_SCOPE_MODE')==PROFILE
    if spec is None:
        _expect(POLICY_KEY not in policy,'INVALID_SPEC')
        _expect(not selected,'HOST_POLICY_REQUIRED')
        return None
    _expect(selected,'SOURCE_PROFILE_REQUIRED')
    from deploom_mcp.goal_execution_environment import V2,environment_contract
    _expect(environment_contract(policy)==V2,'EXPLICIT_ENVIRONMENT_REQUIRED')
    _expect('tapNycInput' not in policy and 'toolCache' not in policy,'MIXED_PROFILE_UNSUPPORTED')
    from deploom_mcp.goal_delta_trial import _core
    _core()
    from standalone_build_outputs import validate_spec,GeneratedOutputError
    try:validate_spec(spec,Path(project),profile['commands'])
    except (GeneratedOutputError,OSError,KeyError,TypeError,ValueError) as exc:
        raise GoalContractError('STANDALONE_OUTPUT_RECIPE_UNPROVEN') from exc
    return spec

def verifier_arguments(policy,profile,project):
    spec=validate_host_policy(policy,profile,project)
    return {} if spec is None else {'generated_output_spec':spec,'environment':project_host_environment(os.environ)}

def expected_binding(raw):
    return {'sourceSnapshotKey':raw['sourceSnapshotKey'],'profileDigest':raw['finalVerificationProfileDigest'],
        'invocationId':raw['freshCommandInvocationId'],
        'dependencyPlanDigest':raw['materializedDependencyObligations']['obligationPlanDigest'],
        'approvedLockSha256':raw['resolvedLockfileHash'],'environmentDigest':raw['environmentDigest'],
        'toolchain':raw['materializedSourceOrigin']['toolchain'],
        'buildCommandDigest':raw['commandObservations'][0]['commandDigest']}

def observation_matches(policy,profile,raw):
    try:
        receipt=raw.get('generatedOutputObservation');spec=policy.get(POLICY_KEY)
        if spec is None:return POLICY_KEY not in policy and receipt is None
        commands=profile['commands']
        if commands[:2]!=['npm test','npm run test:perf']:return False
        fields={'schemaVersion','kind','recipeDigest','binding','inventory','inventoryDigest','authority','writerProvenanceVerified','arbitraryConcurrentFilesystemClosureVerified'}
        if (not isinstance(receipt,dict) or set(receipt)!=fields or type(receipt['schemaVersion']) is not int or receipt['schemaVersion']!=1
                or receipt['kind']!=KIND or receipt['recipeDigest']!=canonical_digest(spec)
                or receipt['binding']!=expected_binding(raw)
                or receipt['binding']['profileDigest']!=canonical_digest(profile)
                or receipt['binding']['buildCommandDigest']!=hashlib.sha256(commands[0].encode()).hexdigest()
                or not isinstance(receipt['inventory'],list) or not 1<=len(receipt['inventory'])<=512
                or receipt['inventoryDigest']!=canonical_digest(receipt['inventory'])
                or receipt['authority']!='NON_REUSABLE_BOUNDARY_OBSERVATION'
                or receipt['writerProvenanceVerified'] is not False or receipt['arbitraryConcurrentFilesystemClosureVerified'] is not False):return False
        return True
    except (KeyError,TypeError,ValueError,AttributeError):return False

def physically_current(policy,profile,project,raw,*,deadline):
    if not observation_matches(policy,profile,raw):return False
    if POLICY_KEY not in policy:return True
    try:
        spec=validate_host_policy(policy,profile,project)
        from standalone_build_outputs import receipt_current
        return receipt_current(project,spec,raw['generatedOutputObservation'],deadline=deadline,binding=expected_binding(raw))
    except (GoalContractError,OSError,KeyError,TypeError,ValueError,RuntimeError):return False

def checkpoint_identity(record):
    receipt=record.get('liveObservation',{}).get('generatedOutputObservation')
    return {} if receipt is None else {'generatedOutputObservationDigest':canonical_digest(receipt)}


def source_materialization_arguments(policy,profile,project):
    """Typed input-only copy contract; supplies no verification authority."""
    spec=validate_host_policy(policy,profile,project)
    return {} if spec is None else {'generated_output_materialization':{
        'spec':spec,'commands':list(profile['commands'])}}
