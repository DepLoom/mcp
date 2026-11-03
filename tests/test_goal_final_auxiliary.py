"""Real bounded filesystem preparation; pure receipt rejection tests aren't project PASS."""
import copy,json,os,subprocess,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_final_auxiliary import (MODE,policy_paths,validate_layout,observed_paths,prepare_observed_auxiliary,validate_live_auxiliary)

class AuxiliaryPreparationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.project=self.root/'dependency-stage/repo';(self.project/'node_modules').mkdir(parents=True)
  self.paths=['node_modules/.vite-temp'];self.policy={'finalAuxiliaryPreparation':{'schemaVersion':1,'mode':MODE,'paths':self.paths}}
  self.plan={'instances':[{'path':'node_modules/vite'}],'obligationPlanDigest':'p'*64}
 def test_default_no_preparation_and_explicit_policy(self):
  self.assertEqual(policy_paths({}),());self.assertEqual(policy_paths(self.policy),tuple(self.paths))
 def test_scoped_traversal_absolute_special_and_malformed_paths_rejected(self):
  for text in ['node_modules/@scope/.hidden','../node_modules/.x','/node_modules/.x','node_modules//.x','node_modules/./.x','node_modules/.bin','node_modules/.package-lock.json','node_modules/.x.','node_modules/.x:stream','node_modules/ordinary','node_modules/..','node_modules/\\.x']:
   p=copy.deepcopy(self.policy);p['finalAuxiliaryPreparation']['paths']=[text]
   with self.subTest(text=text),self.assertRaises(GoalContractError):policy_paths(p)
 def test_boolean_schema_and_casecollision_rejected(self):
  p=copy.deepcopy(self.policy);p['finalAuxiliaryPreparation']['schemaVersion']=True
  with self.assertRaises(GoalContractError):policy_paths(p)
  p=copy.deepcopy(self.policy);p['finalAuxiliaryPreparation']['paths']=['node_modules/.VITE-temp','node_modules/.vite-temp']
  with self.assertRaises(GoalContractError):policy_paths(p)
 def test_only_leaf_created_then_empty_checked(self):
  rows=validate_layout(self.project,self.paths,self.plan,create=True);self.assertFalse(rows[0]['existedBeforePreparation'])
  self.assertTrue(validate_layout(self.project,self.paths,self.plan)[0]['emptyObserved'])
 def test_missing_parent_and_unrecognized_owner_cannot_create_ancestors(self):
  with self.assertRaises(GoalContractError):validate_layout(self.project,['node_modules/other/node_modules/.x'],self.plan,create=True)
  self.assertFalse((self.project/'node_modules/other').exists())
 def test_real_nested_unscoped_slot_with_locked_owner(self):
  (self.project/'node_modules/vite/node_modules').mkdir(parents=True)
  validate_layout(self.project,['node_modules/vite/node_modules/.x'],self.plan,create=True)
  self.assertTrue((self.project/'node_modules/vite/node_modules/.x').is_dir())
 def test_nonempty_file_child_and_locked_slot_rejected(self):
  target=self.project/self.paths[0];target.mkdir()
  for mode in ['file','child']:
   item=target/mode
   item.write_bytes(b'x') if mode=='file' else item.mkdir()
   with self.assertRaisesRegex(GoalContractError,'NOT_EMPTY'):validate_layout(self.project,self.paths,self.plan,create=True)
   item.unlink() if mode=='file' else item.rmdir()
  plan={**self.plan,'instances':[{'path':self.paths[0]}]}
  with self.assertRaisesRegex(GoalContractError,'PACKAGE_SLOT'):validate_layout(self.project,self.paths,plan,create=True)
  target.rmdir();target.write_bytes(b'x')
  with self.assertRaisesRegex(GoalContractError,'LINK_OR_TYPE'):validate_layout(self.project,self.paths,self.plan,create=True)
 @unittest.skipUnless(os.name=='nt','actual Windows junction')
 def test_actual_junction_rejected_without_writing_target(self):
  outside=self.root/'outside';outside.mkdir();link=self.project/self.paths[0]
  command="New-Item -ItemType Junction -Path '"+str(link).replace("'","''")+"' -Target '"+str(outside).replace("'","''")+"' | Out-Null"
  result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',command],capture_output=True,timeout=10)
  self.assertEqual(result.returncode,0,result.stderr)
  try:
   with self.assertRaisesRegex(GoalContractError,'LINK_OR_TYPE'):validate_layout(self.project,self.paths,self.plan,create=True)
   self.assertEqual(list(outside.iterdir()),[])
  finally:os.rmdir(link)
 def test_missing_or_invalid_receipt_auxiliary_observations_rejected(self):
  with self.assertRaises(GoalContractError):observed_paths({})
  raw={'auxiliaryDirectoryObservations':[{'phase':'final','directories':[{'relativePath':self.paths[0],'classification':'UNSCOPED_NONPACKAGE_EMPTY_DIRECTORY','emptyObserved':False,'linkFreeObserved':True}]}]}
  with self.assertRaises(GoalContractError):observed_paths(raw)
 def test_unaccepted_receipt_stops_before_mkdir(self):
  tx=SimpleNamespace(registration=SimpleNamespace(policy=self.policy),context=SimpleNamespace(accepted_receipt={}))
  with patch('deploom_mcp.goal_frozen_candidate.frozen_profile_complete',return_value=False):
   with self.assertRaisesRegex(GoalContractError,'FRESH_RECEIPT'):prepare_observed_auxiliary(tx,self.project,self.plan)
  self.assertFalse((self.project/self.paths[0]).exists())
 def test_receipt_observation_cannot_authorize_an_unobserved_policy_path(self):
  reg=SimpleNamespace(policy=self.policy,goal={'policyDigest':canonical_digest(self.policy)})
  tx=SimpleNamespace(registration=reg,context=SimpleNamespace(accepted_receipt={'verificationObservation':{'coreWorkspaceObservation':{'auxiliaryDirectoryObservations':[]}}}),directory=self.root,remaining=lambda:30)
  with patch('deploom_mcp.goal_frozen_candidate.frozen_profile_complete',return_value=True):
   with self.assertRaisesRegex(GoalContractError,'NOT_OBSERVED'):prepare_observed_auxiliary(tx,self.project,self.plan)
  self.assertFalse((self.project/self.paths[0]).exists())
 def test_live_requires_bound_preparation_and_exact_observed_set(self):
  validate_layout(self.project,self.paths,self.plan,create=True)
  receipt={'receipt':'bound'};reg=SimpleNamespace(policy=self.policy)
  state={'mode':MODE,'paths':self.paths,'acceptedReceiptDigest':canonical_digest(receipt),'policyDigest':canonical_digest(self.policy),'obligationPlanDigest':self.plan['obligationPlanDigest']}
  tx=SimpleNamespace(registration=reg,context=SimpleNamespace(accepted_receipt=receipt),record={'dependencyStage':{'auxiliaryPreparation':state}},project=self.project)
  row={'relativePath':self.paths[0],'classification':'UNSCOPED_NONPACKAGE_EMPTY_DIRECTORY','emptyObserved':True,'linkFreeObserved':True}
  raw={'auxiliaryDirectoryObservations':[{'phase':'final','directories':[row]}]}
  validate_live_auxiliary(tx,self.plan,raw)
  state['acceptedReceiptDigest']='stale'
  with self.assertRaisesRegex(GoalContractError,'PREPARATION_UNPROVEN'):validate_live_auxiliary(tx,self.plan,raw)
  state['acceptedReceiptDigest']=canonical_digest(receipt);raw['auxiliaryDirectoryObservations'][0]['directories'].append({**row,'relativePath':'node_modules/.unexpected'})
  with self.assertRaisesRegex(GoalContractError,'UNEXPECTED_LIVE_SET'):validate_live_auxiliary(tx,self.plan,raw)
 def test_full_tree_guard_still_detects_extra_empty_dir_and_changed_file(self):
  from deploom_mcp.goal_windows_tree_swap import tree_digest
  validate_layout(self.project,self.paths,self.plan,create=True);root=self.project/'node_modules';(root/'payload').write_bytes(b'one')
  sealed=tree_digest(root,deadline=time.time()+20);(root/'.unexpected').mkdir()
  self.assertNotEqual(sealed,tree_digest(root,deadline=time.time()+20));(root/'.unexpected').rmdir()
  (root/'payload').write_bytes(b'two');self.assertNotEqual(sealed,tree_digest(root,deadline=time.time()+20))

if __name__=='__main__':unittest.main()
