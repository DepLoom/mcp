"""Join one typed TAP input lease to actual Core command observations.

This adapter does not grant execution or acceptance. Host registry and Core
profile/tree/source checks remain authoritative; callers retain raw receipts.
"""
import hashlib,json,os
from contextlib import ExitStack
from deploom_mcp import goal_tool_cache as tool_cache
from pathlib import Path
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_tap_nyc_input import (KIND,validate_policy,held_input,seal_observation,current_matches,_API)


def _expect(value,code):
    if not value:raise GoalContractError('TAP_NYC_'+code)


def validate_profile(policy,profile):
    value=validate_policy(policy['tapNycInput'])
    from deploom_mcp.goal_execution_environment import V2,environment_contract
    _expect(environment_contract(policy)==V2,'EXPLICIT_ENVIRONMENT_REQUIRED')
    commands=profile.get('commands',[])
    _expect(commands.count('npm run ci')==1,'EXACT_CI_COMMAND_REQUIRED')
    ci=commands.index('npm run ci')+1;found=[]
    for row in profile.get('checkBindings',[]):
        expected=tool_cache.declared(policy,value)
        wanted=expected if row.get('kind')=='command' and row.get('commandIndex')==ci else 'NONE_DECLARED_BY_HOST'
        inputs=row.get('normative',{}).get('allowedInputs',{})
        _expect(inputs.get('externalInputs')==wanted,'NORMATIVE_INPUT_MISMATCH')
        if wanted==expected:
            _expect(not row.get('oracle') and inputs.get('oracle')=='SEALED_PROJECT_COMMAND','SEALED_COMMAND_REQUIRED');found.append(row)
    _expect(bool(found),'NORMATIVE_CI_BINDING_REQUIRED')
    return value


def child_environment(policy,cache,role):
    from deploom_mcp.goal_execution_environment import workspace_environment,V2
    from project_execution_environment import project_execution_environment
    value=validate_policy(policy['tapNycInput'])
    _expect(role in ('private','final'),'ROLE')
    return project_execution_environment(workspace_environment(policy,cache),contract=V2,
        overrides={'TAP_RCFILE':value['lanes'][role]['yaml']['path']})


def _operations_match(raw,profile,project,environment_digest,host_policy=None):
    from deploom_mcp.goal_execution_environment import environment_observation_matches_contract,V2
    policy={'projectExecutionEnvironment':V2,'privateVerificationRoute':'FROZEN_EXACT_SINGLE_MATERIALIZATION_V1'}
    if not environment_observation_matches_contract(host_policy or policy,raw):return False
    if (raw.get('environmentDigest')!=environment_digest or raw.get('workspacePathDigest')!=canonical_digest(str(Path(project).resolve()))
        or raw.get('finalVerificationProfileDigest')!=canonical_digest(profile)):return False
    commands=raw.get('commandObservations',[])
    if len(commands)!=len(profile['commands']):return False
    for i,(command,row) in enumerate(zip(profile['commands'],commands),1):
        if (type(row.get('commandIndex')) is not int or row['commandIndex']!=i or row.get('commandDigest')!=hashlib.sha256(command.encode()).hexdigest()
            or row.get('status')!='PASS' or type(row.get('returnCode')) is not int or row['returnCode']!=0):return False
    operations=raw['ownerOperationObservations'];offset=1 if raw['installation']['status']=='FRESH_COLD_FROZEN_INSTALL_OBSERVED' else 0
    for i,row in enumerate(operations):
        if (row.get('operation')!=('strict-frozen-install' if offset and i==0 else 'project-command')
            or row.get('commandIndex')!=(None if offset and i==0 else i+1-offset)
            or (not (offset and i==0) and type(row.get('commandIndex')) is not int)
            or type(row.get('returnCode')) is not int or row['returnCode']!=0
            or row.get('supervision')!='guaranteed-tree' or type(row.get('descendantsRemaining')) is not int
            or row['descendantsRemaining']!=0 or row.get('timedOut') is not False or row.get('cancelled') is not False):return False
    return True


def verify_with_tap_input(verifier,policy,profile,role,project,**arguments):
    from deploom_mcp.goal_execution_environment import core_environment_arguments,V2
    _expect('toolCache' not in policy or 'tapNycInput' in policy,'HOST_POLICY_REQUIRED')
    if 'tapNycInput' not in policy:
        from deploom_mcp.goal_generated_outputs import verifier_arguments
        return verifier(project,**arguments,**core_environment_arguments(policy),**verifier_arguments(policy,profile,project)),None
    value=validate_profile(policy,profile);env=child_environment(policy,arguments['cache_dir'],role)
    from project_execution_environment import project_environment_fingerprint
    digest=project_environment_fingerprint(env,contract=V2);deadline=arguments['deadline']
    feature=tool_cache.validate(policy);ci_env=tool_cache.ci_environment(policy,env,role);ci_digest=project_environment_fingerprint(ci_env,contract=V2)
    options={'command_cache_directories':{profile['commands'].index('npm run ci')+1:value['lanes'][role]['outputs']['cache']['path']}} if feature else {}
    _expect(tuple(arguments['commands'])==tuple(profile['commands']) and arguments['final_profile_digest']==canonical_digest(profile),'PROFILE_ARGUMENT_MISMATCH')
    # Reserve once BEFORE entering the verifier. A failed run consumes the slot;
    # no cleanup/resealing converts an interrupted run into an unused attempt.
    marker=Path(value['lanes'][role]['yaml']['path']+'.consumed.json')
    payload={'kind':'TAP_NYC_SINGLE_USE_EXECUTION_SLOT_RESETTABLE_V2','role':role,'profileDigest':canonical_digest(profile),
        'cwdDigest':canonical_digest(str(Path(project).resolve())),'recipeHash':value['recipeHash'],'environmentDigest':ci_digest}
    if feature:payload['toolCacheRecipeHash']=feature['recipeHash']
    with ExitStack() as consumers,held_input(value,project,role,canonical_digest(profile),deadline=deadline,seal_environment_digest=ci_digest) as observation:
        if feature:observation['toolCacheBinding']=tool_cache.prospective(policy,profile,role,digest)
        try:
            with marker.open('xb') as f:f.write(json.dumps(payload,sort_keys=True,separators=(',',':')).encode());f.flush();os.fsync(f.fileno())
        except FileExistsError as exc:raise GoalContractError('TAP_NYC_SLOT_ALREADY_CONSUMED') from exc
        api=_API(deadline);handle=api.open(marker,readonly_file=True)
        try:
            slot=api.file(handle)
            from deploom_mcp.goal_tap_nyc_spawn import make_guard
            guard,spawn=make_guard(value,profile,role,project,observation,env,deadline,arguments.get('materialize',False),ci_environment=ci_env,tool_policy=policy if feature else None,consumer_leases=consumers)
            raw=verifier(project,**arguments,environment=env,environment_contract=V2,trusted_pre_popen_guard=guard,**options)
            _expect(_operations_match(raw,profile,project,digest,policy),'ACTUAL_EXECUTION_JOIN_MISMATCH')
            _expect(api.file(handle)==slot,'SLOT_CHANGED')
            _expect(spawn['target'] is not None and len(spawn['seen'])==len(raw['ownerOperationObservations']),
                'NATIVE_SPAWN_WITNESS_MISSING')
        finally:api.close(handle)
    observation=seal_observation(observation,ci_digest)
    if feature:_expect(tool_cache.namespace_matches(observation['outputReceipt']),'TOOL_CACHE_NAMESPACE_UNPROVEN')
    from deploom_mcp.goal_tap_nyc_spawn import witness_matches
    _expect(witness_matches(raw,profile,role,observation),'NATIVE_SPAWN_WITNESS_INVALID')
    target=next(r['guardedSpawnWitness'] for r in raw['ownerOperationObservations'] if r.get('guardedSpawnWitness'))
    _expect(all(target[k]==v for k,v in spawn['target'].items()),'NATIVE_SPAWN_WITNESS_INVALID')
    return raw,{'schemaVersion':3 if feature else 2,'inputObservation':observation,'slot':{'path':str(marker),'file':slot},'coreObservationDigest':canonical_digest(raw)}


def receipt_matches(policy,profile,role,project,raw,receipt):
    try:
        if 'tapNycInput' not in policy:return receipt is None and 'toolCache' not in policy
        value=validate_profile(policy,profile);r=receipt;feature=tool_cache.validate(policy)
        if not isinstance(r,dict) or set(r)!={'schemaVersion','inputObservation','slot','coreObservationDigest'} or type(r['schemaVersion']) is not int or r['schemaVersion']!=(3 if feature else 2):return False
        if not isinstance(raw,dict) or not isinstance(r['inputObservation'],dict) or not isinstance(r['slot'],dict):return False
        o=r['inputObservation'];lane=value['lanes'][role]
        if o.get('schemaVersion')!=2 or o.get('kind')!=KIND or o.get('outputReceipt',{}).get('schemaVersion')!=2:return False
        payload={'kind':'TAP_NYC_SINGLE_USE_EXECUTION_SLOT_RESETTABLE_V2','role':role,'profileDigest':canonical_digest(profile),
            'cwdDigest':canonical_digest(str(Path(project).resolve())),'recipeHash':value['recipeHash'],'environmentDigest':o.get('actualEnvironmentDigest')}
        if feature:payload['toolCacheRecipeHash']=feature['recipeHash']
        if not tool_cache.binding_matches(policy,profile,role,o.get('toolCacheBinding'),raw['environmentDigest']):return False
        if feature and not tool_cache.namespace_matches(o['outputReceipt']):return False
        slot_bytes=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
        slot_file=r['slot'].get('file',{})
        if (set(r['slot'])!={'path','file'} or not isinstance(slot_file,dict)
            or set(slot_file)!={'volumeSerial','fileId','sha256','size'}
            or slot_file.get('sha256')!=hashlib.sha256(slot_bytes).hexdigest() or slot_file.get('size')!=len(slot_bytes)) :return False
        if (o.get('role')!=role or o.get('recipeHash')!=value['recipeHash'] or o.get('profileDigest')!=canonical_digest(profile)
            or o.get('cwdDigest')!=canonical_digest(str(Path(project).resolve())) or o.get('executionAuthority')!='NONE' or o.get('fullAcceptance') is not False
            or o.get('yamlPath')!=lane['yaml']['path'] or o.get('outputs')!=lane['outputs'] or o.get('ancestryDigest')!=lane['ancestryDigest']
            or o.get('sourceConfig',{}).get('sha256')!=value['sourceSha256']
            or o.get('yaml',{}).get('sha256')!=lane['yaml']['sha256'] or o.get('yaml',{}).get('size')!=lane['yaml']['size']
            or any(o.get('yaml',{}).get(k)!=v for k,v in lane['yaml']['identity'].items())
            or seal_observation({k:v for k,v in o.items() if k!='inputBindingHash'},o['actualEnvironmentDigest'])!=o
            or r['slot'].get('path')!=lane['yaml']['path']+'.consumed.json'
            or r['coreObservationDigest']!=canonical_digest(raw)):return False
        from deploom_mcp.goal_tap_nyc_spawn import witness_matches
        return _operations_match(raw,profile,project,raw['environmentDigest'] if feature else o['actualEnvironmentDigest'],policy) and witness_matches(raw,profile,role,o)
    except (KeyError,TypeError,ValueError,RuntimeError,OSError):return False


def physically_current(policy,profile,project,raw,receipt,*,deadline):
    if 'tapNycInput' not in policy:return receipt is None
    if not receipt_matches(policy,profile,'final',project,raw,receipt):return False
    try:
        api=_API(deadline);h=api.open(Path(receipt['slot']['path']),readonly_file=True)
        try:
            with ExitStack() as leases:
                binding=receipt['inputObservation'].get('toolCacheBinding')
                if tool_cache.validate(policy):leases.enter_context(tool_cache.held_consumers(policy,project,deadline=deadline,expected=binding['consumerIdentities']))
                return api.file(h)==receipt['slot']['file'] and current_matches(policy['tapNycInput'],project,receipt['inputObservation'],deadline=deadline,extra_binding=binding)
        finally:api.close(h)
    except (KeyError,TypeError,ValueError,RuntimeError,OSError):return False
