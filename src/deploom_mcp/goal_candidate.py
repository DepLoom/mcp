"""Host-authorized isolated candidate checks; full goal acceptance remains separate."""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path
from deploom_mcp.goal_contract import GoalContractError,assert_current_binding,canonical_digest
from deploom_mcp.goal_execution_registry import load_host_registration,require_registered_execution_class,private_execution_budgets
from deploom_mcp.process_runner import run_process_async


def invoke_target_only(registration,*,verifier,config_factory,proof_cache_dir: Path,remaining_seconds: float,invocation_observer=None):
    """Code-only bridge tested with isolated doubles; not an MCP-supplied executor.

    The production worker does not connect this seam until source/lock/command
    receipt closure is available and reviewed. No fake result is persisted here.
    """
    goal=registration.goal
    if goal["goalKind"]!="functional-maintenance":
        raise GoalContractError("BASELINE_OBSERVATION_HAS_NO_EXECUTION_AUTHORITY")
    assert_current_binding(registration.binding,goal,registration.binding["identity"])
    if remaining_seconds<=0:raise GoalContractError("EXECUTION_DEADLINE_EXCEEDED")
    seconds=max(1,int(min(remaining_seconds,600)))
    config=config_factory(enabled=True,project_checks="strict",commands=tuple(registration.profile["commands"]),
        verification_purpose="intermediate-candidate",publish_durable_prepared_artifact=False,
        unbounded_preparation=False,timeout_seconds=seconds,attempt_timeout_seconds=seconds,
        budget_phase_deadline=time.monotonic()+min(remaining_seconds,600),proof_cache_dir=str(proof_cache_dir))
    options={"config":config,"run_project_checks":True}
    if invocation_observer is not None:options["invocation_observer"]=invocation_observer
    return verifier(Path(registration.project_path),dict(goal["exactTargets"]),**options)


def assess_target_only_result(result,*,target_assignment,expected_source_key,fingerprint):
    """Never confuse a target-only fingerprint, result.ok or observability with full proof."""
    actual_assignment=getattr(result,"verified_assignment_fingerprint","")
    source=getattr(result,"verified_source_snapshot_key","")
    assignment_status="MATCH" if actual_assignment and actual_assignment==fingerprint(target_assignment) else "MISSING_OR_MISMATCH"
    source_status="MATCH" if source and source==expected_source_key else "MISSING_OR_MISMATCH"
    kind=str(getattr(result,"kind","unknown"))
    invocation_outcome=("CHECK_FAILED" if not getattr(result,"ok",False) and kind in {"dependency","preparation","project"}
                        else "UNKNOWN" if not getattr(result,"ok",False) else "REPORTED_PASS_EVIDENCE_INCOMPLETE")
    return {"verifierReportedOk":bool(getattr(result,"ok",False)),"verifierKind":kind,"invocationOutcome":invocation_outcome,
        "targetOnlyFingerprintStatus":assignment_status,"targetOnlyMapDigest":canonical_digest(target_assignment),
        "fingerprintScope":"TARGET_MAP_ONLY_NOT_FULL_ASSIGNMENT","sourceSnapshotStatus":source_status,
        "heldRootObservation":"NOT_VERIFIED","scopeNecessity":"NOT_VERIFIED","commandReceiptClosure":"NOT_VERIFIED",
        "status":"EVIDENCE_INCOMPLETE","fullAcceptance":False,"verification":"NOT_VERIFIED"}


def _worker_argv(*registered_ids):
    return [sys.executable,"-B","-m","deploom_mcp.goal_candidate_worker",*registered_ids]


async def verify_goal_candidate(run_id: str,candidate_id: str,expected_binding_digest: str,authorization: str="") -> dict:
    try:
        registration=load_host_registration(run_id,candidate_id,expected_binding_digest,authorization)
        remaining=registration.deadline-time.time()
        if remaining<=0:raise GoalContractError("EXECUTION_DEADLINE_EXCEEDED")
        ids=(run_id,candidate_id,expected_binding_digest,authorization) if registration.candidate_files else ()
        budgets=private_execution_budgets(registration)
        process=await run_process_async(_worker_argv(*ids),cwd=str(Path(__file__).parent),env=dict(os.environ),
            timeout=min(remaining,budgets['workerSeconds'] if ids else 30),max_output_bytes=32768)
        if process.timed_out:
            return {"ok":False,"error":{"code":"EXECUTOR_READINESS_TIMEOUT"},"verifierInvoked":None if ids else False,"executionReady":False,"fullAcceptance":False,"invocationStatus":"UNKNOWN_AFTER_WORKER_TIMEOUT"}
        try:diagnostic=json.loads(process.stdout)
        except (ValueError,TypeError):diagnostic={}
        if registration.candidate_files:
            # An exit/stdout success never supplies project proof authority. Only
            # a completed host-bound durable engineering receipt is reportable.
            origin=(registration.delta_authorization or {}).get("origin")
            normal=origin=="PREAPPROVED_HOST_POLICY"
            scoped=normal and registration.profile.get("admission") is not None
            observation_status=("HOST_AUTHORIZED_PRIVATE_VERIFICATION_OBSERVED" if normal else "ENGINEERING_CONTROLLED_VERIFICATION_OBSERVED")
            status="HOST_AUTHORIZED_SCOPED_CANDIDATE_PROFILE_ACCEPTED" if scoped else observation_status
            if (process.returncode==0 and isinstance(diagnostic,dict)
                    and diagnostic.get("code")==status):
                from deploom_mcp.goal_verifier_bridge import trial_ledger_path
                path=trial_ledger_path(registration)
                if path.stat().st_size>128*1024:raise GoalContractError("INVALID_CONTROLLED_VERIFIER_RECEIPT")
                record=json.loads(path.read_bytes())
                # Reload the pinned host configuration after the child, before
                # observing its durable result. Revocation/drift cannot replay it.
                current=load_host_registration(run_id,candidate_id,expected_binding_digest,authorization)
                if current!=registration:raise GoalContractError("EXECUTION_REGISTRATION_CHANGED")
                require_registered_execution_class(current)
                approval=registration.delta_authorization or {}
                if not isinstance(record,dict):raise GoalContractError("INVALID_CONTROLLED_VERIFIER_RECEIPT")
                observation=record.get("verificationObservation",{})
                if not isinstance(observation,dict):raise GoalContractError("INVALID_CONTROLLED_VERIFIER_RECEIPT")
                if (canonical_digest(record)!=diagnostic.get("receiptDigest")
                        or record.get("status")!=(status if normal else "CONTROLLED_VERIFICATION_OBSERVED")
                        or record.get("allOwnedSnapshotCleanupConfirmed") is not True
                        or record.get("runId")!=run_id or record.get("candidateId")!=candidate_id
                        or record.get("candidateDigest")!=approval.get("candidateDigest")
                        or record.get("deltaDigest")!=approval.get("deltaDigest")
                        or record.get("authorizationOrigin")!=origin
                        or record.get("fullAcceptance") is not False
                        or observation.get("verificationAuthority")!=observation_status
                        or observation.get("bindingDigest")!=expected_binding_digest
                        or observation.get("fullAcceptance") is not False
                        or observation.get("projectChecksExecuted") is not True
                        or observation.get("observationComplete") is not True):
                    raise GoalContractError("INVALID_CONTROLLED_VERIFIER_RECEIPT")
                if time.time()>=registration.deadline:raise GoalContractError("EXECUTION_DEADLINE_EXCEEDED")
                if normal:
                    named=observation.get("namedCheckObservations",{})
                    source=observation.get("materializedSourceOrigin",{})
                    if not isinstance(named,dict) or not isinstance(source,dict):
                        raise GoalContractError("INVALID_CONTROLLED_VERIFIER_RECEIPT")
                    frozen_source=registration.policy.get('privateVerificationRoute') is not None
                    if frozen_source:
                        from deploom_mcp.goal_frozen_candidate import frozen_profile_complete
                        source_matches=frozen_profile_complete(record,registration)
                    else:
                        source_matches=(source.get('status')=='SOURCE_ORIGIN_OBSERVED' and source.get('authority')=='NON_REUSABLE_CORE_OBSERVATION')
                    if (record.get("bindingDigest")!=expected_binding_digest
                            or record.get("goalDigest")!=registration.binding["goalDigest"]
                            or record.get("profileDigest")!=canonical_digest(registration.profile)
                            or record.get("privateExecutionAuthorized") is not True
                            or record.get("privateExecutionAttempted") is not True
                            or record.get("privateVerificationObserved") is not True
                            or record.get("candidateProfileAccepted") is not scoped
                            or record.get("goalChecksComplete") is not scoped
                            or record.get("checkpointStatus")!="NOT_PUBLISHED"
                            or observation.get("engineeringControl") is not False
                            or named.get("mappingStatus")!="HOST_PINNED"
                            or named.get("goalChecksComplete") is not scoped
                            or not source_matches
                            or record.get("sourceOriginObservationDigest")!=canonical_digest(source)):
                        raise GoalContractError("INVALID_CONTROLLED_VERIFIER_RECEIPT")
                    if scoped:
                        from deploom_mcp.goal_verifier_bridge import scoped_profile_requirements_complete
                        if not scoped_profile_requirements_complete(record,registration):
                            raise GoalContractError("SCOPED_CANDIDATE_PROFILE_ADMISSION_UNPROVEN")
                    return {"ok":True,"status":status,"authorizationOrigin":origin,
                        "privateExecutionAuthorized":True,"privateExecutionAttempted":True,"privateVerificationObserved":True,
                        "candidateProfileAccepted":scoped,"goalChecksComplete":scoped,"checkpointStatus":"NOT_PUBLISHED",
                        "profileAdmission":({"status":"SCOPED_CANDIDATE_PROFILE_ACCEPTED","scope":"HISTORICAL_"+registration.profile["admission"]["scope"],
                            "externalInputClosure":"HOST_DECLARED_NOT_ENFORCED","arbitraryJSSemantics":"NOT_AUTOMATICALLY_PROVEN"}
                            if scoped else {"status":"PROFILE_ADMISSION_NOT_READY","remaining":["materialized held/transitive obligations","normative oracle applicability/input scope"]}),
                        "currentWorkspaceVerified":False,"observation":observation,"allOwnedSnapshotCleanupConfirmed":True,
                        "verifierInvoked":True,"executionReady":False,"verification":"NOT_VERIFIED",
                        "fullAcceptance":False,"productionAuthorization":False}
                return {"ok":True,"status":"ENGINEERING_CONTROLLED_VERIFICATION_OBSERVED",
                    "observation":observation,"allOwnedSnapshotCleanupConfirmed":True,"verifierInvoked":True,"executionReady":False,
                    "verification":"NOT_VERIFIED","fullAcceptance":False,"productionAuthorization":False}
            code=diagnostic.get("code") if isinstance(diagnostic,dict) else None
            allowed_errors={"FROZEN_CORE_VERIFICATION_INCOMPLETE","FROZEN_CORE_OBSERVATION_INCOMPLETE",
                "FROZEN_HOST_ADMISSION_REQUIRED","FROZEN_EMPTY_DEPENDENCY_TREE_REQUIRED","TRIAL_ALREADY_REGISTERED","DELTA_AUTHORIZATION_BINDING_MISMATCH",
                "HOST_AUTHORIZED_VERIFICATION_ACTION_REQUIRED","HOST_AUTHORIZED_NAMED_PROFILE_REQUIRED",
                "HOST_AUTHORIZED_CANDIDATE_FILES_REQUIRED","EXECUTION_REGISTRATION_CHANGED",
                "SCOPED_CANDIDATE_PROFILE_ADMISSION_UNPROVEN","SCOPED_ADMISSION_REQUIRES_NORMAL_HOST_AUTHORIZATION",
                "NORMATIVE_CHECK_SUBJECT_UNAVAILABLE","DEPENDENCY_OBLIGATION_OBSERVATION_MISMATCH",
                "DEPENDENCY_OBLIGATION_COMMAND_GUARD_MISMATCH",
                "PRODUCTION_CANDIDATE_EXECUTION_ADMISSION_NOT_READY","STALE_DELTA_BASELINE",
                "STALE_OR_INVALID_GOAL_BINDING","APPROVED_CANDIDATE_LOCK_DRIFT",
                "CORE_CANDIDATE_VERIFICATION_INCOMPLETE","OWNED_OFFLINE_CACHE_REQUIRED",
                "HOST_CHECK_ORACLE_UNAVAILABLE_OR_CHANGED","PROFILE_CHANGED_DURING_INVOCATION",
                "CORE_CANDIDATE_SOURCE_ORIGIN_UNCONFIRMED","SOURCE_ORIGIN_OBSERVATION_MISMATCH",
                "CANDIDATE_SOURCE_CAPTURE_MISMATCH","EXECUTION_DEADLINE_EXCEEDED","OWNED_SOURCE_SNAPSHOT_CLOSURE_FAILED"}
            return {"ok":False,"error":{"code":code if code in allowed_errors else "CONTROLLED_CANDIDATE_EXECUTION_NOT_CONFIRMED"},
                "verifierInvoked":None,"executionReady":False,"verification":"NOT_VERIFIED","fullAcceptance":False}
        # Worker stdout cannot create execution/proof authority. This increment
        # only recognizes its explicit NOT_READY diagnostic; no success branch.
        allowed={"CORE_ATTEMPT_RECEIPT_PROVIDER_UNAVAILABLE","CANDIDATE_EXECUTION_ADMISSION_NOT_READY","CORE_INTEGRITY_MISMATCH"}
        code=diagnostic.get("code") if isinstance(diagnostic,dict) else None
        if code not in allowed:code="INVALID_EXECUTOR_READINESS_RESULT"
        return {"ok":False,"error":{"code":code},"executionReady":False,"verifierInvoked":False,
            "verification":"NOT_VERIFIED","fullAcceptance":False,
            "remainingWork":["independent production validation of the Core observer connection","independent held-root and published-necessity evidence"]}
    except GoalContractError as exc:
        return {"ok":False,"error":{"code":exc.code},"executionReady":False,"verifierInvoked":False,"fullAcceptance":False}
    except (OSError,ValueError,TypeError,KeyError):
        return {"ok":False,"error":{"code":"EXECUTOR_INPUT_UNAVAILABLE"},"executionReady":False,"verifierInvoked":False,"fullAcceptance":False}
