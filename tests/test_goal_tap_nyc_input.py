"""Real Windows input hold and typed output facts, never CI/acceptance proof."""
import copy,hashlib,json,os,shutil,sys,tempfile,time,unittest
from pathlib import Path
from deploom_mcp.goal_tap_nyc_input import (source_config,recipe_hash,render_config,describe_lane,held_input,seal_observation,current_matches,validate_policy,KIND)
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.distribution import resolve_core_dir
sys.path.insert(0,str(resolve_core_dir()))
from verification_process_supervisor import run_supervised
from project_execution_environment import project_execution_environment,project_environment_fingerprint,V2

@unittest.skipUnless(os.name=='nt','Native held-input and output profiles require Windows handles')
class TapInputTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name).resolve();self.project=self.root/'project';self.project.mkdir();self.source=b'coverage: true'+bytes([10])+b'coverage-map: coverage-map.js'+bytes([10])+b'reporter: terse'+bytes([10])+b'files: [test/**/*.test.js]'+bytes([10]);(self.project/'.taprc.yaml').write_bytes(self.source);(self.project/'index.js').write_text('module.exports=1')
  self.deadline=time.time()+30;lanes={}
  for role in ('private','final'):
   base=self.root/role;base.mkdir();outputs={k:base/k for k in ('temp','report','cache')}
   for d in outputs.values():d.mkdir()
   cfg=base/'input.yaml';cfg.write_bytes(render_config(self.source,outputs));lanes[role]=describe_lane(cfg,outputs,deadline=self.deadline)
  self.policy={'schemaVersion':2,'kind':KIND,'sourcePath':'.taprc.yaml','sourceSha256':hashlib.sha256(self.source).hexdigest(),'recipeHash':recipe_hash(hashlib.sha256(self.source).hexdigest()),'lanes':lanes}
 def outputs(self,role):return {k:Path(v['path']) for k,v in self.policy['lanes'][role]['outputs'].items()}
 def emit(self,role):
  (self.outputs(role)['temp']/'data').mkdir(exist_ok=True)
  lines=['TN:','SF:index.js','DA:1,1','LF:1','LH:1','end_of_record'];(self.outputs(role)['report']/'lcov.info').write_text(chr(10).join(lines)+chr(10),encoding='utf-8')
 def record(self,role='final'):
  with held_input(self.policy,self.project,role,'a'*64,deadline=self.deadline) as obs:self.emit(role)
  return seal_observation(obs,'b'*32)
 def test_two_instances_same_recipe_but_different_real_bindings(self):
  a=self.record('private');b=self.record('final');self.assertEqual(a['recipeHash'],b['recipeHash']);self.assertNotEqual(a['inputBindingHash'],b['inputBindingHash']);self.assertFalse(a['fullAcceptance']);self.assertEqual(a['executionAuthority'],'NONE');self.assertTrue(current_matches(self.policy,self.project,b,deadline=self.deadline))
 def test_real_child_reads_input_but_cannot_write_or_rename(self):
  cfg=self.policy['lanes']['private']['yaml']['path'];env=project_execution_environment(os.environ,contract=V2,overrides={'TAP_RCFILE':cfg});fp=project_environment_fingerprint(env,contract=V2)
  body="const fs=require('fs'),a=require('assert/strict'),p=process.env.TAP_RCFILE;a(fs.readFileSync(p).length>0);a.throws(()=>fs.writeFileSync(p,'evil'));a.throws(()=>fs.renameSync(p,p+'.moved'));console.log('ACTUAL_HELD_READ_WRITE_RENAME_GUARDS_PASS')"
  with held_input(self.policy,self.project,'private','a'*64,deadline=self.deadline) as obs:
   x=run_supervised([shutil.which('node'),'-e',body],self.project,base_env=env,timeout_seconds=10,expected_project_environment_digest=fp);self.assertEqual(x.returncode,0,x.stdout);self.assertIn('ACTUAL_HELD_READ',x.stdout);self.assertEqual(x.supervision.descendants_remaining,0);self.emit('private')
  self.assertFalse(seal_observation(obs,fp)['fullAcceptance'])
 def test_source_and_config_byte_tampering_rejects(self):
  cfg=Path(self.policy['lanes']['private']['yaml']['path']);cfg.write_bytes(cfg.read_bytes()+b' ')
  with self.assertRaises(GoalContractError):
   with held_input(self.policy,self.project,'private','a'*64,deadline=self.deadline):pass
 def test_identical_bytes_new_identity_rejected(self):
  cfg=Path(self.policy['lanes']['private']['yaml']['path']);replacement=cfg.with_suffix('.new');replacement.write_bytes(cfg.read_bytes());replacement.replace(cfg)
  with self.assertRaises(GoalContractError):
   with held_input(self.policy,self.project,'private','a'*64,deadline=self.deadline):pass
 def test_duplicate_yaml_and_recipe_changes_rejected(self):
  with self.assertRaises(GoalContractError):source_config(self.source+b'coverage: false'+bytes([10]))
  cfg=Path(self.policy['lanes']['private']['yaml']['path']);cfg.write_bytes(cfg.read_bytes()+b'  - "--all=false"'+bytes([10]));self.policy['lanes']['private']=describe_lane(cfg,self.outputs('private'),deadline=self.deadline)
  with self.assertRaises(GoalContractError):
   with held_input(self.policy,self.project,'private','a'*64,deadline=self.deadline):pass
 def test_cross_role_overlap_and_nonempty_output_rejected(self):
  p=copy.deepcopy(self.policy);p['lanes']['final']['outputs']['temp']=copy.deepcopy(p['lanes']['private']['outputs']['temp'])
  with self.assertRaises(GoalContractError):validate_policy(p)
  (self.outputs('private')['temp']/'old').write_text('old')
  with self.assertRaises(GoalContractError):
   with held_input(self.policy,self.project,'private','a'*64,deadline=self.deadline):pass
 def test_missing_or_bad_lcov_is_not_observation_complete(self):
  with self.assertRaises(GoalContractError):
   with held_input(self.policy,self.project,'private','a'*64,deadline=self.deadline): (self.outputs('private')['temp']/'data').mkdir()
  with self.assertRaises(GoalContractError):
   with held_input(self.policy,self.project,'final','a'*64,deadline=self.deadline):
    (self.outputs('final')['temp']/'data').mkdir();(self.outputs('final')['report']/'lcov.info').write_text('wrong')
 def test_current_checks_actual_output_and_input_identity(self):
  r=self.record();self.assertTrue(current_matches(self.policy,self.project,r,deadline=self.deadline));(self.outputs('final')['report']/'lcov.info').write_text('changed');self.assertFalse(current_matches(self.policy,self.project,r,deadline=self.deadline))
 def test_hardlink_alias_is_not_allowed(self):
  cfg=Path(self.policy['lanes']['private']['yaml']['path']);alias=cfg.with_suffix('.alias');os.link(cfg,alias)
  with self.assertRaises(RuntimeError):
   with held_input(self.policy,self.project,'private','a'*64,deadline=self.deadline):pass

 def test_v1_policy_is_not_promoted(self):
  p=copy.deepcopy(self.policy);p['schemaVersion']=1
  with self.assertRaises(GoalContractError):validate_policy(p)
  p=copy.deepcopy(self.policy);p['kind']='PINNED_TAP_RCFILE_YAML_V1'
  with self.assertRaises(GoalContractError):validate_policy(p)
 def test_existing_empty_child_rejected_before_execution(self):
  (self.outputs('private')['temp']/'data').mkdir()
  with self.assertRaisesRegex(GoalContractError,'OUTPUT_NOT_EMPTY'):
   with held_input(self.policy,self.project,'private','a'*64,deadline=self.deadline):self.fail('must not enter')
 def test_same_bytes_new_child_identity_is_not_current(self):
  r=self.record();child=self.outputs('final')['temp']/'data';old=child.with_name('old')
  child.rename(old);child.mkdir();old.rmdir()
  self.assertFalse(current_matches(self.policy,self.project,r,deadline=self.deadline))
 def test_complete_manifest_and_seal_holds_child(self):
  from deploom_mcp.goal_tap_nyc_outputs import held_output_receipt
  self.emit('final');child=self.outputs('final')['temp']/'data';(child/'result.json').write_bytes(b'{}')
  with held_output_receipt(self.outputs('final'),deadline=self.deadline) as r:
   rows=r['manifests']['temp'];self.assertEqual([x['relativePath'] for x in rows],['','data','data/result.json'])
   self.assertEqual(rows[-1]['size'],2);self.assertEqual(rows[-1]['sha256'],hashlib.sha256(b'{}').hexdigest())
   with self.assertRaises(OSError):child.rename(child.with_name('foreign'))
   with self.assertRaises(OSError):(child/'result.json').write_bytes(b'bad')
 def test_addition_during_seal_prevents_receipt(self):
  from deploom_mcp.goal_tap_nyc_outputs import held_output_receipt
  self.emit('final')
  with self.assertRaisesRegex(RuntimeError,'INCOMPLETE'):
   with held_output_receipt(self.outputs('final'),deadline=self.deadline):(self.outputs('final')['temp']/'data'/'late').write_text('late')
 def test_final_junction_and_foreign_container_are_rejected(self):
  import _winapi
  outside=self.root/'outside';outside.mkdir();child=self.outputs('final')['temp']/'data'
  _winapi.CreateJunction(str(outside),str(child));self.emit('final')
  from deploom_mcp.goal_tap_nyc_outputs import held_output_receipt
  with self.assertRaises(RuntimeError):
   with held_output_receipt(self.outputs('final'),deadline=self.deadline):pass
  temp=self.outputs('private')['temp'];temp.rename(temp.with_name('oldtemp'));temp.mkdir()
  with self.assertRaisesRegex(GoalContractError,'OUTPUT_IDENTITY_CHANGED'):
   with held_input(self.policy,self.project,'private','a'*64,deadline=self.deadline):pass

if __name__=='__main__':unittest.main()
