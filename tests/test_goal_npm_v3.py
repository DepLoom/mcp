"""Version3 graph contracts retain scope; no fabricated legacy compatibility tree."""
import copy,unittest
import test_goal_lock_scope as fixtures
from deploom_mcp.goal_contract import npm_lock_format,validate_root_lock_versions,GoalContractError
from deploom_mcp.goal_lock_scope import validate_npm_lock_scope
from deploom_mcp.goal_final_authorization import final_workspace_profile

class NpmV3ContractTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.LockInstanceScopeTests();fixture.setUp();self.goal=fixture.goal;self.old=fixture.old;self.new=fixture.new
        for lock in [self.old,self.new]:lock['lockfileVersion']=3;lock.pop('dependencies')
    def test_packages_only_v3_preserves_exact_scope_and_planning_only_status(self):
        result=validate_npm_lock_scope(self.old,self.new,self.goal)
        self.assertEqual(result['status'],'DECLARED_LOCK_CLOSURE_ONLY');self.assertFalse(result['necessityVerified'])
        self.assertTrue(all(r['view']=='packages' for r in result['changedInstances']))
    def test_immutable_nested_identity_and_integrity_changes_are_rejected(self):
        path='node_modules/express/node_modules/body-parser'
        for key,value in [('version','99.0.0'),('integrity','foreign')]:
            changed=copy.deepcopy(self.new);changed['packages'][path][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(GoalContractError,'IMMUTABLE_OR_EXCLUDED'):validate_npm_lock_scope(self.old,changed,self.goal)
    def test_shared_non_target_cannot_be_replaced_or_retargeted(self):
        changed=copy.deepcopy(self.new);changed['packages']['node_modules/body-parser']['integrity']='foreign'
        with self.assertRaises(GoalContractError):validate_npm_lock_scope(self.old,changed,self.goal)
    def test_v3_legacy_view_is_not_synthesized_or_accepted(self):
        for value in [{},{'express':{'version':'4.18.2'}}]:
            changed=copy.deepcopy(self.new);changed['dependencies']=value
            with self.assertRaisesRegex(GoalContractError,'LEGACY_VIEW'):validate_npm_lock_scope(self.old,changed,self.goal)
    def test_mixed_versions_one_boolean_and_float_are_rejected(self):
        for version in [1,2,True,3.0,'3']:
            changed=copy.deepcopy(self.new);changed['lockfileVersion']=version
            with self.subTest(version=version),self.assertRaisesRegex(GoalContractError,'LOCK_FORMAT'):validate_root_lock_versions(self.old,changed,self.goal)
    def test_final_scope_tracks_the_explicit_admission_format(self):
        profile={'admission':{'scope':'ISOLATED_NPM_V3_CANDIDATE'},'checkBindings':[{'normative':{'allowedInputs':{'source':'SEALED_PREPARED_SOURCE'}}}]}
        result=final_workspace_profile(profile);self.assertEqual(result['admission']['scope'],'REGISTERED_CURRENT_NPM_V3_WORKSPACE')
        self.assertEqual(profile['admission']['scope'],'ISOLATED_NPM_V3_CANDIDATE')
    def test_linked_and_noncanonical_instances_remain_unsupported(self):
        for field,value in [('link',True),('name','different')]:
            changed=copy.deepcopy(self.new);changed['packages']['node_modules/express'][field]=value
            with self.assertRaises(GoalContractError):validate_npm_lock_scope(self.old,changed,self.goal)

if __name__=='__main__':unittest.main()
