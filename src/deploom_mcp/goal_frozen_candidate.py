"""One owned frozen install + existing Core workspace verifier for private candidates.

Raw CURRENT_WORKSPACE observations retain their original scope. This adapter
never fabricates the older assignment-verifier event stream or a final PASS.
"""
import hashlib,os,time
from pathlib import Path,PurePosixPath
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_execution_registry import configured_state_root,require_registered_execution_class,FROZEN_ROUTE
from deploom_mcp.goal_check_bindings import (observe_oracles,summarize_check_bindings,validate_verification_context,validate_normative_admission)
from deploom_mcp.goal_workspace_observation import workspace_profile_complete


def _expect(value,code):
    if not value:raise GoalContractError(code)


def _verify_raw(raw,registration,source_key,plan,project):
    from deploom_mcp.goal_execution_environment import environment_observation_matches_contract
    _expect(environment_observation_matches_contract(registration.policy,raw),'FROZEN_ENVIRONMENT_CONTRACT_MISMATCH')
    from deploom_mcp.goal_generated_outputs import observation_matches
    _expect(observation_matches(registration.policy,registration.profile,raw),'FROZEN_GENERATED_OUTPUT_UNPROVEN')
    _expect(workspace_profile_complete(registration.profile,source_key,raw,plan,project),'FROZEN_CORE_OBSERVATION_INCOMPLETE')
    tree=raw['materializedDependencyObligations'];origin=raw['materializedSourceOrigin']
    _expect(raw.get('currentWorkspaceVerified') is False and origin.get('scope')=='CURRENT_HOST_WORKSPACE'
        and origin.get('workspacePathDigest')==raw['workspacePathDigest']
        and origin.get('resolvedLockfileHash')==plan['approvedLockSha256']
        and tree.get('status')=='MATERIALIZED_NPM_INSTANCE_OBLIGATIONS_OBSERVED'
        and tree.get('authority')=='NON_REUSABLE_CORE_OBSERVATION' and tree.get('fullAcceptance') is False
        and tree.get('instanceCount')==plan['instanceCount'],'FROZEN_PHYSICAL_SCOPE_UNCONFIRMED')
    operations=raw.get('ownerOperationObservations')
    _expect(isinstance(operations,list) and len(operations)==1+len(registration.profile['commands']),'FROZEN_OPERATION_SET_INCOMPLETE')
    for index,row in enumerate(operations):
        _expect(isinstance(row,dict) and row.get('operation')==('strict-frozen-install' if index==0 else 'project-command')
            and row.get('commandIndex')==(None if index==0 else index)
            and type(row.get('returnCode')) is int and row['returnCode']==0
            and row.get('supervision')=='guaranteed-tree' and type(row.get('descendantsRemaining')) is int
            and row['descendantsRemaining']==0 and row.get('timedOut') is False and row.get('cancelled') is False,
            'FROZEN_OPERATION_CLOSURE_UNCONFIRMED')
    for row,command_row in zip(operations[1:],raw['commandObservations']):
        inline=command_row.get('inlineExecution')
        if inline:_expect(row.get('argvDigest')==canonical_digest(inline['argv']),'FROZEN_OPERATION_ARGV_MISMATCH')
    _expect(raw.get('installation',{}).get('status')=='FRESH_COLD_FROZEN_INSTALL_OBSERVED'
        and raw['installation'].get('scriptsEnabled') is False,'FROZEN_INSTALL_NOT_OBSERVED')


def verify_frozen_applied_candidate(project,container,registration,proposal):
    from deploom_mcp.goal_delta_trial import _core,_remaining,_proposal_artifacts
    from resolved_dependency_state import compile_npm_dependency_obligations,MaterializedDependencyObligationError
    from materialized_workspace_verifier import verify_current_workspace,MaterializedWorkspaceVerificationError
    _expect(require_registered_execution_class(registration)=='PREAPPROVED_HOST_POLICY'
        and registration.policy.get('privateVerificationRoute')==FROZEN_ROUTE
        and registration.profile.get('admission') is not None,'FROZEN_HOST_ADMISSION_REQUIRED')
    root=configured_state_root().resolve();project=Path(project).resolve();container=Path(container).resolve()
    _expect(container.parent==(root/'delta-trial-workspaces') and project==container/'repo','FROZEN_OWNED_WORKSPACE_REQUIRED')
    modules=project/'node_modules'
    _expect(not modules.exists(),'FROZEN_EMPTY_DEPENDENCY_TREE_REQUIRED')
    core=_core();old_ceiling=os.environ.get('GIT_CEILING_DIRECTORIES');os.environ['GIT_CEILING_DIRECTORIES']=str(root)
    try:
        # Core's workspace verifier owns the sole strict candidate capture.
        # Fingerprint-before + expected key in that capture closes the gap; do
        # not nest a reused strict capture and waive ownership on cleanup.
        candidate_key=core.source_snapshot_fingerprint(project)
        _expect(core._subject_layout(project)[0].resolve()==project and candidate_key!=proposal.source_key,'CANDIDATE_SOURCE_CAPTURE_MISMATCH')
        for name,wanted in _proposal_artifacts(proposal):
            _expect((project/name).read_bytes()==wanted,'CANDIDATE_SOURCE_BYTES_MISMATCH')
        initial=(Path(registration.project_path)/'package-lock.json').read_bytes()
        _expect(hashlib.sha256(initial).hexdigest()==proposal.baseline_lock_hash,'STALE_DELTA_BASELINE')
        plan=compile_npm_dependency_obligations(initial,proposal.lock_bytes,exact_targets=registration.goal['exactTargets'],
            changed_instances=sorted({r['instance'] for r in proposal.descriptor['instances'] if r['view']=='packages'}),
            immutable_packages=registration.goal['immutablePackages'],excluded_packages=registration.goal['excludedPackages'],
            delta_digest=proposal.delta_digest,scope_digest=registration.profile['admission']['allowedChangeScopeDigest'],
            optional_absence_rules=registration.profile['admission']['optionalAbsenceRules'],
            bundled_owner_rules=registration.profile['admission'].get('bundledOwnerRules'))
        known={r['name'] for r in plan['instances']}|{r['installName'] for r in plan['instances']}
        _expect(all(set(r['normative']['purpose']['subjectPackages']).issubset(known) for r in registration.profile['checkBindings']),
            'NORMATIVE_CHECK_SUBJECT_UNAVAILABLE')
        oracle_before=observe_oracles(registration.profile,root,project)
        _expect(all(v=='MATCH' for v in oracle_before.values()),'HOST_CHECK_ORACLE_UNAVAILABLE_OR_CHANGED')
        profile_digest=canonical_digest(registration.profile)
        from deploom_mcp.goal_execution_registry import private_core_deadline,private_execution_budgets
        operational_budget=private_core_deadline(registration,time.time());deadline=operational_budget['effectiveCoreDeadline']
        from deploom_mcp.goal_tap_nyc_execution import verify_with_tap_input
        try:
            raw,tap_input=verify_with_tap_input(verify_current_workspace,registration.policy,registration.profile,
                'private',project,expected_source_key=candidate_key,dependency_plan=plan,
                commands=registration.profile['commands'],cache_dir=Path(os.environ['DEPLOOM_MCP_OFFLINE_CACHE']),
                deadline=deadline,final_profile_digest=profile_digest,materialize=True)
        except (MaterializedWorkspaceVerificationError,MaterializedDependencyObligationError) as exc:
            # Safe structured failure diagnostics; do not leak command bodies or logs.
            from deploom_mcp.goal_delta_trial import _write_ledger
            error_path=root/('frozen-failure-'+canonical_digest({'run':registration.run_id,'candidate':registration.candidate_id})+'.json')
            _write_ledger(error_path,{'code':str(exc),'ownerOperationObservations':list(getattr(exc,'operation_observations',())),
                'fullAcceptance':False,'sourceSnapshotKey':candidate_key,'profileDigest':profile_digest})
            raise GoalContractError('FROZEN_CORE_VERIFICATION_INCOMPLETE') from exc
        # Actual return/cleanup is required before any observation-complete claim.
        raw_at_return=canonical_digest(raw)
        slim={k:plan[k] for k in ('obligationPlanDigest','approvedLockSha256','baselineLockSha256','approvedDeltaDigest','allowedChangeScopeDigest')}
        slim['instanceCount']=len(plan['instances'])
        _verify_raw(raw,registration,candidate_key,slim,project)
        _expect(core.source_snapshot_fingerprint(project)==candidate_key,
            'CANDIDATE_SOURCE_CHANGED_DURING_VERIFICATION')
        oracle_after=observe_oracles(registration.profile,root,project)
        _expect(oracle_after==oracle_before and all(v=='MATCH' for v in oracle_after.values()),'HOST_CHECK_ORACLE_UNAVAILABLE_OR_CHANGED')
        _expect(canonical_digest(registration.profile)==profile_digest and canonical_digest(raw)==raw_at_return,
            'PROFILE_CHANGED_DURING_INVOCATION')
        identities={r['commandIndex']:{'inlineNodeExecutionMode':r['inlineExecution']['executionMode'],
            'inlineNodeBodySha256':r['inlineExecution']['bodySha256'],
            'nodeExecutableSha256':r['inlineExecution']['nodeExecutableSha256']}
            for r in raw['commandObservations'] if r.get('inlineExecution')}
        named=summarize_check_bindings(registration.profile,list(range(1,len(registration.profile['commands'])+1)),
            oracle_before,oracle_after,identities)
        for item in named['checks']:
            if item.get('evidence')=='SOURCE_LOCK_CONTINUITY':
                item.update(status='CORE_CONTINUITY_OBSERVED',contractFulfillment='SCOPED_OBSERVED',reason='RAW_CORE_WORKSPACE_BOUNDARIES')
        return {'verificationRoute':FROZEN_ROUTE,'verificationAuthority':'HOST_AUTHORIZED_PRIVATE_VERIFICATION_OBSERVED',
            'bindingDigest':registration.binding['bindingDigest'],'profileDigest':profile_digest,
            'approvedCandidateDigest':proposal.candidate_digest,'approvedDeltaDigest':proposal.delta_digest,
            'approvedLockSha256':slim['approvedLockSha256'],'baselineSourceSnapshotKey':proposal.source_key,
            'candidateVerifiedSourceSnapshotKey':candidate_key,'privateWorkspaceRelativePath':project.relative_to(root).as_posix(),
            'coreWorkspaceObservation':raw,'coreObservationDigest':raw_at_return,'obligationPlanIdentity':slim,
            'materializedSourceOrigin':raw['materializedSourceOrigin'],'namedCheckObservations':named,
            **({'tapInputObservation':tap_input} if tap_input is not None else {}),
            **({'executionBudget':operational_budget} if private_execution_budgets(registration)['explicit'] else {}),
            'outerInvocationState':'OBSERVATION_COMPLETE_AFTER_RETURN','engineeringControl':False,
            'observationComplete':True,'projectChecksExecuted':True,'fullAcceptance':False,
            'currentWorkspaceVerified':False,'productionAuthorization':False}
    finally:
        if old_ceiling is None:os.environ.pop('GIT_CEILING_DIRECTORIES',None)
        else:os.environ['GIT_CEILING_DIRECTORIES']=old_ceiling


def frozen_profile_complete(record,registration):
    try:
        _expect(require_registered_execution_class(registration)=='PREAPPROVED_HOST_POLICY'
            and registration.policy.get('privateVerificationRoute')==FROZEN_ROUTE
            and canonical_digest(registration.policy)==registration.goal['policyDigest'],'FROZEN_HOST_ADMISSION_REQUIRED')
        _expect(validate_normative_admission(registration.profile,registration.goal['requiredChecks']) is not None,'FROZEN_HOST_ADMISSION_REQUIRED')
        validate_verification_context(registration.goal,registration.profile,registration.binding['identity'])
        approval=registration.delta_authorization;o=record['verificationObservation'];raw=o['coreWorkspaceObservation'];plan=o['obligationPlanIdentity']
        for actual,expected in [(record.get('runId'),registration.run_id),(record.get('candidateId'),registration.candidate_id),
                (record.get('bindingDigest'),registration.binding['bindingDigest']),(record.get('goalDigest'),registration.binding['goalDigest']),
                (record.get('profileDigest'),canonical_digest(registration.profile)),(record.get('candidateDigest'),approval['candidateDigest']),
                (record.get('deltaDigest'),approval['deltaDigest']),(record.get('sourceSnapshotKey'),approval['sourceSnapshotKey']),
                (o.get('bindingDigest'),registration.binding['bindingDigest']),(o.get('profileDigest'),canonical_digest(registration.profile)),
                (o.get('approvedCandidateDigest'),approval['candidateDigest']),(o.get('approvedDeltaDigest'),approval['deltaDigest']),
                (o.get('baselineSourceSnapshotKey'),approval['sourceSnapshotKey']),(plan['baselineLockSha256'],registration.binding['identity']['lockDigest']),
                (plan['approvedDeltaDigest'],approval['deltaDigest']),(plan['allowedChangeScopeDigest'],registration.profile['admission']['allowedChangeScopeDigest'])]:
            _expect(actual==expected,'FROZEN_RECEIPT_BINDING_MISMATCH')
        _expect(record.get('authorizationOrigin')=='PREAPPROVED_HOST_POLICY'
            and all(record.get(k) is True for k in ('privateExecutionAuthorized','privateExecutionAttempted','privateVerificationObserved','allOwnedSnapshotCleanupConfirmed'))
            and record.get('fullAcceptance') is False and record.get('currentWorkspaceVerified') is False
            and o.get('verificationRoute')==FROZEN_ROUTE and o.get('observationComplete') is True
            and o.get('projectChecksExecuted') is True and o.get('engineeringControl') is False
            and o.get('fullAcceptance') is False and o.get('currentWorkspaceVerified') is False
            and o.get('outerInvocationState')=='OBSERVATION_COMPLETE_AFTER_RETURN'
            and o.get('verificationAuthority')=='HOST_AUTHORIZED_PRIVATE_VERIFICATION_OBSERVED'
            and o.get('candidateVerifiedSourceSnapshotKey')!=approval['sourceSnapshotKey']
            and canonical_digest(raw)==o.get('coreObservationDigest')
            and o.get('materializedSourceOrigin')==raw['materializedSourceOrigin']
            and record.get('sourceOriginObservationDigest')==canonical_digest(raw['materializedSourceOrigin']),
            'FROZEN_RECEIPT_NOT_COMPLETE')
        from deploom_mcp.goal_execution_registry import private_core_deadline,private_execution_budgets
        if private_execution_budgets(registration)['explicit']:
            budget=o.get('executionBudget');_expect(isinstance(budget,dict),'FROZEN_BUDGET_UNPROVEN')
            _expect(type(budget.get('coreStartedAt')) in (int,float) and budget==private_core_deadline(registration,budget['coreStartedAt']),'FROZEN_BUDGET_UNPROVEN')
        root=configured_state_root().resolve();rel=o['privateWorkspaceRelativePath'];parts=PurePosixPath(rel).parts
        _expect(len(parts)==3 and parts[0]=='delta-trial-workspaces' and parts[1].startswith('delta-') and parts[2]=='repo'
            and PurePosixPath(rel).as_posix()==rel,'FROZEN_WORKSPACE_IDENTITY_MISMATCH')
        project=root/rel
        _expect(project.resolve().is_relative_to(root) and o['approvedLockSha256']==plan['approvedLockSha256'],'FROZEN_LOCK_IDENTITY_MISMATCH')
        _verify_raw(raw,registration,o['candidateVerifiedSourceSnapshotKey'],plan,project)
        from deploom_mcp.goal_tap_nyc_execution import receipt_matches
        _expect(receipt_matches(registration.policy,registration.profile,'private',project,raw,o.get('tapInputObservation')),
            'FROZEN_TAP_INPUT_UNPROVEN')
        named=o['namedCheckObservations'];_expect(named['mappingStatus']=='HOST_PINNED','FROZEN_NAMED_CHECKS_INCOMPLETE')
        contracts={r['checkId']:r for r in registration.profile['checkBindings']}
        _expect(len(named['checks'])==len(contracts) and {r['checkId'] for r in named['checks']}==set(contracts),'FROZEN_NAMED_CHECKS_INCOMPLETE')
        for row in named['checks']:
            binding=contracts[row['checkId']];wanted=('COMMAND_OBSERVED_PASS' if binding['kind']=='command' else
                'CORE_CONTINUITY_OBSERVED' if binding['evidence']=='SOURCE_LOCK_CONTINUITY' else 'OWNED_CLEANUP_OBSERVED')
            _expect(row.get('status')==wanted and row.get('normativeScope')=='HOST_PINNED'
                and row.get('contractFulfillment')=='SCOPED_OBSERVED'
                and row.get('normativeContractDigest')==canonical_digest(binding['normative']),'FROZEN_NAMED_CHECKS_INCOMPLETE')
            if binding.get('oracle'):_expect(row.get('oracleExecutedBytes')=='DIRECT_ARGV_BODY_OWNER_OBSERVED','FROZEN_ORACLE_EXECUTION_UNCONFIRMED')
        return True
    except (GoalContractError,KeyError,TypeError,ValueError,AttributeError,OSError):return False
