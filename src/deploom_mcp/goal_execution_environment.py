"""Trusted goal policy selects an explicit Core project-child environment contract."""
import os,re
from pathlib import Path
from deploom_mcp.goal_contract import GoalContractError

LEGACY='LEGACY_INHERITED_ENV_V1'
V2='HOST_OWNED_PROJECT_EXECUTION_ENV_V1'


def environment_contract(policy):
    mode=policy.get('projectExecutionEnvironment',LEGACY)
    if not isinstance(mode,str) or mode not in {LEGACY,V2}:raise GoalContractError('PROJECT_ENVIRONMENT_POLICY_UNSUPPORTED')
    if mode==V2 and policy.get('privateVerificationRoute')!='FROZEN_EXACT_SINGLE_MATERIALIZATION_V1':
        raise GoalContractError('PROJECT_ENVIRONMENT_EXPLICIT_OWNER_REQUIRED')
    return mode


def core_environment_arguments(policy):
    mode=environment_contract(policy)
    return {} if mode==LEGACY else {'environment_contract':mode}


def workspace_environment(policy,cache_dir):
    from deploom_mcp.goal_delta_trial import _core
    _core()
    from project_execution_environment import workspace_execution_environment
    host=os.environ
    if 'standaloneOutputSpec' in policy:
        from deploom_mcp.goal_generated_outputs import project_host_environment
        host=project_host_environment(host)
    return workspace_execution_environment(host,Path(cache_dir).absolute().resolve(),contract=environment_contract(policy))


def environment_observation_matches_contract(policy,observation):
    mode=environment_contract(policy);record=observation.get('projectExecutionEnvironment')
    if mode==LEGACY:return record is None
    from deploom_mcp.goal_delta_trial import _core
    _core()
    from project_execution_environment import native_launch_observation_matches
    operations=observation.get('ownerOperationObservations');commands=observation.get('commandObservations')
    installation=observation.get('installation')
    if not isinstance(installation,dict):return False
    installation=installation.get('status')
    if not isinstance(operations,list) or not isinstance(commands,list) or installation not in {'FRESH_COLD_FROZEN_INSTALL_OBSERVED','EXISTING_TREE_PHYSICALLY_OBSERVED'}:return False
    from deploom_mcp.goal_tool_cache import expected_environment_digests
    try:expected=expected_environment_digests(policy,observation)
    except (ValueError,RuntimeError,KeyError,TypeError,OSError):return False
    if observation.get('commandEnvironmentDigests',{})!=expected:return False
    count=len(commands)+(1 if installation=='FRESH_COLD_FROZEN_INSTALL_OBSERVED' else 0)
    if not count or len(operations)!=count or any(not isinstance(row,dict) or not native_launch_observation_matches(row.get('projectExecutionEnvironment'),expected.get(str(row.get('commandIndex')),observation.get('environmentDigest')),observation.get('workspacePathDigest')) for row in operations):return False
    return (isinstance(record,dict) and set(record)=={'schemaVersion','contract','origin','environmentDigest',
        'removedTransportNames','transportValueForwarded','arbitraryDescendantEnvironmentClosureVerified'}
        and type(record['schemaVersion']) is int and record['schemaVersion']==2
        and record['contract']==V2 and record['origin']=='EXPLICIT_OWNER_CHILD_MAPPING'
        and isinstance(record['environmentDigest'],str) and re.fullmatch('[0-9a-f]{32}',record['environmentDigest']) is not None
        and record['environmentDigest']==observation.get('environmentDigest')
        and record['removedTransportNames']==['CODEX_TOOL_CALL_ID'] and record['transportValueForwarded'] is False
        and record['arbitraryDescendantEnvironmentClosureVerified'] is False)


def environment_matches(policy,observation):
    if not environment_observation_matches_contract(policy,observation):return False
    from deploom_mcp.goal_delta_trial import _core
    _core()
    from project_execution_environment import project_environment_fingerprint
    cache=Path(os.environ['DEPLOOM_MCP_OFFLINE_CACHE'])
    if 'tapNycInput' in policy:
        from deploom_mcp.goal_tap_nyc_execution import child_environment
        env=child_environment(policy,cache,'final')
    else:env=workspace_environment(policy,cache)
    return project_environment_fingerprint(env,contract=environment_contract(policy))==observation['environmentDigest']
