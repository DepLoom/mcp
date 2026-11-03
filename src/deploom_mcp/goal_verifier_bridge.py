"""Host-owned isolated verifier; execution permission is not goal acceptance.

No caller commands, paths, bytes, grant or source keys enter through MCP JSON.
Real Core owns offline resolver/project execution for engineering or explicitly
preapproved host policy. Full goal admission and final apply remain separate.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import time
from deploom_mcp.goal_contract import GoalContractError,canonical_digest,npm_lock_format
from deploom_mcp.goal_execution_registry import configured_state_root,load_host_registration,OPERATION,require_registered_execution_class
from deploom_mcp.goal_delta_trial import (_core,propose_exact_delta,load_host_delta_grant,
    _apply_authorized_private_trial,APPLY_OPERATION,_remaining,_proposal_artifacts,_json)
from deploom_mcp.goal_report import _plain_under
from deploom_mcp.goal_invocation_adapter import InvocationObservationCollector


def trial_ledger_path(registration):
    root=configured_state_root().resolve()
    key=canonical_digest({"runId":registration.run_id,"candidateId":registration.candidate_id})
    return _plain_under(root/"delta-trial-attempts"/(key+".json"),root)


def _candidate_bytes(registration):
    files=registration.candidate_files
    if not files:raise GoalContractError("HOST_CANDIDATE_FILES_UNAVAILABLE")
    root=configured_state_root().resolve();result=[]
    for name,limit in (("manifest",1024*1024),("lock",8*1024*1024)):
        path=_plain_under(root/files[name],root)
        if path.stat().st_size>limit:raise GoalContractError("CANDIDATE_FILE_SIZE_OR_TYPE_INVALID")
        result.append(path.read_bytes())
    if "sources" in files:
        sources={}
        for name,relative in files["sources"].items():
            path=_plain_under(root/relative,root)
            if path.stat().st_size>1024*1024:raise GoalContractError("SOURCE_REPAIR_FILE_SIZE_OR_TYPE_INVALID")
            sources[name]=path.read_bytes()
        result.append(sources)
    return tuple(result)


def _require_complete_observation(result, observation, commands):
    if (observation.get("observationComplete") is not True or not result.ok or result.kind!="passed"
            or observation["commandChecks"]["unconfirmed"]
            or observation["commandChecks"]["freshConfirmed"]!=list(range(1,len(commands)+1))):
        raise GoalContractError("CORE_CANDIDATE_VERIFICATION_INCOMPLETE")


def _verify_applied_candidate(project,container,registration,proposal):
    if registration.policy.get('privateVerificationRoute') is not None:
        from deploom_mcp.goal_frozen_candidate import verify_frozen_applied_candidate
        return verify_frozen_applied_candidate(project,container,registration,proposal)
    from baseline_constraint_verifier import verify_assignment,BaselineVerifyConfig,assignment_fingerprint
    core=_core()
    # This dedicated worker must not discover an unrelated Git repository above
    # its private materialization. Git's normal ceiling is process-scoped, and
    # capture-root equality below remains mandatory (no monkeypatch of Core).
    old_ceiling=os.environ.get("GIT_CEILING_DIRECTORIES")
    os.environ["GIT_CEILING_DIRECTORIES"]=str(configured_state_root().resolve())
    try:
        with core.verification_source_snapshot_scope(project,timeout_seconds=_remaining(registration),strict_cleanup=True) as candidate:
            if candidate.capture_root.resolve()!=project.resolve() or candidate.key==proposal.source_key:
                raise GoalContractError("CANDIDATE_SOURCE_CAPTURE_MISMATCH")
            core.validate_source_snapshot(candidate,timeout_seconds=_remaining(registration))
            for name,wanted in _proposal_artifacts(proposal):
                if (candidate.project_path/name).read_bytes()!=wanted:
                    raise GoalContractError("CANDIDATE_SOURCE_BYTES_MISMATCH")
            admission=registration.profile.get("admission")
            dependency_plan={}
            if admission is not None:
                from resolved_dependency_state import compile_npm_dependency_obligations
                initial_lock=_plain_under(Path(registration.project_path)/"package-lock.json",Path(registration.project_path)).read_bytes()
                if hashlib.sha256(initial_lock).hexdigest()!=proposal.baseline_lock_hash:
                    raise GoalContractError("STALE_DELTA_BASELINE")
                dependency_plan=compile_npm_dependency_obligations(initial_lock,proposal.lock_bytes,
                    exact_targets=registration.goal["exactTargets"],
                    changed_instances=sorted({r["instance"] for r in proposal.descriptor["instances"] if r["view"]=="packages"}),
                    immutable_packages=registration.goal["immutablePackages"],excluded_packages=registration.goal["excludedPackages"],
                    delta_digest=proposal.delta_digest,scope_digest=admission["allowedChangeScopeDigest"],
                    optional_absence_rules=admission["optionalAbsenceRules"],
                    bundled_owner_rules=admission.get("bundledOwnerRules"))
                known={row["name"] for row in dependency_plan["instances"]}|{row["installName"] for row in dependency_plan["instances"]}
                if any(not set(row["normative"]["purpose"]["subjectPackages"]).issubset(known) for row in registration.profile["checkBindings"]):
                    raise GoalContractError("NORMATIVE_CHECK_SUBJECT_UNAVAILABLE")
            collector=InvocationObservationCollector(registration,candidate.key,
                assignment_fingerprint(registration.goal["exactTargets"]),
                engineering_control=require_registered_execution_class(registration)=="ENGINEERING_CONTROL",
                expected_obligation_plan_digest=dependency_plan.get("obligationPlanDigest"))
            _remaining(registration)
            remaining=registration.deadline-time.time();seconds=max(1,int(min(remaining,120)))
            config=BaselineVerifyConfig(enabled=True,project_checks="strict",lifecycle_scripts=False,
                observe_materialized_source_origin=True,observe_materialized_dependency_obligations=admission is not None,
                dependency_obligation_plan=dependency_plan,commands=tuple(registration.profile["commands"]),
                verification_purpose="intermediate-candidate",publish_durable_prepared_artifact=False,
                telemetry_path=str(configured_state_root()/("core-"+canonical_digest({"run":registration.run_id,"candidate":registration.candidate_id})+".jsonl")),
                timeout_seconds=seconds,attempt_timeout_seconds=seconds,project_check_timeout_seconds=seconds,
                snapshot_copy_timeout_seconds=seconds,budget_phase_deadline=time.monotonic()+remaining)
            result=collector.invoke(lambda observer:verify_assignment(project,dict(registration.goal["exactTargets"]),
                config=config,run_project_checks=True,invocation_observer=observer))
            if not result.ok or result.kind!="passed":
                raise GoalContractError("CORE_CANDIDATE_VERIFICATION_INCOMPLETE")
            observation=collector.finalize(result)
            _require_complete_observation(result, observation, config.commands)
            origin=observation.get("materializedSourceOrigin")
            if not origin or origin.get("inputSourceSnapshotKey")!=candidate.key:
                raise GoalContractError("CORE_CANDIDATE_SOURCE_ORIGIN_UNCONFIRMED")
            if result.resolved_lockfile_hash!=hashlib.sha256(proposal.lock_bytes).hexdigest():
                raise GoalContractError("APPROVED_CANDIDATE_LOCK_DRIFT")
            core.validate_source_snapshot(candidate,timeout_seconds=_remaining(registration))
            if not core.source_snapshot_live_source_continuity(candidate,timeout_seconds=_remaining(registration)):
                raise GoalContractError("CANDIDATE_SOURCE_CHANGED_DURING_VERIFICATION")
            # Only this owner, after actual Core/source/lock checks, may supply
            # structured continuity observations. Commands cannot satisfy them.
            continuity={"isolatedCandidateSourceContinuity":"EXACT_HOST_DELTA_SOURCE_CONTINUITY_OBSERVED",
                "isolatedCandidateSourceScope":"PRIVATE_APPLIED_CANDIDATE_TO_PM_PREPARED_ONLY",
                "approvedDeltaDigest":proposal.delta_digest,"baselineSourceSnapshotKey":proposal.source_key,
                "candidateSourceSnapshotKey":candidate.key,"sourceOriginObservationDigest":canonical_digest(origin),
                "approvedLockSha256":hashlib.sha256(proposal.lock_bytes).hexdigest()}
            if admission is not None:observation["isolatedSourceContinuityObservation"]=continuity
            for item in observation["namedCheckObservations"]["checks"]:
                if item.get("kind")=="core" and item.get("evidence")=="SOURCE_LOCK_CONTINUITY":
                    item.update(status="CORE_CONTINUITY_OBSERVED",reason="ACTUAL_CORE_AND_CANDIDATE_GUARDS",
                        scope="CANDIDATE_INPUT_TO_PREPARED_AND_RESOLVED_LOCK",postPMSourceAuthorization="NOT_VERIFIED",
                        postPMCandidateSourceOrigin="OWNER_OBSERVED",sourceOriginObservationDigest=canonical_digest(origin),
                        candidateSourceSnapshotKey=candidate.key,
                        approvedLockSha256=hashlib.sha256(proposal.lock_bytes).hexdigest())
                    if admission is not None:
                        item.update(continuity)
                        item["contractFulfillment"]="SCOPED_OBSERVED"
            origin_class=require_registered_execution_class(registration)
            authority=("ENGINEERING_CONTROLLED_VERIFICATION_OBSERVED" if origin_class=="ENGINEERING_CONTROL"
                else "HOST_AUTHORIZED_PRIVATE_VERIFICATION_OBSERVED")
            observation.update(verificationAuthority=authority,
                baselineSourceSnapshotKey=proposal.source_key,candidateVerifiedSourceSnapshotKey=candidate.key,
                expectedObligationPlanDigest=dependency_plan.get("obligationPlanDigest"),
                approvedCandidateDigest=proposal.candidate_digest,approvedDeltaDigest=proposal.delta_digest,
                approvedLockSha256=hashlib.sha256(proposal.lock_bytes).hexdigest(),
                sourceIdentityScope="DISTINCT_BASELINE_AND_APPLIED_CANDIDATE",projectChecksExecuted=True,
                productionAuthorization=False)
            return observation
    finally:
        if old_ceiling is None:os.environ.pop("GIT_CEILING_DIRECTORIES",None)
        else:os.environ["GIT_CEILING_DIRECTORIES"]=old_ceiling


def execute_registered_engineering_candidate(run_id,candidate_id,binding_digest,authorization):
    """Compatibility entry: engineering callers cannot select normal authority."""
    registration=load_host_registration(run_id,candidate_id,binding_digest,authorization)
    if require_registered_execution_class(registration)!="ENGINEERING_CONTROL":
        raise GoalContractError("PRODUCTION_CANDIDATE_EXECUTION_ADMISSION_NOT_READY")
    return execute_registered_private_candidate(run_id,candidate_id,binding_digest,authorization)


def execute_registered_private_candidate(run_id,candidate_id,binding_digest,authorization):
    registration=load_host_registration(run_id,candidate_id,binding_digest,authorization)
    execution_class=require_registered_execution_class(registration)
    root=configured_state_root().resolve()
    cache_env=os.environ.get("DEPLOOM_MCP_OFFLINE_CACHE","")
    if not cache_env or not Path(cache_env).is_absolute():raise GoalContractError("OWNED_OFFLINE_CACHE_REQUIRED")
    cache=_plain_under(Path(cache_env),root)
    if not cache.is_dir():raise GoalContractError("OWNED_OFFLINE_CACHE_REQUIRED")
    core=_core();candidate_bytes=_candidate_bytes(registration)
    lock_version=npm_lock_format(_json(candidate_bytes[1],8*1024*1024))
    # Dedicated child environment only; never persists npm user/global settings.
    os.environ.update(npm_config_offline="true",npm_config_ignore_scripts="true",npm_config_audit="false",
        npm_config_fund="false",npm_config_update_notifier="false",npm_config_cache=str(cache),
        npm_config_lockfile_version=str(lock_version),npm_config_strict_peer_deps="true",npm_config_engine_strict="true")
    from deploom_mcp.goal_delta_trial import _write_ledger
    record=None;baseline_closed=False
    try:
        with core.verification_source_snapshot_scope(Path(registration.project_path),timeout_seconds=_remaining(registration),strict_cleanup=True) as baseline:
            proposal=propose_exact_delta(registration,baseline,*candidate_bytes)
            grant=load_host_delta_grant(run_id,candidate_id,binding_digest,APPLY_OPERATION,proposal)
            record=_apply_authorized_private_trial(grant,proposal,baseline,_verification_action=_verify_applied_candidate)
        baseline_closed=True
        # The caller-owned baseline snapshot must also retire before final
        # publication. The private helper receipt remains explicitly pending.
        _remaining(registration)
    except BaseException as exc:
        if record is not None:
            status=("POST_CLOSURE_DEADLINE_EXCEEDED" if baseline_closed and isinstance(exc,GoalContractError) and exc.code=="EXECUTION_DEADLINE_EXCEEDED"
                    else "POST_CLOSURE_FAILED" if baseline_closed else "BASELINE_CLOSURE_FAILED")
            record.update(status=status,allOwnedSnapshotCleanupConfirmed=baseline_closed)
            try:
                _write_ledger(trial_ledger_path(registration),record)
            except OSError as storage_error:
                exc.add_note("FAILURE_LEDGER_UPDATE_FAILED: "+type(storage_error).__name__)
        if str(exc) in {"SOURCE_SNAPSHOT_CLEANUP_INCOMPLETE","SOURCE_SNAPSHOT_STRICT_SCOPE_OWNERSHIP_REQUIRED"}:
            raise GoalContractError("OWNED_SOURCE_SNAPSHOT_CLOSURE_FAILED") from exc
        raise
    # Both outer owned scopes are closed here. This is still a historical
    # private observation, never a durable reusable user-goal checkpoint.
    for item in record.get("verificationObservation",{}).get("namedCheckObservations",{}).get("checks",[]):
        if item.get("kind")=="core" and item.get("evidence")=="OWNED_CLEANUP_CLOSED":
            item.update(status="OWNED_CLEANUP_OBSERVED",reason="ALL_OWNED_SNAPSHOT_SCOPES_CLOSED")
            if registration.profile.get("admission") is not None:item["contractFulfillment"]="SCOPED_OBSERVED"
    record.update(status="CONTROLLED_VERIFICATION_OBSERVED",allOwnedSnapshotCleanupConfirmed=True)
    if execution_class=="PREAPPROVED_HOST_POLICY":
        record.update(status="HOST_AUTHORIZED_PRIVATE_VERIFICATION_OBSERVED",
            privateExecutionAuthorized=True,privateExecutionAttempted=True,privateVerificationObserved=True,
            candidateProfileAccepted=False,goalChecksComplete=False,checkpointStatus="NOT_PUBLISHED",
            bindingDigest=registration.binding["bindingDigest"],
            sourceOriginObservationDigest=canonical_digest(record["verificationObservation"]["materializedSourceOrigin"]))
    if registration.profile.get("admission") is not None:
        try:
            current=load_host_registration(run_id,candidate_id,binding_digest,authorization)
            if current!=registration or not scoped_profile_requirements_complete(record,registration):
                raise GoalContractError("SCOPED_CANDIDATE_PROFILE_ADMISSION_UNPROVEN")
        except GoalContractError:
            record.update(status="SCOPED_PROFILE_ADMISSION_UNPROVEN")
            _write_ledger(trial_ledger_path(registration),record)
            raise
        record.update(status="HOST_AUTHORIZED_SCOPED_CANDIDATE_PROFILE_ACCEPTED",candidateProfileAccepted=True,
            goalChecksComplete=True,admissionScope="HISTORICAL_"+registration.profile["admission"]["scope"])
        record["verificationObservation"]["namedCheckObservations"]["goalChecksComplete"]=True
    _write_ledger(trial_ledger_path(registration),record)
    return record


def scoped_profile_requirements_complete(record,registration):
    """Pure check of owner-produced scoped facts; never a caller grant or apply."""
    if registration.policy.get('privateVerificationRoute') is not None:
        from deploom_mcp.goal_frozen_candidate import frozen_profile_complete
        return frozen_profile_complete(record,registration)
    from deploom_mcp.goal_check_bindings import validate_normative_admission,validate_verification_context
    try:
        if require_registered_execution_class(registration)!="PREAPPROVED_HOST_POLICY":return False
        if validate_normative_admission(registration.profile,registration.goal['requiredChecks']) is None:return False
        validate_verification_context(registration.goal,registration.profile,registration.binding['identity'])
        approval=registration.delta_authorization;o=record['verificationObservation']
        origin=o['materializedSourceOrigin'];tree=o['materializedDependencyObligations'];named=o['namedCheckObservations']
        continuity=o.get('isolatedSourceContinuityObservation',{})
        if not isinstance(continuity,dict):return False
        if (continuity.get('isolatedCandidateSourceContinuity')!='EXACT_HOST_DELTA_SOURCE_CONTINUITY_OBSERVED'
                or continuity.get('isolatedCandidateSourceScope')!='PRIVATE_APPLIED_CANDIDATE_TO_PM_PREPARED_ONLY'
                or continuity.get('approvedDeltaDigest')!=approval['deltaDigest']
                or continuity.get('baselineSourceSnapshotKey')!=approval['sourceSnapshotKey']
                or continuity.get('baselineSourceSnapshotKey')!=record.get('sourceSnapshotKey')
                or continuity.get('baselineSourceSnapshotKey')!=o.get('baselineSourceSnapshotKey')
                or continuity.get('candidateSourceSnapshotKey')!=o.get('candidateVerifiedSourceSnapshotKey')
                or continuity.get('candidateSourceSnapshotKey')!=origin.get('inputSourceSnapshotKey')
                or continuity.get('candidateSourceSnapshotKey')!=o.get('identities',{}).get('verified_source_snapshot_key')
                or continuity.get('sourceOriginObservationDigest')!=canonical_digest(origin)
                or continuity.get('approvedLockSha256')!=o.get('approvedLockSha256')
                or continuity.get('approvedLockSha256')!=origin.get('resolvedLockfileHash')
                or continuity.get('approvedLockSha256')!=o.get('identities',{}).get('resolved_lockfile_hash')
                or origin.get('status')!='SOURCE_ORIGIN_OBSERVED' or origin.get('authority')!='NON_REUSABLE_CORE_OBSERVATION'
                or record.get('authorizationOrigin')!='PREAPPROVED_HOST_POLICY'
                or record.get('bindingDigest')!=registration.binding['bindingDigest']
                or record.get('goalDigest')!=registration.binding['goalDigest']
                or record.get('profileDigest')!=canonical_digest(registration.profile)
                or record.get('candidateDigest')!=approval['candidateDigest'] or record.get('deltaDigest')!=approval['deltaDigest']
                or record.get('privateExecutionAuthorized') is not True or record.get('privateVerificationObserved') is not True
                or record.get('allOwnedSnapshotCleanupConfirmed') is not True
                or record.get('fullAcceptance') is not False or record.get('currentWorkspaceVerified') is not False
                or o.get('engineeringControl') is not False or o.get('observationComplete') is not True
                or o.get('approvedCandidateDigest')!=approval['candidateDigest'] or o.get('approvedDeltaDigest')!=approval['deltaDigest']
                or o.get('profileDigest')!=canonical_digest(registration.profile)
                or o.get('bindingDigest')!=registration.binding['bindingDigest']
                or o.get('commandChecks',{}).get('freshConfirmed')!=list(range(1,len(registration.profile['commands'])+1))
                or tree.get('status')!='MATERIALIZED_NPM_INSTANCE_OBLIGATIONS_OBSERVED'
                or tree.get('authority')!='NON_REUSABLE_CORE_OBSERVATION' or tree.get('finalBoundaryObserved') is not True
                or tree.get('obligationPlanDigest')!=o.get('expectedObligationPlanDigest')
                or tree.get('sourceOriginObservationDigest')!=canonical_digest(origin)
                or tree.get('approvedLockSha256')!=o.get('approvedLockSha256')
                or tree.get('resolvedStateKey')!=o.get('identities',{}).get('resolved_state_key')
                or type(tree.get('unknownCount')) is not int or tree['unknownCount']!=0 or tree.get('violations')!=[]
                or named.get('mappingStatus')!='HOST_PINNED'):
            return False
        contracts={r['checkId']:r for r in registration.profile['checkBindings']}
        checks=named['checks']
        if len(checks)!=len(contracts) or {r.get('checkId') for r in checks}!=set(contracts):return False
        for check in checks:
            expected=contracts[check['checkId']]
            wanted=('COMMAND_OBSERVED_PASS' if expected['kind']=='command' else
                'CORE_CONTINUITY_OBSERVED' if expected['evidence']=='SOURCE_LOCK_CONTINUITY' else 'OWNED_CLEANUP_OBSERVED')
            if (check.get('status')!=wanted or check.get('normativeScope')!='HOST_PINNED'
                    or check.get('contractFulfillment')!='SCOPED_OBSERVED'
                    or check.get('normativeContractDigest')!=canonical_digest(expected['normative'])):return False
            if expected.get('oracle') and check.get('oracleExecutedBytes')!='DIRECT_ARGV_BODY_OWNER_OBSERVED':return False
            if expected.get('evidence')=='SOURCE_LOCK_CONTINUITY' and any(check.get(key)!=value for key,value in continuity.items()):return False
        guards=tree.get('commandBoundaryGuards')
        if not isinstance(guards,list) or len(guards)!=len(registration.profile['commands']):return False
        for index,guard in enumerate(guards,1):
            if (type(guard.get('commandIndex')) is not int or guard['commandIndex']!=index
                    or guard.get('status')!='PRE_POST_OBSERVED'
                    or guard.get('obligationPlanDigest')!=tree['obligationPlanDigest']
                    or guard.get('materializedTreeObservationDigest')!=tree['materializedTreeObservationDigest']):return False
        return True
    except (GoalContractError,KeyError,TypeError,ValueError,AttributeError):
        return False
