"""Actual Core/native child joins with deliberate negative receipt mutations.

These generated fixtures are NOT the real S2 project migration or TAP coverage.
"""
import copy,hashlib,json,os,shutil,time,unittest
from pathlib import Path
import test_goal_tap_nyc_core as fixtures
from source_snapshot import source_snapshot_fingerprint
from materialized_workspace_verifier import verify_current_workspace
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_tap_nyc_execution import (verify_with_tap_input,receipt_matches,physically_current,validate_profile)
from deploom_mcp.goal_tap_nyc_input import KIND
from project_execution_environment import project_environment_observation,project_environment_fingerprint,V2
from verification_process_supervisor import run_supervised

@unittest.skipUnless(os.name=='nt','Native held-input and output profiles require Windows handles')
class TapExecutionTests(unittest.TestCase):
 def setUp(self):
  self.real=fixtures.TapRealCoreTests();self.real.setUp();self.addCleanup(self.real.doCleanups);self.f=self.real.f
  self.policy={'projectExecutionEnvironment':V2,'privateVerificationRoute':'FROZEN_EXACT_SINGLE_MATERIALIZATION_V1','tapNycInput':self.f.policy}
  self.profile={'commands':['npm run ci'],'checkBindings':[{'kind':'command','commandIndex':1,'normative':{'allowedInputs':{'oracle':'SEALED_PROJECT_COMMAND','externalInputs':{'kind':KIND,'recipeHash':self.f.policy['recipeHash']}}}}]}
  self.calls=0
 def invoke(self,role='final',verifier=None):
  return verify_with_tap_input(verifier or self.native_double,self.policy,self.profile,role,self.f.project,
   commands=self.profile['commands'],cache_dir=self.real.cache,deadline=self.f.deadline,final_profile_digest=canonical_digest(self.profile),materialize=True,
   dependency_plan=self.real.plan,expected_source_key=source_snapshot_fingerprint(self.f.project))
 def native_double(self,project,**kw):
  self.calls+=1;return verify_current_workspace(project,**kw)
 def test_real_child_receipt_joins_and_slot_is_single_use(self):
  raw,receipt=self.invoke();self.assertTrue(receipt_matches(self.policy,self.profile,'final',self.f.project,raw,receipt));self.assertTrue(physically_current(self.policy,self.profile,self.f.project,raw,receipt,deadline=self.f.deadline))
  # Existing output is independently forbidden; clearing it cannot reset slot.
  (self.f.outputs('final')['report']/'lcov.info').unlink()
  (self.f.outputs('final')['temp']/'data').rmdir()
  with self.assertRaisesRegex(GoalContractError,'SLOT_ALREADY_CONSUMED'):self.invoke()
  self.assertEqual(self.calls,1)
 def test_bad_actual_cwd_env_command_or_closure_rejected(self):
  for field in ('cwd','environment','command','descendants'):
   # Distinct fixture for each consumed run, never reseal one slot.
   case=TapExecutionTests();case.setUp()
   try:
    def bad(project,**kw):
     raw=case.native_double(project,**kw)
     if field=='cwd':raw['ownerOperationObservations'][0]['projectExecutionEnvironment']['cwdDigest']='a'*64
     if field=='environment':raw['environmentDigest']='a'*32
     if field=='command':raw['commandObservations'][0]['commandDigest']='a'*64
     if field=='descendants':raw['ownerOperationObservations'][0]['descendantsRemaining']=1
     return raw
    with self.assertRaisesRegex(GoalContractError,'ACTUAL_EXECUTION_JOIN_MISMATCH'):case.invoke(verifier=bad)
    self.assertTrue(Path(case.f.policy['lanes']['final']['yaml']['path']+'.consumed.json').is_file())
   finally:case.doCleanups()
 def test_wrong_recipe_or_command_never_calls_core(self):
  self.profile['commands']=['npm test']
  with self.assertRaisesRegex(GoalContractError,'EXACT_CI_COMMAND_REQUIRED'):self.invoke()
  self.assertEqual(self.calls,0)
 def test_missing_or_cross_role_receipt_rejected(self):
  raw,receipt=self.invoke();self.assertFalse(receipt_matches(self.policy,self.profile,'private',self.f.project,raw,receipt));self.assertFalse(receipt_matches(self.policy,self.profile,'final',self.f.project,raw,None))
 def test_current_rechecks_marker_physical_identity(self):
  raw,receipt=self.invoke();p=Path(receipt['slot']['path']);rawbytes=p.read_bytes();q=p.with_suffix('.replacement');q.write_bytes(rawbytes);q.replace(p)
  self.assertFalse(physically_current(self.policy,self.profile,self.f.project,raw,receipt,deadline=self.f.deadline))
 def test_exception_consumes_slot_without_manufactured_receipt(self):
  def fail(*a,**kw):raise RuntimeError('CONFIRMED_OWNER_FAILURE')
  with self.assertRaisesRegex(RuntimeError,'CONFIRMED_OWNER_FAILURE'):self.invoke(verifier=fail)
  with self.assertRaisesRegex(GoalContractError,'SLOT_ALREADY_CONSUMED'):self.invoke()
  self.assertEqual(self.calls,0)
 def registration(self,policy=None,profile=None):
  from types import SimpleNamespace
  from deploom_mcp.goal_check_bindings import admission_scope_digest,initial_verification_context
  import test_goal_contract as contracts
  f=contracts.GoalContractTests();f.setUp();policy=policy or self.policy
  goal={**f.goal,'requiredChecks':['ci'],'policyDigest':canonical_digest(policy)}
  profile=copy.deepcopy(profile or self.profile);profile.update(schemaVersion=2,requiredChecks=['ci'],projectChecks='strict',verificationPurpose='intermediate-candidate')
  row=profile['checkBindings'][0];row.update(checkId='ci',commandSha256=hashlib.sha256(profile['commands'][0].encode()).hexdigest())
  row['normative'].update(schemaVersion=1,purpose={'goalRequirementId':'ci','subjectPackages':['express']},expectedOutcome={'kind':'PASS','exitCode':0})
  row['normative']['allowedInputs'].update(source='SEALED_PREPARED_SOURCE',dependencies='APPROVED_PHYSICAL_TREE')
  profile['admission']={'schemaVersion':1,'scope':'ISOLATED_NPM_V2_CANDIDATE','allowedChangeScopeDigest':admission_scope_digest(goal),'optionalAbsenceRules':[]}
  identity={**f.identity,'policyDigest':goal['policyDigest']};goal['verificationContext']=initial_verification_context(profile,identity)
  return SimpleNamespace(project_path=str(self.f.project),policy=policy,profile=profile,goal=goal,binding={'identity':identity},candidate_files={'manifest':'candidate.json'},delta_authorization={'origin':'PREAPPROVED_HOST_POLICY'})
 def test_declared_external_input_requires_matching_host_policy(self):
  from deploom_mcp.goal_execution_registry import require_registered_execution_class
  policy={k:v for k,v in self.policy.items() if k!='tapNycInput'}
  with self.assertRaisesRegex(GoalContractError,'TAP_NYC_HOST_POLICY_REQUIRED'):require_registered_execution_class(self.registration(policy=policy))
 def test_owned_input_slots_cannot_escape_registered_host_state(self):
  from unittest.mock import patch
  from deploom_mcp.goal_execution_registry import require_registered_execution_class
  foreign=self.f.root/'different-owner';foreign.mkdir()
  with patch.dict(os.environ,{'DEPLOOM_MCP_STATE_DIR':str(foreign)}):
   with self.assertRaisesRegex(GoalContractError,'INVALID_GOAL_ARTIFACT_PATH'):require_registered_execution_class(self.registration())
 def test_typed_declaration_cannot_extend_other_commands(self):
  from deploom_mcp.goal_check_bindings import validate_check_bindings
  registration=self.registration();validate_check_bindings(registration.profile,['ci'])
  registration.profile['commands']=['npm test'];registration.profile['checkBindings'][0]['commandSha256']=hashlib.sha256(b'npm test').hexdigest()
  with self.assertRaisesRegex(GoalContractError,'INPUT_SCOPE_UNSUPPORTED'):validate_check_bindings(registration.profile,['ci'])
 def test_validated_host_policy_enables_only_typed_execution_class(self):
  from deploom_mcp.goal_execution_registry import require_registered_execution_class
  registration=self.registration()
  self.assertEqual(require_registered_execution_class(registration),'PREAPPROVED_HOST_POLICY')
  self.assertFalse(hasattr(registration,'passed'))
 def test_checkpoint_identity_binds_external_output_receipt(self):
  from deploom_mcp.goal_final_checkpoint import tap_checkpoint_identity
  raw,r=self.invoke();a=tap_checkpoint_identity({'tapInputObservation':r});changed=copy.deepcopy(r)
  changed['inputObservation']['outputReceipt']['lcov']['sha256']='f'*64
  self.assertNotEqual(a,tap_checkpoint_identity({'tapInputObservation':changed}));self.assertEqual(tap_checkpoint_identity({}),{})
 def test_host_profile_and_policy_recipe_must_agree(self):
  p=copy.deepcopy(self.profile);p['checkBindings'][0]['normative']['allowedInputs']['externalInputs']['recipeHash']='f'*64
  with self.assertRaisesRegex(GoalContractError,'NORMATIVE_INPUT_MISMATCH'):validate_profile(self.policy,p)
 def test_ordinary_policy_does_not_add_environment_or_receipt(self):
  seen={}
  def owner(project,**kw):seen.update(kw);return {'raw':'untouched'}
  result=verify_with_tap_input(owner,{}, {},'final',self.f.project,materialize=False)
  self.assertEqual(result,({'raw':'untouched'},None));self.assertNotIn('environment',seen);self.assertNotIn('environment_contract',seen)

if __name__=='__main__':unittest.main()
