"""Real read-only worker context plus backwards-compatible optional attachment."""
import asyncio,copy,hashlib,json,os,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from deploom_mcp.goal_contract import bind_goal,canonical_digest
from deploom_mcp.goal_worker import prepare_goal_plan,observe_goal_source
from deploom_mcp.goal_tools import get_goal_plan
from deploom_mcp.migration_worker import run_evidence
from deploom_mcp.migration_evidence import SCHEMA,build_reference_section
from deploom_mcp.process_runner import ProcessRun

class GoalReferenceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve();self.project=self.root/'project';self.project.mkdir();self.state=self.root/'state'
        package={'name':'reference-fixture','version':'1.0.0','dependencies':{'strip-json-comments':'3.1.1'}}
        lock={'lockfileVersion':3,'packages':{'':package,'node_modules/strip-json-comments':{'version':'3.1.1'}}}
        (self.project/'package.json').write_text(json.dumps(package),encoding='utf-8');(self.project/'package-lock.json').write_text(json.dumps(lock),encoding='utf-8')
        (self.project/'entry.js').write_text("const strip=require('strip-json-comments');",encoding='utf-8')
        self.goal={'schemaVersion':1,'goalKind':'functional-maintenance','healthPolicy':'REPORT_ONLY','goalId':'reference-fixture',
            'exactTargets':{'strip-json-comments':'5.0.0'},'mutablePackages':['strip-json-comments'],'immutablePackages':[],
            'excludedPackages':[],'requiredChecks':['install','runtime'],'policyDigest':canonical_digest({'healthPolicy':'REPORT_ONLY'}),
            'acceptPartial':False,'allowSourceRepair':False}
        self.request={'schema':SCHEMA,'mode':'ON_DEMAND','transitions':[{'package':'strip-json-comments','from':'3.1.1','to':'5.0.0'}],'detail':'summary','localApplicability':'OFF'}
        self.env=patch.dict(os.environ,{'DEPLOOM_MCP_STATE_DIR':str(self.state),'DEPLOOM_MCP_MIGRATION_OFFLINE':'1'})
        self.env.start();self.addCleanup(self.env.stop)
    async def test_default_and_explicit_none_remain_identical_and_no_reference_worker(self):
        payload=prepare_goal_plan(self.project,self.goal)
        mock=AsyncMock(return_value=ProcessRun(0,json.dumps(payload),''))
        with patch('deploom_mcp.goal_tools.run_process_async',mock),patch('deploom_mcp.migration_evidence.attach_to_plan',side_effect=AssertionError('default attached references')):
            first=await get_goal_plan(str(self.project),self.goal);second=await get_goal_plan(str(self.project),self.goal,migration_evidence_request=None)
        self.assertEqual(first,second);self.assertNotIn('officialMigrationEvidence',first)
        self.assertTrue(all(call.args[0][3]=='deploom_mcp.goal_worker' for call in mock.call_args_list))
    async def test_real_optional_worker_offline_reference_preserves_plan_authority(self):
        result=await get_goal_plan(str(self.project),self.goal,20,self.request)
        self.assertTrue(result['ok']);self.assertFalse(result['executionReady']);self.assertEqual(result['verification'],'NOT_VERIFIED')
        section=result['officialMigrationEvidence'];self.assertEqual(section['verificationAuthority'],'NONE');self.assertIn(section['status'],['UNKNOWN','PARTIAL'])
        self.assertEqual(section['goalBindingDigest'],result['binding']['bindingDigest'])
        self.assertEqual(section.get('metrics',{}).get('httpTransactions',0),0)
    async def test_project_and_host_state_overlap_rejected_before_cache_or_writes(self):
        payload=prepare_goal_plan(self.project,self.goal)
        context={'projectPath':str(self.project),'binding':payload['binding'],'assignment':payload['assignment'],'request':self.request}
        state=self.project/'must-not-exist'
        with patch.dict(os.environ,{'DEPLOOM_MCP_STATE_DIR':str(state)}),patch('deploom_mcp.migration_worker.SourceStore',side_effect=AssertionError('cache reached')):
            with self.assertRaisesRegex(ValueError,'EVIDENCE_STATE_OVERLAP_REJECTED'):run_evidence(context,10)
        self.assertFalse(state.exists())
        with patch.dict(os.environ,{'DEPLOOM_MCP_STATE_DIR':str(self.project.parent)}),patch('deploom_mcp.migration_worker.SourceStore',side_effect=AssertionError('cache reached')):
            with self.assertRaisesRegex(ValueError,'EVIDENCE_STATE_OVERLAP_REJECTED'):run_evidence(context,10)
    async def test_goal_transition_mismatch_rejects_before_any_fetch(self):
        payload=prepare_goal_plan(self.project,self.goal);request=copy.deepcopy(self.request);request['transitions'][0]['from']='3.0.0'
        context={'projectPath':str(self.project),'binding':payload['binding'],'assignment':payload['assignment'],'request':request}
        with patch('deploom_mcp.migration_sources.OfficialHTTP.read',side_effect=AssertionError('mismatch fetched')):
            with self.assertRaisesRegex(ValueError,'GOAL_TRANSITION_MISMATCH'):run_evidence(context,10)
    async def test_actual_source_change_marks_usage_stale_not_current(self):
        payload=prepare_goal_plan(self.project,self.goal);context={'projectPath':str(self.project),'binding':payload['binding'],'assignment':payload['assignment'],'request':self.request}
        actual=build_reference_section
        def change(*args,**kwargs):
            result=actual(*args,**kwargs);(self.project/'entry.js').write_text('changed',encoding='utf-8');return result
        with patch('deploom_mcp.migration_worker.build_reference_section',side_effect=change):result=run_evidence(context,15)
        self.assertEqual(result['bindingStatus'],'STALE');self.assertEqual(result['transitions'][0]['applicability']['status'],'STALE')
        self.assertEqual(result['verificationAuthority'],'NONE')
