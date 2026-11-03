"""Read-only finite-parent plan worker; no project commands or MCP authority gain."""
from __future__ import annotations
import dataclasses
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from deploom_mcp.goal_contract import GoalContractError,bind_goal,canonical_digest,_roots
from deploom_mcp.goal_choice_contract import validate_choice_goal
from deploom_mcp.goal_choice_lineage import load_inputs,build_model,derive
from deploom_mcp.goal_execution_registry import configured_state_root
from deploom_mcp.goal_report import _plain_under
from deploom_mcp.goal_worker import observe_goal_source

def prepare_choice_plan(project,parent):
    parent=validate_choice_goal(parent);root=configured_state_root()
    inputs=load_inputs(root,parent)
    observed=observe_goal_source(project,parent['policyDigest'])
    try:current={n:observed['lock']['packages']['node_modules/'+n]['version'] for n in observed['roots']}
    except (KeyError,TypeError):raise GoalContractError('CURRENT_ROOT_INVENTORY_INCOMPLETE') from None
    from deploom_mcp.process_runner import run_process
    node=shutil.which('node')
    version=run_process([node,'--version'],cwd=str(project),env=dict(os.environ),timeout=5)
    if version.returncode or version.timed_out or version.stdout.strip()!='v'+parent['nodeVersion']:
        raise GoalContractError('CHOICE_NODE_CONTEXT_MISMATCH')
    model=build_model(parent,current,inputs,project)
    from peer_solver_goal import solve_goal_choice,GoalChoiceUnsupported
    try:result=solve_goal_choice(model,timeout_ms=10000)
    except GoalChoiceUnsupported as exc:raise GoalContractError('COUPLED_SOLVER_RESULT_INVALID',str(exc)) from exc
    if result.status!='optimal' or result.backend!='z3':raise GoalContractError('COUPLED_SELECTION_NOT_OPTIMAL',result.status)
    # Reobserve exact source/lock/toolchain after solving; never bind a stale probe.
    confirmed=observe_goal_source(project,parent['policyDigest'])
    if confirmed['identity']!=observed['identity']:raise GoalContractError('CHOICE_CONTEXT_CHANGED')
    binding=bind_goal(parent,observed['identity'])
    from deploom_mcp.goal_choice_profile import load_parent_profile
    parent_profile=load_parent_profile(root,parent,binding['identity'])
    receipt={'schemaVersion':1,'kind':'HOST_PRODUCED_ROOT_CHOICE_V1','parentBinding':binding,
        'currentRoots':current,'assignment':result.assignment,'score':list(result.score),
        'modelDigest':canonical_digest(dataclasses.asdict(model)),'status':result.status,'backend':result.backend}
    raw=json.dumps(receipt,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
    digest=hashlib.sha256(raw).hexdigest()
    target=_plain_under(root/'choice-results'/(digest+'.json'),root);target.parent.mkdir(parents=True,exist_ok=True)
    try:
        with target.open('xb') as stream:stream.write(raw)
    except FileExistsError:
        if target.read_bytes()!=raw:raise GoalContractError('SELECTION_RECEIPT_CONFLICT')
    from deploom_mcp.goal_choice_issuance import issue_selection
    issuance_digest=issue_selection(root,receipt,digest,result)
    intent=derive(receipt,inputs,digest,issuance_digest,parent_profile)
    return {'ok':True,'kind':'coupled-root-choice-plan','planId':binding['bindingDigest'],'binding':binding,
        'assignment':result.assignment,'modelStatus':'OPTIMAL_DECLARED_ROOT_IR_ONLY',
        'derivedExactIntent':intent,'selectionEvidenceDigest':digest,'solverElapsedMs':result.elapsed_ms,
        'authority':'PLANNING_ONLY','verification':'NOT_VERIFIED','executionReady':False,
        'fullTreeStatus':'NOT_EVALUATED','installationStatus':'NOT_RUN','runtimeStatus':'NOT_RUN',
        'metadataAuthority':'HOST_PINNED_NOT_ATTESTED'}

def main():
    try:
        if len(sys.argv)!=3 or len(sys.argv[2])>65536:raise GoalContractError('INVALID_GOAL_REQUEST')
        result=prepare_choice_plan(Path(sys.argv[1]),json.loads(sys.argv[2]))
    except GoalContractError as exc:result={'ok':False,'error':{'code':exc.code,'detail':str(exc)},'verification':'NOT_VERIFIED'}
    except (OSError,ValueError,KeyError,TypeError):result={'ok':False,'error':{'code':'CHOICE_INPUT_UNAVAILABLE'},'verification':'NOT_VERIFIED'}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False));return 0 if result['ok'] else 1
if __name__=='__main__':raise SystemExit(main())
