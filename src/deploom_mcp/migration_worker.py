"""Optional bounded reference worker. It cannot register/verify/apply a goal."""
from __future__ import annotations
import json,os,sys,time
from pathlib import Path
from deploom_mcp.goal_contract import GoalContractError,assert_current_binding
from deploom_mcp.goal_worker import observe_goal_source
from deploom_mcp.goal_execution_registry import configured_state_root
from deploom_mcp.migration_sources import SourceStore,EvidenceUnavailable
from deploom_mcp.migration_evidence import build_reference_section,validate_request,unknown,_app

def run_evidence(context,seconds):
    start=time.monotonic();deadline=start+min(30.0,max(0.0,seconds));cpu_start=time.process_time()
    if not isinstance(context,dict) or set(context)!={'projectPath','binding','assignment','request'}:raise EvidenceUnavailable('INVALID_EVIDENCE_CONTEXT')
    request=validate_request(context['request']);project=Path(context['projectPath']);binding=context['binding']
    if not isinstance(binding,dict) or not isinstance(binding.get('identity'),dict):raise EvidenceUnavailable('EVIDENCE_BINDING_UNAVAILABLE')
    state=configured_state_root();resolved_state=state.resolve();resolved_project=project.resolve()
    if resolved_state.is_relative_to(resolved_project) or resolved_project.is_relative_to(resolved_state):
        raise EvidenceUnavailable('EVIDENCE_STATE_OVERLAP_REJECTED')
    observed=observe_goal_source(project,binding['identity']['policyDigest'])
    assert_current_binding(binding,binding['goal'],observed['identity'])
    if time.monotonic()>=deadline:raise EvidenceUnavailable('EVIDENCE_DEADLINE')
    for row in request['transitions']:
        record=observed['lock']['packages'].get('node_modules/'+row['package'],{})
        if row['package'] not in observed['roots'] or record.get('version')!=row['from'] or context['assignment'].get(row['package'])!=row['to']:
            raise EvidenceUnavailable('GOAL_TRANSITION_MISMATCH')
    store=SourceStore(state/'migration-evidence',deadline=deadline,
        offline=os.environ.get('DEPLOOM_MCP_MIGRATION_OFFLINE')=='1')
    section=build_reference_section(request,store,binding_digest=binding['bindingDigest'])
    if request['localApplicability']=='ON':
        from deploom_mcp.migration_usage import inspect_usage
        app=inspect_usage(project,[x['package'] for x in request['transitions']],state,deadline=deadline,
            source_identity=observed['identity']['sourceDigest'])
        section['metrics']['nodeParserReportedCpuSeconds']=app.get('parserReportedCpuSeconds')
        for row in section['transitions']:
            row['applicability']={**app,'observations':[x for x in app['observations'] if x['package']==row['package']]}
    confirmed=observe_goal_source(project,binding['identity']['policyDigest'])
    if confirmed['identity']!=observed['identity']:
        section['bindingStatus']='STALE'
        for row in section['transitions']:row['applicability']=_app(status='STALE',reason='SOURCE_OR_CONTEXT_CHANGED_DURING_EVIDENCE')
    else:section['bindingStatus']='MATCHES_OBSERVED_GOAL_CONTEXT'
    section['metrics'].update(workerWallSeconds=time.monotonic()-start,workerPythonCpuSeconds=time.process_time(),
        cpuScope='WORKER_PYTHON_PROCESS; NODE_PARSER_REPORTED_SEPARATELY; TOOLCHAIN_SUBPROCESS_CPU_UNMEASURED')
    return section

def main():
    binding_digest=None
    try:
        if len(sys.argv)!=3 or len(sys.argv[1])>65536:raise EvidenceUnavailable('INVALID_EVIDENCE_REQUEST')
        context=json.loads(sys.argv[1]);seconds=float(sys.argv[2]);binding_digest=context.get('binding',{}).get('bindingDigest')
        if not 0<seconds<=30:raise EvidenceUnavailable('INVALID_EVIDENCE_DEADLINE')
        result=run_evidence(context,seconds)
    except GoalContractError as exc:result=unknown(exc.code,binding_digest=binding_digest)
    except EvidenceUnavailable as exc:result=unknown(str(exc),binding_digest=binding_digest)
    except (OSError,ValueError,KeyError,TypeError,ImportError):result=unknown('MIGRATION_EVIDENCE_UNAVAILABLE',binding_digest=binding_digest)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False));return 0
if __name__=='__main__':raise SystemExit(main())
