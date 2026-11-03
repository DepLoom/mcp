"""Generated no-dependency project through actual Core/npm/Node owners.

This is a boundary regression fixture, not the real S2 migration or TAP coverage.
"""
import hashlib,json,os,time,unittest
from pathlib import Path
from unittest.mock import patch
import test_goal_tap_nyc_input as fixtures
from deploom_mcp.goal_contract import canonical_digest,GoalContractError
from deploom_mcp.goal_tap_nyc_execution import verify_with_tap_input,receipt_matches,physically_current
from deploom_mcp.goal_tap_nyc_input import KIND
from materialized_workspace_verifier import verify_current_workspace,MaterializedWorkspaceVerificationError
from source_snapshot import source_snapshot_fingerprint
from resolved_dependency_state import compile_npm_dependency_obligations
from project_execution_environment import V2

@unittest.skipUnless(os.name=='nt','Native held-input and output profiles require Windows handles')
class TapRealCoreTests(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.TapInputTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.deadline=time.time()+60
  self.env=patch.dict(os.environ,{'GIT_CEILING_DIRECTORIES':str(self.f.root),'DEPLOOM_MCP_STATE_DIR':str(self.f.root),'NODE_OPTIONS':'','NODE_PATH':''});self.env.start();self.addCleanup(self.env.stop)
  self.cache=self.f.root/'cache';self.cache.mkdir();os.environ['DEPLOOM_MCP_OFFLINE_CACHE']=str(self.cache)
  self.policy={'projectExecutionEnvironment':V2,'privateVerificationRoute':'FROZEN_EXACT_SINGLE_MATERIALIZATION_V1','tapNycInput':self.f.policy}
  self.profile={'commands':['npm run ci'],'checkBindings':[{'kind':'command','commandIndex':1,'normative':{'allowedInputs':{'oracle':'SEALED_PROJECT_COMMAND','externalInputs':{'kind':KIND,'recipeHash':self.f.policy['recipeHash']}}}}]}
  manifest={'name':'tap-input-core-boundary','version':'1.0.0','private':True,'scripts':{'ci':'node check.cjs'}}
  (self.f.project/'package.json').write_text(json.dumps(manifest),encoding='utf-8')
  self.raw=json.dumps({'name':manifest['name'],'version':'1.0.0','lockfileVersion':2,'requires':True,'packages':{'':{'name':manifest['name'],'version':'1.0.0'}}}).encode()
  (self.f.project/'package-lock.json').write_bytes(self.raw)
  self.body=r"""const fs=require('fs'),a=require('assert/strict'),p=require('path');
const cfg=fs.readFileSync(process.env.TAP_RCFILE,'utf8');a.throws(()=>fs.writeFileSync(process.env.TAP_RCFILE,'evil'));
const tmp=JSON.parse(cfg.split(/\r?\n/).find(x=>x.includes('--temp-dir=')).trim().slice(2)).slice('--temp-dir='.length);fs.mkdirSync(tmp,{recursive:true});
const line=cfg.split(/\r?\n/).find(x=>x.includes('--report-dir='));const arg=JSON.parse(line.trim().slice(2));
fs.writeFileSync(p.join(arg.slice('--report-dir='.length),'lcov.info'),'TN:\nSF:index.js\nDA:1,1\nLF:1\nLH:1\nend_of_record\n');
console.log('REAL_CORE_CHILD_OUTPUT_BOUNDARY_PASS');"""
  (self.f.project/'check.cjs').write_text(self.body,encoding='utf-8')
  self.plan=compile_npm_dependency_obligations(self.raw,self.raw,exact_targets={},changed_instances=[],immutable_packages=[],excluded_packages=[],delta_digest='a'*64,scope_digest='b'*64)
 def invoke(self,role,materialize):
  return verify_with_tap_input(verify_current_workspace,self.policy,self.profile,role,self.f.project,
    expected_source_key=source_snapshot_fingerprint(self.f.project),dependency_plan=self.plan,commands=self.profile['commands'],
    cache_dir=self.cache,deadline=self.f.deadline,final_profile_digest=canonical_digest(self.profile),materialize=materialize)
 def test_actual_frozen_install_private_and_live_final_receipts(self):
  key=source_snapshot_fingerprint(self.f.project);private,a=self.invoke('private',True);final,b=self.invoke('final',False)
  self.assertEqual(len(private['ownerOperationObservations']),2);self.assertEqual(len(final['ownerOperationObservations']),1)
  for role,raw,r in [('private',private,a),('final',final,b)]:
   self.assertTrue(receipt_matches(self.policy,self.profile,role,self.f.project,raw,r));self.assertFalse(raw['fullAcceptance']);self.assertFalse(raw['currentWorkspaceVerified'])
   self.assertEqual(raw['sourceSnapshotKey'],key);self.assertTrue(raw['sourceSnapshotCleanupClosed']);self.assertTrue(raw['ownedCleanupConfirmed'])
  self.assertEqual(a['inputObservation']['recipeHash'],b['inputObservation']['recipeHash']);self.assertNotEqual(a['inputObservation']['inputBindingHash'],b['inputObservation']['inputBindingHash'])
  self.assertTrue(physically_current(self.policy,self.profile,self.f.project,final,b,deadline=self.f.deadline))
  self.assertEqual(source_snapshot_fingerprint(self.f.project),key);self.assertFalse((self.f.project/'coverage').exists());self.assertFalse((self.f.project/'.nyc_output').exists())
  from types import SimpleNamespace
  from deploom_mcp.goal_final_checkpoint import live_complete,current_state_matches
  context=SimpleNamespace(registration=SimpleNamespace(policy=self.policy),final_profile=self.profile,accepted_receipt={'verificationObservation':{'candidateVerifiedSourceSnapshotKey':key}})
  self.assertTrue(live_complete(context,final,self.plan,self.f.project,b));self.assertFalse(live_complete(context,final,self.plan,self.f.project))
  record={'liveObservation':final,'tapInputObservation':b}
  self.assertTrue(current_state_matches(context,record,self.f.project))
  cfg=Path(self.f.policy['lanes']['final']['yaml']['path']);cfg.write_bytes(cfg.read_bytes()+b' ')
  self.assertFalse(current_state_matches(context,record,self.f.project))
 def test_install_time_output_injection_prevents_ci_native_spawn(self):
  import verification_process_supervisor as supervisor
  original=supervisor.prepare_unc_shell;popen=supervisor.subprocess.Popen;ci_started=[]
  def adapted(argv,cwd,env):
   values=original(argv,cwd,env)
   if argv[-1]=='npm run ci':(self.f.outputs('private')['temp']/'data').mkdir()
   return values
  def launch(argv,*a,**kw):
   if isinstance(argv,str) and 'npm run ci' in argv:ci_started.append(argv)
   return popen(argv,*a,**kw)
  key=source_snapshot_fingerprint(self.f.project)
  with patch.object(supervisor,'prepare_unc_shell',side_effect=adapted),patch.object(supervisor.subprocess,'Popen',side_effect=launch):
   with self.assertRaisesRegex(GoalContractError,'PRE_POPEN_INPUT_REJECTED'):self.invoke('private',True)
  self.assertEqual(ci_started,[]);self.assertTrue(Path(self.f.policy['lanes']['private']['yaml']['path']+'.consumed.json').is_file());self.assertEqual(source_snapshot_fingerprint(self.f.project),key)
 def test_actual_cwd_boundary_mismatch_prevents_ci_spawn(self):
  import verification_process_supervisor as supervisor
  original=supervisor.prepare_unc_shell
  def adapted(argv,cwd,env):
   a,c,e=original(argv,cwd,env)
   return (a,self.f.root,e) if argv[-1]=='npm run ci' else (a,c,e)
  with patch.object(supervisor,'prepare_unc_shell',side_effect=adapted):
   with self.assertRaisesRegex(GoalContractError,'PRE_POPEN_INPUT_REJECTED'):self.invoke('private',True)
  self.assertFalse((self.f.outputs('private')['report']/'lcov.info').exists())
 def test_missing_or_forged_native_witness_does_not_close_receipt(self):
  import copy
  raw,r=self.invoke('private',True)
  for change in ('missing','duplicate','unstarted','different-result'):
   bad=copy.deepcopy(raw);target=bad['ownerOperationObservations'][-1]
   if change=='missing':target.pop('guardedSpawnWitness')
   if change=='duplicate':bad['ownerOperationObservations'][0]['guardedSpawnWitness']=copy.deepcopy(target['guardedSpawnWitness'])
   if change=='unstarted':target['guardedSpawnWitness']['spawnStarted']=False
   if change=='different-result':target['guardedSpawnWitness']['nativeProcessResultId']='f'*32
   edited=copy.deepcopy(r);edited['coreObservationDigest']=canonical_digest(bad)
   with self.subTest(change=change):self.assertFalse(receipt_matches(self.policy,self.profile,'private',self.f.project,bad,edited))
 def test_entry_lease_does_not_enforce_later_empty_at_spawn(self):
  # Deliberately test the limited primitive without claiming execution authority.
  from deploom_mcp.goal_tap_nyc_input import held_input
  with self.assertRaisesRegex(RuntimeError,'OUTPUT_CAPTURE_INCOMPLETE'):
   with held_input(self.f.policy,self.f.project,'private',canonical_digest(self.profile),deadline=self.f.deadline) as obs:
    (self.f.outputs('private')['temp']/'injected-after-entry').write_text('foreign')
    self.f.emit('private')
  self.assertFalse(obs['fullAcceptance']);self.assertEqual(obs['executionAuthority'],'NONE')
  self.assertTrue((self.f.outputs('private')['temp']/'injected-after-entry').is_file())
 def test_actual_core_source_mutation_fails_and_consumes_slot(self):
  (self.f.project/'check.cjs').write_text(self.body+"\nfs.writeFileSync('index.js','evil');",encoding='utf-8')
  with self.assertRaisesRegex(MaterializedWorkspaceVerificationError,'INPUT_CHANGED'):self.invoke('private',True)
  self.assertTrue(Path(self.f.policy['lanes']['private']['yaml']['path']+'.consumed.json').is_file())
  self.assertEqual((self.f.project/'index.js').read_text(),'evil')

if __name__=='__main__':unittest.main()
