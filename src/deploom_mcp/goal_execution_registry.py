"""Host-configured execution authorization; imported observation records are never executable.

The manifest and its expected hash are startup/operator configuration, not MCP
request fields or a signing/attestation system. A hash identifies the operator's
chosen registry bytes; it does not prove that any project check has run.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime,timezone
from pathlib import Path
from deploom_mcp.goal_contract import GoalContractError,assert_current_binding,canonical_digest,validate_goal
from deploom_mcp.goal_report import _RUN,_SHA,_plain_under
from deploom_mcp.goal_check_bindings import validate_check_bindings,observe_oracles,validate_verification_context

OPERATION="verify-private-candidate"
FROZEN_ROUTE='FROZEN_EXACT_SINGLE_MATERIALIZATION_V1'

@dataclass(frozen=True)
class HostRegistration:
    run_id: str
    candidate_id: str
    project_path: str
    goal: dict
    binding: dict
    profile: dict
    policy: dict
    deadline: float
    manifest_sha256: str
    delta_authorization: dict | None = None
    candidate_files: dict | None = None
    final_authorization: dict | None = None


def require_registered_execution_class(registration):
    """Classify pinned host permission, not verification or publication authority."""
    approval=registration.delta_authorization or {}
    origin=approval.get("origin")
    if origin not in {"ENGINEERING_CONTROL","PREAPPROVED_HOST_POLICY"}:
        raise GoalContractError("AUTHORIZATION_UNAVAILABLE")
    if registration.profile.get("admission") is not None and origin!="PREAPPROVED_HOST_POLICY":
        raise GoalContractError("SCOPED_ADMISSION_REQUIRES_NORMAL_HOST_AUTHORIZATION")
    if origin=="PREAPPROVED_HOST_POLICY":
        if not registration.candidate_files:
            raise GoalContractError("HOST_AUTHORIZED_CANDIDATE_FILES_REQUIRED")
        if registration.profile.get("schemaVersion")!=2 or not registration.goal.get("verificationContext"):
            raise GoalContractError("HOST_AUTHORIZED_NAMED_PROFILE_REQUIRED")
        validate_check_bindings(registration.profile,registration.goal["requiredChecks"])
        validate_verification_context(registration.goal,registration.profile,registration.binding["identity"])
    policy=getattr(registration,'policy',{})
    if not isinstance(policy,dict):raise GoalContractError('INVALID_EXECUTION_POLICY')
    from deploom_mcp.goal_final_auxiliary import policy_paths
    from deploom_mcp.goal_current_proof_budget import registered_current_proof_spec
    registered_current_proof_spec(registration)
    auxiliary=policy_paths(policy)
    if auxiliary and origin!='PREAPPROVED_HOST_POLICY':raise GoalContractError('FINAL_AUXILIARY_HOST_POLICY_REQUIRED')
    from deploom_mcp.goal_execution_environment import environment_contract
    environment_contract(policy)
    from deploom_mcp.goal_generated_outputs import validate_host_policy
    validate_host_policy(policy,registration.profile,registration.project_path)
    declared_tap=any(isinstance(row.get('normative',{}).get('allowedInputs',{}).get('externalInputs'),dict)
        for row in registration.profile.get('checkBindings',[]))
    if declared_tap or 'tapNycInput' in policy or 'toolCache' in policy:
        if 'tapNycInput' not in policy:raise GoalContractError('TAP_NYC_HOST_POLICY_REQUIRED')
        from deploom_mcp.goal_tap_nyc_execution import validate_profile
        value=validate_profile(policy,registration.profile)
        owner_root=configured_state_root().resolve()
        for lane in value['lanes'].values():
            for path in [lane['yaml']['path'],*[row['path'] for row in lane['outputs'].values()]]:
                _plain_under(Path(path),owner_root)
        if origin!='PREAPPROVED_HOST_POLICY':raise GoalContractError('TAP_NYC_HOST_POLICY_REQUIRED')
        # Actual private/final owners additionally require the native spawn witness.
    route=policy.get('privateVerificationRoute')
    if route is not None and (route!=FROZEN_ROUTE or origin!='PREAPPROVED_HOST_POLICY' or registration.profile.get('admission') is None):
        raise GoalContractError('FROZEN_HOST_ADMISSION_REQUIRED')
    private_execution_budgets(registration)
    return origin


def private_execution_budgets(registration):
    """Operator-only bounded time allowance; never verification evidence."""
    policy=getattr(registration,'policy',{})
    if 'privateVerificationBudgetSeconds' not in policy:return {'coreSeconds':120,'workerSeconds':180,'explicit':False}
    seconds=policy['privateVerificationBudgetSeconds']
    if (type(seconds) is not int or not 120<=seconds<=300
            or policy.get('privateVerificationRoute')!=FROZEN_ROUTE
            or (registration.delta_authorization or {}).get('origin')!='PREAPPROVED_HOST_POLICY'
            or registration.profile.get('admission') is None):
        raise GoalContractError('PRIVATE_EXECUTION_BUDGET_UNPROVEN')
    return {'coreSeconds':seconds,'workerSeconds':seconds+120,'explicit':True}


def private_core_deadline(registration,now):
    import math
    budget=private_execution_budgets(registration)
    if not math.isfinite(now) or now>=registration.deadline:raise GoalContractError('EXECUTION_DEADLINE_EXCEEDED')
    return {'coreSeconds':budget['coreSeconds'],'workerSeconds':budget['workerSeconds'],
        'registeredDeadline':registration.deadline,'coreStartedAt':now,
        'effectiveCoreDeadline':min(registration.deadline,now+budget['coreSeconds'])}


def configured_state_root() -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.environ.get("DEPLOOM_MCP_STATE_DIR",str(Path.home()/".deploom-mcp")))))


def load_host_registration(run_id: str,candidate_id: str,expected_binding_digest: str,authorization: str,*,state_root: Path|None=None,allow_expired_read: bool=False) -> HostRegistration:
    if not all(isinstance(v,str) and _RUN.fullmatch(v) for v in [run_id,candidate_id]) or not isinstance(expected_binding_digest,str) or not _SHA.fullmatch(expected_binding_digest):
        raise GoalContractError("INVALID_EXECUTION_REQUEST")
    if type(allow_expired_read) is not bool:raise GoalContractError('INVALID_EXECUTION_REQUEST')
    if authorization!=OPERATION:
        raise GoalContractError("EXPLICIT_EXECUTION_AUTHORIZATION_REQUIRED")
    root=state_root or configured_state_root()
    imported=_plain_under(root/"goal-runs"/(run_id+".json"),root)
    if imported.exists():
        raise GoalContractError("IMPORTED_RUN_NOT_EXECUTABLE")
    location=os.environ.get("DEPLOOM_MCP_EXECUTION_MANIFEST")
    expected=os.environ.get("DEPLOOM_MCP_EXECUTION_MANIFEST_SHA256","")
    if not location or not _SHA.fullmatch(expected):
        raise GoalContractError("EXECUTION_REGISTRY_NOT_CONFIGURED")
    if not Path(location).is_absolute():raise GoalContractError("INVALID_EXECUTION_REGISTRY_PATH")
    path=_plain_under(Path(location),root)
    if path.stat().st_size>1024*1024:raise GoalContractError("EXECUTION_REGISTRY_TOO_LARGE")
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:
        raise GoalContractError("EXECUTION_REGISTRY_CHANGED")
    manifest=json.loads(raw)
    if not isinstance(manifest,dict) or set(manifest)!={"schemaVersion","entries"} or type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"]!=1 or not isinstance(manifest["entries"],list) or len(manifest["entries"])>64:
        raise GoalContractError("INVALID_EXECUTION_REGISTRY")
    matches=[e for e in manifest["entries"] if isinstance(e,dict) and e.get("runId")==run_id and e.get("candidateId")==candidate_id]
    if len(matches)!=1:raise GoalContractError("EXECUTION_CANDIDATE_NOT_REGISTERED")
    entry=matches[0]
    required={"runId","candidateId","projectPath","goal","binding","profile","policy","authorization","status","deadlineUTC"}
    if not required.issubset(entry) or set(entry)-required-{"deltaAuthorization","candidateFiles","finalAuthorization"}:
        raise GoalContractError("INVALID_EXECUTION_REGISTRATION")
    final_authorization=entry.get("finalAuthorization")
    if final_authorization is not None and not isinstance(final_authorization,dict):
        raise GoalContractError("INVALID_FINAL_AUTHORIZATION")
    candidate_files=entry.get("candidateFiles")
    if candidate_files is not None:
        if (not isinstance(candidate_files,dict) or not {"manifest","lock"}.issubset(candidate_files)
                or set(candidate_files)-{"manifest","lock","sources"}
                or any(not isinstance(v,str) or not v or len(v)>512 or Path(v).is_absolute()
                       or ".." in Path(v).parts for k,v in candidate_files.items() if k!="sources")):
            raise GoalContractError("INVALID_HOST_CANDIDATE_FILES")
    delta=entry.get("deltaAuthorization")
    if delta is not None:
        fields={"schemaVersion","operation","origin","candidateDigest","deltaDigest","bindingDigest","profileDigest","sourceSnapshotKey","transitiveScope","stateRootDigest"}
        if (not isinstance(delta,dict) or set(delta)!=fields or type(delta["schemaVersion"]) is not int or delta["schemaVersion"]!=1
                or delta["operation"]!="apply-private-candidate"
                or delta["origin"] not in {"ENGINEERING_CONTROL","PREAPPROVED_HOST_POLICY"}
                or delta["transitiveScope"] not in {"ROOT_TARGETS_ONLY","EXACT_HOST_ENUMERATED"}
                or any(not isinstance(delta[k],str) or not _SHA.fullmatch(delta[k]) for k in ("candidateDigest","deltaDigest","bindingDigest","profileDigest","stateRootDigest"))
                or not isinstance(delta["sourceSnapshotKey"],str) or not 1<=len(delta["sourceSnapshotKey"])<=128):
            raise GoalContractError("INVALID_DELTA_AUTHORIZATION")
    if entry["status"] not in ({"OPEN","CLOSED"} if allow_expired_read else {"OPEN"}):raise GoalContractError("EXECUTION_RUN_CLOSED")
    goal=validate_goal(entry["goal"])
    if goal["goalKind"]!="functional-maintenance":
        raise GoalContractError("BASELINE_OBSERVATION_HAS_NO_EXECUTION_AUTHORITY")
    if candidate_files is not None:
        sources=candidate_files.get("sources", {})
        allow={e["path"] for e in goal.get("sourceRepairAllowlist", [])}
        if (not isinstance(sources,dict) or set(sources)!=allow
                or any(not isinstance(v,str) or not v or len(v)>512 or Path(v).is_absolute()
                       or ".." in Path(v).parts for v in sources.values())):
            raise GoalContractError("INVALID_HOST_SOURCE_REPAIR_FILES")
    binding=entry["binding"]
    if not isinstance(binding,dict):raise GoalContractError("INVALID_EXECUTION_REGISTRATION")
    assert_current_binding(binding,goal,binding.get("identity",{}))
    if binding["bindingDigest"]!=expected_binding_digest:
        raise GoalContractError("STALE_OR_INVALID_GOAL_BINDING")
    profile=entry["profile"]
    if (not isinstance(profile,dict) or set(profile) not in ({"commands","requiredChecks","projectChecks","verificationPurpose"}, {"schemaVersion","commands","requiredChecks","projectChecks","verificationPurpose","checkBindings"}, {"schemaVersion","commands","requiredChecks","projectChecks","verificationPurpose","checkBindings","admission"})
            or profile["projectChecks"]!="strict" or profile["verificationPurpose"]!="intermediate-candidate"
            or not isinstance(profile["requiredChecks"],list) or any(not isinstance(c,str) for c in profile["requiredChecks"]) or sorted(profile["requiredChecks"])!=sorted(goal["requiredChecks"])
            or not isinstance(profile["commands"],list) or not 1<=len(profile["commands"])<=64
            or any(not isinstance(c,str) or not c.strip() or len(c)>4096 for c in profile["commands"])):
        raise GoalContractError("INVALID_EXECUTION_PROFILE")
    validate_check_bindings(profile,goal["requiredChecks"])
    validate_verification_context(goal,profile,binding["identity"])
    policy=entry["policy"]
    if not isinstance(policy,dict) or canonical_digest(policy)!=goal["policyDigest"] or type(policy.get("hardPolicyRequired")) is not bool:
        raise GoalContractError("POLICY_BINDING_MISMATCH")
    from deploom_mcp.goal_current_proof_budget import parse_current_proof_spec
    parse_current_proof_spec(policy)
    # An operator's JSON flag is not hard-policy evidence. A future owning
    # admission provider must close these gates; never accept a claimed PASS.
    if policy["hardPolicyRequired"]:
        raise GoalContractError("HARD_POLICY_ADMISSION_PROVIDER_UNAVAILABLE")
    if "coupledChoice" in policy:
        from deploom_mcp.goal_choice_lineage import validate_lineage
        validate_lineage(root,goal,binding,policy,entry["projectPath"],profile=profile)
    wanted={"operation":OPERATION,"bindingDigest":binding["bindingDigest"],"profileDigest":canonical_digest(profile),"targetMapDigest":canonical_digest(goal["exactTargets"])}
    if entry["authorization"]!=wanted:raise GoalContractError("EXECUTION_AUTHORIZATION_BINDING_MISMATCH")
    project=entry["projectPath"]
    if not isinstance(project,str) or not Path(project).is_absolute():raise GoalContractError("INVALID_PROJECT_PATH")
    if any(value!="MATCH" for value in observe_oracles(profile,root,project).values()):
        raise GoalContractError("HOST_CHECK_ORACLE_UNAVAILABLE_OR_CHANGED")
    deadline=datetime.fromisoformat(entry["deadlineUTC"])
    if deadline.tzinfo is None or (not allow_expired_read and deadline.timestamp()<=datetime.now(timezone.utc).timestamp()):
        raise GoalContractError("EXECUTION_DEADLINE_EXCEEDED")
    registration=HostRegistration(run_id,candidate_id,project,copy.deepcopy(goal),copy.deepcopy(binding),copy.deepcopy(profile),copy.deepcopy(policy),deadline.timestamp(),expected,copy.deepcopy(delta),copy.deepcopy(candidate_files),copy.deepcopy(final_authorization))
    if delta and delta["origin"]=="PREAPPROVED_HOST_POLICY":
        require_registered_execution_class(registration)
    return registration
