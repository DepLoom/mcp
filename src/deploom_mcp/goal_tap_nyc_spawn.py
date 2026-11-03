"""One trusted code guard for pinned TAP CI at the native spawn boundary."""
import hashlib,json,subprocess,re
from pathlib import Path
from deploom_mcp.goal_contract import canonical_digest,GoalContractError
from deploom_mcp.goal_tap_nyc_input import held_input


def _expect(value):
    if not value:raise GoalContractError('TAP_NYC_PRE_POPEN_INPUT_REJECTED')


def make_guard(value,profile,role,project,entry,environment,deadline,materialize,*,ci_environment=None,tool_policy=None,consumer_leases=None):
    from project_execution_environment import project_environment_fingerprint,V2
    env_digest=project_environment_fingerprint(environment,contract=V2);profile_digest=canonical_digest(profile)
    target=profile['commands'].index('npm run ci')+1;cwd=str(Path(project).resolve())
    sequence=([None] if materialize else [])+list(range(1,len(profile['commands'])+1));state={'seen':[],'target':None,'invocationId':None}
    ci_environment=environment if ci_environment is None else ci_environment
    ci_digest=project_environment_fingerprint(ci_environment,contract=V2)
    def guard(event):
        index=event['commandIndex'];seen=state['seen'];_expect(len(seen)<len(sequence) and index==sequence[len(seen)])
        _expect(event['profileDigest']==profile_digest and event['actualCwd']==cwd
            and project_environment_fingerprint(event['actualEnv'],contract=V2)==(ci_digest if index==target else env_digest))
        if state['invocationId'] is None:state['invocationId']=event['invocationId']
        _expect(state['invocationId']==event['invocationId']);seen.append(index)
        if index!=target:return None
        _expect(state['target'] is None and event['operation']=='project-command'
            and event['sealedCommandHash']==hashlib.sha256(b'npm run ci').hexdigest())
        from baseline_constraint_verifier import project_check_command_argv
        argv=project_check_command_argv('npm run ci',environment)
        expected=subprocess.list2cmdline(argv[:-1])+' "'+argv[-1]+'"'
        _expect(event['actualArgv']==expected and event['actualEnv'].get('TAP_RCFILE')==value['lanes'][role]['yaml']['path'])
        # Outer leases remain held. This nested read rechecks physical IDs/bytes
        # after install, immediately on the FINAL Popen values; no output waiver.
        with held_input(value,project,role,profile_digest,deadline=deadline,require_empty=False) as now:
            _expect(now=={k:v for k,v in entry.items() if k!='toolCacheBinding'})
            for row in value['lanes'][role]['outputs'].values():_expect(not any(Path(row['path']).iterdir()))
        if tool_policy is not None:
            from deploom_mcp.goal_tool_cache import held_consumers,expect
            expect(consumer_leases is not None and event['actualEnv'].get('CACHE_DIR')==value['lanes'][role]['outputs']['cache']['path'])
            entry['toolCacheBinding']['consumerIdentities']=consumer_leases.enter_context(held_consumers(tool_policy,project,deadline=deadline))
        input_hash=canonical_digest({**entry,'actualEnvironmentDigest':ci_digest})
        state['target']={'prePopenObservationHash':event['prePopenObservationHash'],'nativeProcessResultId':event['nativeProcessResultId']}
        return {'role':role,'inputBindingHash':input_hash}
    return guard,state


def witness_matches(raw,profile,role,input_observation):
    try:
        target=profile['commands'].index('npm run ci')+1;ops=raw['ownerOperationObservations'];selected=[r for r in ops if r.get('guardedSpawnWitness') is not None]
        if len(selected)!=1:return False
        op=selected[0];w=op['guardedSpawnWitness']
        keys={'schemaVersion','kind','invocationId','operation','commandIndex','sealedCommandHash','profileDigest','argvDigest',
            'launchArgvDigest','environmentDigest','cwdDigest','nativeProcessResultId','role','inputBindingHash','prePopenObservationHash','spawnStarted','nativePid'}
        if (not isinstance(w,dict) or set(w)!=keys or type(w['schemaVersion']) is not int or w['schemaVersion']!=1
            or w['kind']!='TRUSTED_PRE_POPEN_GUARD_V1' or w['operation']!='project-command' or w['role']!=role
            or type(w['commandIndex']) is not int or w['commandIndex']!=target or op['commandIndex']!=target
            or w['invocationId']!=raw['freshCommandInvocationId'] or w['sealedCommandHash']!=hashlib.sha256(b'npm run ci').hexdigest()
            or w['profileDigest']!=canonical_digest(profile) or w['argvDigest']!=op['argvDigest']
            or w['environmentDigest']!=input_observation['actualEnvironmentDigest'] or w['cwdDigest']!=input_observation['cwdDigest']
            or w['inputBindingHash']!=input_observation['inputBindingHash'] or w['spawnStarted'] is not True
            or type(w['nativePid']) is not int or w['nativePid']<1
            or not isinstance(w['nativeProcessResultId'],str) or re.fullmatch('[0-9a-f]{32}',w['nativeProcessResultId']) is None
            or not isinstance(w['launchArgvDigest'],str) or re.fullmatch('[0-9a-f]{64}',w['launchArgvDigest']) is None):return False
        native={k:v for k,v in w.items() if k not in {'role','inputBindingHash','prePopenObservationHash','spawnStarted','nativePid'}}
        return canonical_digest(native)==w['prePopenObservationHash']
    except (KeyError,TypeError,ValueError,AttributeError):return False
