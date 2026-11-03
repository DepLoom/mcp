"""Trusted host copy permission remains declared scope and fails closed."""
import copy,hashlib,unittest
import test_goal_artifact_preservation as preservation_fixture
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_lock_scope import validate_npm_lock_scope
from deploom_mcp.goal_check_bindings import build_inline_node_command,admission_scope_digest
from deploom_mcp.goal_execution_registry import FROZEN_ROUTE

class ArtifactHostScopeTests(unittest.TestCase):
 def setUp(self):
  self.f=preservation_fixture.ArtifactPreservationTests();self.f.setUp();self.f.goal["requiredChecks"]=["runtime"]
  body=b"throw Error('synthetic boundary oracle; not executed')";sha=hashlib.sha256(body).hexdigest();cmd=build_inline_node_command(body)
  self.profile={'schemaVersion':2,'commands':[cmd],'requiredChecks':self.f.goal['requiredChecks'][:],'projectChecks':'strict','verificationPurpose':'intermediate-candidate','checkBindings':[],'admission':{'schemaVersion':1,'scope':'ISOLATED_NPM_V2_CANDIDATE','allowedChangeScopeDigest':'a'*64,'optionalAbsenceRules':[]}}
  for i,name in enumerate(self.f.goal['requiredChecks'],1):
   self.profile['checkBindings'].append({'checkId':name,'kind':'command','commandIndex':i,'commandSha256':hashlib.sha256(cmd.encode()).hexdigest(),'oracle':{'hostPath':f'oracles/{name}.cjs','sha256':sha,'mode':'INLINE_NODE_V1','bodyBytes':len(body)},'normative':{'schemaVersion':1,'purpose':{'goalRequirementId':name,'subjectPackages':['consumer','leaf']},'expectedOutcome':{'kind':'PASS','exitCode':0},'allowedInputs':{'source':'SEALED_PREPARED_SOURCE','dependencies':'APPROVED_PHYSICAL_TREE','oracle':'PINNED_INLINE_BODY','externalInputs':'NONE_DECLARED_BY_HOST'}}})
  c=self.f.contract();permission={k:c[k] for k in ('schemaVersion','mode','beforeLockDigest','afterLockDigest','preservations')};permission.update(impactCheckId='runtime',impactOracleSha256=sha)
  self.policy={'hardPolicyRequired':False,'transitiveChangePolicy':'EXACT_HOST_ENUMERATED','privateVerificationRoute':FROZEN_ROUTE,'heldArtifactPreservation':permission};self.rebind()
 def rebind(self):
  self.f.goal['policyDigest']=canonical_digest(self.policy);self.profile['admission']['allowedChangeScopeDigest']=admission_scope_digest(self.f.goal)
 def check(self):return validate_npm_lock_scope(self.f.old,self.f.new,self.f.goal,host_policy=self.policy,check_profile=self.profile)
 def reject(self):
  with self.assertRaises(GoalContractError):self.check()
 def test_exact_copy_is_declared_only(self):
  r=self.check();self.assertEqual(r['status'],'DECLARED_LOCK_CLOSURE_ONLY');self.assertFalse(r['necessityVerified']);self.assertEqual(r['compatibility'],'NOT_VERIFIED');p=r['declaredArtifactPreservation'];self.assertEqual(p['executionAuthority'],'NONE');self.assertFalse(p['fullAcceptance']);self.assertEqual(p['comparedHeldEdges'],1)
 def test_default_still_rejects(self):
  with self.assertRaisesRegex(GoalContractError,'TRANSITIVE_SCOPE_VIOLATION'):validate_npm_lock_scope(self.f.old,self.f.new,self.f.goal)
 def test_unbound_policy_rejects(self):self.policy['extra']='forged';self.reject()
 def test_only_frozen_owner(self):self.policy.pop('privateVerificationRoute');self.rebind();self.reject()
 def test_only_exact_host_scope(self):self.policy['transitiveChangePolicy']='ROOT_TARGETS_ONLY';self.rebind();self.reject()
 def test_missing_admission(self):self.profile.pop('admission');self.reject()
 def test_wrong_lock_profile(self):self.profile['admission']['scope']='ISOLATED_NPM_V3_CANDIDATE';self.reject()
 def test_wrong_admission_goal_scope(self):self.profile['admission']['allowedChangeScopeDigest']='b'*64;self.reject()
 def test_unknown_permission_field(self):self.policy['heldArtifactPreservation']['passed']=True;self.rebind();self.reject()
 def test_stale_lock_permission(self):self.policy['heldArtifactPreservation']['afterLockDigest']='b'*64;self.rebind();self.reject()
 def test_impact_digest_mismatch(self):self.policy['heldArtifactPreservation']['impactOracleSha256']='b'*64;self.rebind();self.reject()
 def test_impact_missing(self):self.policy['heldArtifactPreservation']['impactCheckId']='absent';self.rebind();self.reject()
 def test_affected_owner_subject_required(self):self.profile['checkBindings'][0]['normative']['purpose']['subjectPackages']=['leaf'];self.reject()
 def test_protected_package_never_relaxed(self):self.f.goal['immutablePackages'].append('leaf');self.reject()
 def test_copy_record_drift_even_rebound(self):
  self.f.new['packages'][self.f.dst]['engines']={'node':'>=99'};self.policy['heldArtifactPreservation']['afterLockDigest']=canonical_digest(self.f.new);self.rebind();self.reject()
 def test_held_context_drift_even_rebound(self):
  self.f.new['packages']['node_modules/consumer']['dependencies']['leaf']='*';self.f.sync_views();self.policy['heldArtifactPreservation']['afterLockDigest']=canonical_digest(self.f.new);self.rebind();self.reject()
 def test_unenumerated_delta_even_rebound(self):
  self.f.new['packages']['node_modules/extra']=copy.deepcopy(self.f.oldleaf);self.f.sync_views();self.policy['heldArtifactPreservation']['afterLockDigest']=canonical_digest(self.f.new);self.rebind();self.reject()
 def test_no_combined_shared_transition(self):self.policy['sharedInstanceTransitions']={};self.rebind();self.reject()

if __name__=='__main__':unittest.main()
