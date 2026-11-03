"""Host-pinned finite-choice inputs and code-produced selection lineage.

Hashes identify trusted host bytes; this is not a signature/hostile-writer proof.
No caller paths, metadata JSON or claimed PASS are accepted by the MCP request.
"""
from __future__ import annotations
import dataclasses
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
from deploom_mcp.goal_contract import GoalContractError,bind_goal,canonical_digest
from deploom_mcp.goal_choice_contract import validate_choice_goal,exact_child
from deploom_mcp.goal_report import _plain_under,_SHA

def _artifact(root,folder,digest):
    if not isinstance(digest,str) or not _SHA.fullmatch(digest):raise GoalContractError('INVALID_CHOICE_DIGEST')
    path=_plain_under(root/folder/(digest+'.json'),root)
    if path.stat().st_size>8*1024*1024:raise GoalContractError('CHOICE_ARTIFACT_TOO_LARGE')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=digest:raise GoalContractError('CHOICE_ARTIFACT_CHANGED')
    value=json.loads(raw)
    if not isinstance(value,dict):raise GoalContractError('INVALID_CHOICE_ARTIFACT')
    return value

def load_inputs(root,parent):
    value=_artifact(root,'choice-inputs',parent['metadataDigest'])
    if (set(value)!={'schemaVersion','kind','createdUTC','expiresUTC','basePolicy','records'}
            or type(value['schemaVersion']) is not int or value['schemaVersion']!=1
            or value['kind']!='HOST_PINNED_ROOT_METADATA_V1'
            or not isinstance(value['basePolicy'],dict) or 'coupledChoice' in value['basePolicy']
            or value['basePolicy'].get('hardPolicyRequired') is not False
            or canonical_digest(value['basePolicy'])!=parent['policyDigest']):
        raise GoalContractError('INVALID_CHOICE_INPUT_BINDING')
    try:
        start=datetime.fromisoformat(value['createdUTC']);end=datetime.fromisoformat(value['expiresUTC'])
        now=datetime.now(timezone.utc)
        if start.tzinfo is None or end.tzinfo is None or not start<=now<end or (end-start).total_seconds()>86400:
            raise ValueError()
    except (ValueError,TypeError):raise GoalContractError('CHOICE_METADATA_STALE') from None
    return value

def build_model(parent,current,inputs,project):
    import sys
    from deploom_mcp.distribution import resolve_core_dir,release_manifest_path
    core=resolve_core_dir().resolve()
    release=json.loads(release_manifest_path().read_text(encoding='utf-8'))
    from deploom_mcp.goal_core_integrity import verify_core_files
    verify_core_files(core,release['core']['files'])
    for name in ('peer_solver_goal','peer_solver_model','peer_solver_z3','dependency_live_roadmap_generator'):
        module=sys.modules.get(name)
        if module is not None and Path(module.__file__).resolve()!=core/(name+'.py'):
            raise GoalContractError('CHOICE_CORE_MODULE_ORIGIN_MISMATCH')
    sys.path.insert(0,str(core))
    from peer_solver_goal import build_goal_choice_model,GoalChoiceUnsupported
    if not set(parent['mutablePackages']).issubset(current):raise GoalContractError('TARGET_NOT_DECLARED')
    if not set(parent['immutablePackages']+parent['excludedPackages']).issubset(current):raise GoalContractError('HELD_ROOT_NOT_DECLARED')
    domains={n:parent['candidateDomains'].get(n,[v]) for n,v in current.items()}
    try:
        return build_goal_choice_model(current,domains,inputs['records'],node_version=parent['nodeVersion'],
            project_dir=str(project),primary_package=parent['objective']['primaryPackage'],
            max_major_changes=parent['objective']['maxMajorChanges'])
    except GoalChoiceUnsupported as exc:raise GoalContractError('COUPLED_MODEL_UNSUPPORTED',str(exc)) from exc

def derive(receipt,inputs,receipt_digest,issuance_digest,parent_profile=None):
    parent=receipt['parentBinding']['goal']
    wrapper={'schemaVersion':1,'selectionDigest':receipt_digest,
        'parentGoalDigest':receipt['parentBinding']['goalDigest'],
        'parentBindingDigest':receipt['parentBinding']['bindingDigest'],'metadataDigest':parent['metadataDigest'],
        'issuanceDigest':issuance_digest}
    policy={**inputs['basePolicy'],'coupledChoice':wrapper}
    child=exact_child(parent,receipt['assignment'],canonical_digest(policy))
    identity={**receipt['parentBinding']['identity'],'policyDigest':child['policyDigest']}
    extra={}
    if 'verificationContext' in parent:
        from deploom_mcp.goal_choice_profile import rebind_profile
        child,profile=rebind_profile(parent,receipt['parentBinding']['identity'],child,identity,parent_profile)
        extra['executionProfile']=profile
    return {**extra,'kind':'DERIVED_EXACT_EXECUTION_INTENT_V1','parentGoalDigest':wrapper['parentGoalDigest'],
        'selectionDigest':receipt_digest,'goal':child,'binding':bind_goal(child,identity),'policy':policy,
        'authority':'PLANNING_ONLY','verification':'NOT_VERIFIED','executionReady':False}

def validate_lineage(root,goal,binding,policy,project,profile=None):
    wrapper=policy.get('coupledChoice')
    if 'coupledChoice' not in policy:return None
    if (not isinstance(wrapper,dict) or set(wrapper)!={'schemaVersion','selectionDigest','parentGoalDigest','parentBindingDigest','metadataDigest','issuanceDigest'} or type(wrapper['schemaVersion']) is not int or wrapper['schemaVersion']!=1):
        raise GoalContractError('INVALID_COUPLED_LINEAGE')
    receipt=_artifact(root,'choice-results',wrapper['selectionDigest'])
    if set(receipt)!={'schemaVersion','kind','parentBinding','currentRoots','assignment','score','modelDigest','status','backend'}:
        raise GoalContractError('INVALID_SELECTION_RECEIPT')
    parent_binding=receipt['parentBinding'];parent=validate_choice_goal(parent_binding['goal'])
    if bind_goal(parent,parent_binding['identity'])!=parent_binding:raise GoalContractError('INVALID_PARENT_BINDING')
    if (type(receipt['schemaVersion']) is not int or receipt['schemaVersion']!=1
            or receipt['kind']!='HOST_PRODUCED_ROOT_CHOICE_V1' or receipt['status']!='optimal' or receipt['backend']!='z3'):
        raise GoalContractError('SELECTION_NOT_FULL_OPTIMAL')
    from deploom_mcp.goal_choice_issuance import validate_issuance
    if validate_issuance(root,receipt,wrapper['selectionDigest'])!=wrapper['issuanceDigest']:
        raise GoalContractError('CORE_SELECTION_ISSUANCE_CHANGED')
    inputs=load_inputs(root,parent)
    model=build_model(parent,receipt['currentRoots'],inputs,project)
    if (canonical_digest(dataclasses.asdict(model))!=receipt['modelDigest']
            or not isinstance(receipt['assignment'],dict) or set(receipt['assignment'])!=set(model.package_map())
            or model.assignment_issue(receipt['assignment'])
            or list(model.assignment_score(receipt['assignment']))!=receipt['score']):
        raise GoalContractError('SELECTION_MODEL_MISMATCH')
    from deploom_mcp.goal_choice_profile import load_parent_profile
    parent_profile=load_parent_profile(root,parent,parent_binding['identity'])
    derived=derive(receipt,inputs,wrapper['selectionDigest'],wrapper['issuanceDigest'],parent_profile)
    if 'executionProfile' in derived and derived['executionProfile']!=profile:
        raise GoalContractError('COUPLED_PROFILE_REQUIREMENTS_CHANGED')
    if derived['policy']!=policy or derived['goal']!=goal or derived['binding']!=binding:
        raise GoalContractError('PARENT_TO_EXACT_LINEAGE_MISMATCH')
    # The policy's parent/selection hashes are now transitively bound into the
    # existing execution authorization, candidate proof and durable checkpoint.
    return {key:wrapper[key] for key in ('parentGoalDigest','parentBindingDigest','selectionDigest','metadataDigest')}
