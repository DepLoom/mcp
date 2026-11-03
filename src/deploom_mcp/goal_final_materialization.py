"""Stage an exact frozen npm tree, preserve/switch it, then check the live root."""
from __future__ import annotations
import hashlib,os,shutil,time
from pathlib import Path
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_report import _plain_under
from deploom_mcp.goal_windows_tree_swap import prepare_tree_swap,apply_tree_swap,tree_digest


def compiled_obligations(tx):
    return compile_bound_obligations(tx.registration,tx.baseline(),tx.proposal)


def compile_bound_obligations(reg,base,proposal):
    from resolved_dependency_state import compile_npm_dependency_obligations
    return compile_npm_dependency_obligations((base.project_path/'package-lock.json').read_bytes(),
        proposal.lock_bytes,exact_targets=reg.goal['exactTargets'],
        changed_instances=sorted({row['instance'] for row in proposal.descriptor['instances'] if row['view']=='packages'}),
        immutable_packages=reg.goal['immutablePackages'],excluded_packages=reg.goal['excludedPackages'],
        delta_digest=proposal.delta_digest,
        scope_digest=reg.profile['admission']['allowedChangeScopeDigest'],
        optional_absence_rules=reg.profile['admission']['optionalAbsenceRules'],
            bundled_owner_rules=reg.profile['admission'].get('bundledOwnerRules'))


def materialize_dependencies(tx):
    """Journal before live-root mutations; existing user tree is never deleted."""
    from verification_process_supervisor import run_supervised
    from resolved_dependency_state import observe_npm_dependency_obligations,capture_resolved_dependency_state,npm_root_assignment_from_obligations
    from baseline_constraint_verifier import observed_resolved_assignment,observed_resolved_hash
    tx.guard_source();plan=compiled_obligations(tx)
    move=tx.record.get('dependencyTreeMove')
    if move is not None:
        expected=(str(tx.project/'node_modules'),str(tx.directory/'dependency-stage/repo/node_modules'),str(tx.directory/'previous-node_modules'))
        if tuple(move[k] for k in ('target','staged','backup'))!=expected or move['deadline']!=tx.record['deadline']:
            raise GoalContractError('FINAL_DEPENDENCY_MOVE_BINDING_MISMATCH')
        apply_tree_swap(move,deadline=tx.record['deadline'],cutpoint=tx.cut)
        tx.guard_source()
        if tx.record['phase'] not in {'LIVE_VERIFIED','SNAPSHOT_DURABLE'}:tx.save('DEPENDENCIES_READY')
        return plan
    cache_text=os.environ.get('DEPLOOM_MCP_OFFLINE_CACHE','')
    if not cache_text or not Path(cache_text).is_absolute():raise GoalContractError('OWNED_OFFLINE_CACHE_REQUIRED')
    cache=_plain_under(Path(cache_text),tx.root)
    if not cache.is_dir():raise GoalContractError('OWNED_OFFLINE_CACHE_REQUIRED')
    stage_container=_plain_under(tx.directory/'dependency-stage',tx.directory)
    stage_project=stage_container/'repo'
    expected_key=tx.context.accepted_receipt['verificationObservation']['candidateVerifiedSourceSnapshotKey']
    state=tx.record.setdefault('dependencyStage',{'sourceCopyAttempts':0,'npmAttempts':0,'verified':False})
    tx.save()
    if state['verified']:
        if tx.core.source_snapshot_fingerprint(stage_project)!=expected_key or tree_digest(stage_project/'node_modules',deadline=tx.record['deadline'])!=state['payloadDigest']:
            raise GoalContractError('FINAL_STAGED_DEPENDENCIES_CHANGED')
    else:
        valid=False
        if stage_project.is_dir():
            try:valid=tx.core.source_snapshot_fingerprint(stage_project)==expected_key
            except (OSError,RuntimeError):valid=False
        if not valid:
            if state['sourceCopyAttempts']>=2:raise GoalContractError('FINAL_STAGE_COPY_LIMIT')
            if stage_container.exists():
                orphan=_plain_under(tx.directory/('dependency-stage-orphan-'+str(state['sourceCopyAttempts'])),tx.directory)
                if orphan.exists():raise GoalContractError('FINAL_STAGE_ORPHAN_COLLISION')
                if not stage_container.resolve().is_relative_to(tx.directory.resolve()):raise GoalContractError('FINAL_STAGE_OWNERSHIP_MISMATCH')
                os.rename(stage_container,orphan)
            state['sourceCopyAttempts']+=1;tx.save();stage_container.mkdir()
            with tx.core.verification_source_snapshot_scope(tx.project,timeout_seconds=tx.remaining(),strict_cleanup=True) as source:
                if source.key!=expected_key:raise GoalContractError('FINAL_DELIVERED_SOURCE_MISMATCH')
                with tx.core.bind_source_snapshot(source):
                    from deploom_mcp.goal_generated_outputs import source_materialization_arguments
                    project,consumed,method=tx.core.materialize_source_for_verification(tx.project,stage_project,timeout_seconds=tx.remaining(),
                        **source_materialization_arguments(tx.registration.policy,tx.registration.profile,tx.project))
                if project.resolve()!=stage_project.resolve() or consumed.key!=expected_key:
                    raise GoalContractError('FINAL_STAGING_SOURCE_MISMATCH')
        if state['npmAttempts']>=3:raise GoalContractError('FINAL_STAGED_INSTALL_LIMIT')
        env=dict(os.environ)
        if any(str(env.get(k,'')).strip() for k in ('NODE_OPTIONS','NODE_PATH')):
            raise GoalContractError('FINAL_EXTERNAL_NODE_LOADER_UNPROVEN')
        env.update(CI='1',npm_config_offline='true',npm_config_ignore_scripts='true',
            npm_config_strict_peer_deps='true',npm_config_engine_strict='true',
            npm_config_force='false',npm_config_legacy_peer_deps='false',npm_config_cache=str(cache))
        node=shutil.which('node',path=env.get('PATH'));npm=Path(node).parent/'node_modules/npm/bin/npm-cli.js' if node else None
        if not node or not npm.is_file():raise GoalContractError('FINAL_INSTALL_TOOLCHAIN_UNAVAILABLE')
        from deploom_mcp.goal_execution_environment import environment_contract,workspace_environment,V2
        launch_options={}
        if environment_contract(tx.registration.policy)==V2:
            env=workspace_environment(tx.registration.policy,cache)
            from project_execution_environment import project_environment_fingerprint
            launch_options['expected_project_environment_digest']=project_environment_fingerprint(env,contract=V2)
        state['npmAttempts']+=1;tx.save()
        with tx.core.verification_source_snapshot_scope(stage_project,timeout_seconds=tx.remaining(),strict_cleanup=True) as source:
            if source.key!=expected_key:raise GoalContractError('FINAL_STAGING_SOURCE_MISMATCH')
            result=run_supervised([node,str(npm),'ci','--offline','--ignore-scripts','--strict-peer-deps',
                '--engine-strict','--force=false','--legacy-peer-deps=false','--no-audit','--no-fund','--cache',str(cache)],
                stage_project,timeout_seconds=tx.remaining(),base_env=env,max_output_bytes=65536,**launch_options)
            supervision=getattr(result,'supervision',None)
            if (result.returncode!=0 or getattr(supervision,'quality',None)!='guaranteed-tree'
                    or getattr(supervision,'descendants_remaining',None)!=0
                    or not tx.core.source_snapshot_live_source_continuity(source,timeout_seconds=tx.remaining())):
                raise GoalContractError('FINAL_STAGED_INSTALL_UNPROVEN')
            if launch_options:
                from project_execution_environment import native_launch_observation_matches
                launch=getattr(result,'project_execution_environment',None)
                if not native_launch_observation_matches(launch,launch_options['expected_project_environment_digest'],canonical_digest(str(stage_project.resolve()))):
                    raise GoalContractError('FINAL_STAGED_ACTUAL_ENVIRONMENT_UNPROVEN')
                state['projectExecutionEnvironment']=dict(launch)
            roots=npm_root_assignment_from_obligations(plan)
            observed=observed_resolved_assignment(stage_project,roots)
            resolved=capture_resolved_dependency_state(stage_project,manager='npm',
                resolver_input_key=canonical_digest({'sourceKey':source.key,'plan':plan['obligationPlanDigest']}),
                observed_resolved_hash=observed_resolved_hash(observed),proof_cache_dir=None)
            physical=observe_npm_dependency_obligations(stage_project,plan,
                source_origin_digest=canonical_digest({'scope':'FINAL_OWNED_NPM_STAGING','sourceKey':source.key,'lockHash':resolved.lockfile_hash}),
                resolved_state_key=resolved.key,deadline=time.monotonic()+tx.remaining())
            if resolved.lockfile_hash!=hashlib.sha256(tx.proposal.lock_bytes).hexdigest() or physical['unknownCount']!=0:
                raise GoalContractError('FINAL_STAGED_TREE_UNPROVEN')
            from deploom_mcp.goal_final_auxiliary import prepare_observed_auxiliary
            auxiliary=prepare_observed_auxiliary(tx,stage_project,plan)
            if auxiliary is not None:state['auxiliaryPreparation']=auxiliary
            state.update(verified=True,sourceKey=source.key,obligationPlanDigest=plan['obligationPlanDigest'],
                payloadDigest=tree_digest(stage_project/'node_modules',deadline=tx.record['deadline']),
                installStdoutDigest=hashlib.sha256((result.stdout or '').encode()).hexdigest(),physicalObservation=physical)
        tx.save('DEPENDENCIES_STAGED');tx.cut('DEPENDENCIES_STAGED')
    tx.guard_source()
    move=prepare_tree_swap(tx.project/'node_modules',stage_project/'node_modules',
        tx.directory/'previous-node_modules',deadline=tx.record['deadline'])
    if move['after']['payloadDigest']!=state['payloadDigest']:raise GoalContractError('FINAL_STAGE_PAYLOAD_CHANGED')
    tx.record['dependencyTreeMove']=move;tx.save();tx.cut('DEPENDENCY_MOVE_PLAN')
    apply_tree_swap(move,deadline=tx.record['deadline'],cutpoint=tx.cut)
    tx.guard_source();tx.save('DEPENDENCIES_READY');return plan


def verify_live_workspace(tx,plan):
    from materialized_workspace_verifier import verify_current_workspace
    tx.guard_source()
    expected=tx.context.accepted_receipt['verificationObservation']['candidateVerifiedSourceSnapshotKey']
    if tx.record.get('liveCheckAttempts',0)>=3:raise GoalContractError('FINAL_LIVE_CHECK_ATTEMPT_LIMIT')
    tx.record['liveCheckAttempts']=tx.record.get('liveCheckAttempts',0)+1;tx.save()
    from deploom_mcp.goal_tap_nyc_execution import verify_with_tap_input
    observation,tap_input=verify_with_tap_input(verify_current_workspace,tx.registration.policy,tx.context.final_profile,
        'final',tx.project,expected_source_key=expected,dependency_plan=plan,
        commands=tuple(tx.context.final_profile['commands']),cache_dir=Path(os.environ['DEPLOOM_MCP_OFFLINE_CACHE']),
        deadline=tx.record['deadline'],final_profile_digest=canonical_digest(tx.context.final_profile),materialize=False)
    if tap_input is not None:tx.record['tapInputObservation']=tap_input
    tx.guard_source()
    from deploom_mcp.goal_final_auxiliary import validate_live_auxiliary
    validate_live_auxiliary(tx,plan,observation)
    move=tx.record['dependencyTreeMove']
    apply_tree_swap(move,deadline=tx.record['deadline'])  # read-only if already switched; checks IDs/payload
    tx.record['liveObservation']=observation;tx.save('LIVE_VERIFIED');tx.cut('LIVE_VERIFIED')
    return observation
