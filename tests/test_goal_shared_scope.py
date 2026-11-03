"""Synthetic exact-shared permission boundaries; never runtime proof."""
import copy,hashlib,sys,unittest
from deploom_mcp.distribution import resolve_core_dir
sys.path.insert(0,str(resolve_core_dir()))
from npm_shared_transition import shared_transition_facts,digest
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_lock_scope import validate_npm_lock_scope
from deploom_mcp.goal_check_bindings import build_inline_node_command

class SharedScopeTests(unittest.TestCase):
 def setUp(self):
  self.old={'lockfileVersion':3,'name':'fixture','version':'1.0.0','packages':{'':{'name':'fixture','version':'1.0.0','dependencies':{'express':'^4.18.2','other':'1.0.0'}},'node_modules/express':{'version':'4.18.2','resolved':'https://example.invalid/express-old.tgz','integrity':'old-target'},'node_modules/other':{'version':'1.0.0','dependencies':{'loader':'1.0.0'}},'node_modules/loader':{'version':'1.0.0','dependencies':{'yaml':'^2.3.4'}},'node_modules/yaml':{'version':'2.4.1','resolved':'https://example.invalid/yaml-old.tgz','integrity':'old-yaml'}}}
  self.new=copy.deepcopy(self.old);self.new['packages']['']['dependencies']['express']='4.18.3';self.new['packages']['node_modules/express'].update(version='4.18.3',resolved='https://example.invalid/express-new.tgz',integrity='new-target',peerDependencies={'yaml':'^2.4.2'},peerDependenciesMeta={'yaml':{'optional':True}});self.new['packages']['node_modules/yaml'].update(version='2.9.1',resolved='https://example.invalid/yaml-new.tgz',integrity='new-yaml')
  self.goal={'schemaVersion':1,'goalKind':'functional-maintenance','healthPolicy':'REPORT_ONLY','goalId':'shared-fixture','exactTargets':{'express':'4.18.3'},'mutablePackages':['express'],'immutablePackages':[],'excludedPackages':[],'requiredChecks':['yaml-impact'],'acceptPartial':False,'allowSourceRepair':False,'policyDigest':'a'*64}
  body=b"throw Error('synthetic test oracle must never execute')";sha=hashlib.sha256(body).hexdigest();command=build_inline_node_command(body)
  self.profile={'schemaVersion':2,'commands':[command],'requiredChecks':['yaml-impact'],'projectChecks':'strict','verificationPurpose':'intermediate-candidate','checkBindings':[{'checkId':'yaml-impact','kind':'command','commandIndex':1,'commandSha256':hashlib.sha256(command.encode()).hexdigest(),'oracle':{'hostPath':'oracles/impact.cjs','sha256':sha,'mode':'INLINE_NODE_V1','bodyBytes':len(body)},'normative':{'schemaVersion':1,'purpose':{'goalRequirementId':'yaml-impact','subjectPackages':['yaml','loader']},'expectedOutcome':{'kind':'PASS','exitCode':0},'allowedInputs':{'source':'SEALED_PREPARED_SOURCE','dependencies':'APPROVED_PHYSICAL_TREE','oracle':'PINNED_INLINE_BODY','externalInputs':'NONE_DECLARED_BY_HOST'}}}],'admission':{'schemaVersion':1,'scope':'ISOLATED_NPM_V3_CANDIDATE','allowedChangeScopeDigest':'a'*64,'optionalAbsenceRules':[]}}
  row=shared_transition_facts(self.old,self.new,'node_modules/yaml',self.goal['exactTargets'],{'other'},set());row.update(impactCheckId='yaml-impact',impactOracleSha256=sha)
  self.policy={'hardPolicyRequired':False,'transitiveChangePolicy':'EXACT_HOST_ENUMERATED','sharedInstanceTransitions':{'schemaVersion':1,'beforeLockDigest':digest(self.old),'afterLockDigest':digest(self.new),'transitions':[row]}};self.rebind()
 def rebind(self):self.goal['policyDigest']=canonical_digest(self.policy)
 def check(self):return validate_npm_lock_scope(self.old,self.new,self.goal,host_policy=self.policy,check_profile=self.profile)
 def rejects(self):
  with self.assertRaises(GoalContractError):self.check()
 def test_default_remains_strict(self):
  with self.assertRaises(GoalContractError) as caught:validate_npm_lock_scope(self.old,self.new,self.goal)
  self.assertEqual(caught.exception.code,'TRANSITIVE_SCOPE_VIOLATION')
 def test_exact_host_permission_only_declared_scope_not_runtime_proof(self):
  r=self.check();self.assertEqual(r['authorizedSharedInstances'],['node_modules/yaml']);self.assertEqual(r['sharedRewritePolicy'],'EXACT_HOST_ENUMERATED');self.assertFalse(r['necessityVerified']);self.assertEqual(r['compatibility'],'NOT_VERIFIED')
 def test_stale_graph_or_entry_rejects(self):
  for field in ('beforeLockDigest','afterLockDigest'):
   with self.subTest(field=field):
    saved=copy.deepcopy(self.policy);self.policy['sharedInstanceTransitions'][field]='b'*64;self.rebind();self.rejects();self.policy=saved;self.rebind()
  self.policy['sharedInstanceTransitions']['transitions'][0]['beforeIncoming']=[];self.rebind();self.rejects()
 def test_protected_shared_never_overridden(self):
  for field in ('immutablePackages','excludedPackages'):
   with self.subTest(field=field):self.goal[field]=['yaml'];self.rejects();self.goal[field]=[]
 def test_missing_or_replaced_impact_check_rejected(self):
  self.profile['checkBindings'][0]['oracle']['sha256']='f'*64;self.rejects()
 def test_missing_affected_owner_subject_rejected(self):
  self.profile['checkBindings'][0]['normative']['purpose']['subjectPackages']=['yaml'];self.rejects()
 def test_agent_policy_with_unbound_digest_rejected(self):
  self.policy['extra']='unapproved';self.rejects()
 def test_scope_mode_cannot_be_broadened(self):
  self.policy['transitiveChangePolicy']='ROOT_TARGETS_ONLY';self.rebind();self.rejects()
 def test_held_root_rewrite_is_not_shared_permission(self):
  self.new['packages']['node_modules/other']['version']='2.0.0';self.rejects()
 def test_changed_held_consumer_rejected_even_when_graph_rebound(self):
  self.new['packages']['node_modules/loader']['dependencies']['yaml']='^2.0.0';self.policy['sharedInstanceTransitions']['afterLockDigest']=digest(self.new);self.rebind();self.rejects()
 def test_unrelated_shared_addition_not_allowed(self):
  self.new['packages']['node_modules/unrelated']={'version':'1.0.0'};self.rejects()
 def test_redundant_permission_duplicate_rejects(self):
  self.policy['sharedInstanceTransitions']['transitions']*=2;self.rebind();self.rejects()

if __name__=='__main__':unittest.main()
