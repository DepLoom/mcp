import unittest,copy
from deploom_mcp.goal_check_bindings import validate_normative_admission
from deploom_mcp.goal_contract import GoalContractError
class PlatformProfileShapeTests(unittest.TestCase):
 def setUp(self):
  self.profile={'admission':{'schemaVersion':1,'scope':'ISOLATED_NPM_V3_CANDIDATE','allowedChangeScopeDigest':'a'*64,'optionalAbsenceRules':{'schemaVersion':1,'kind':'NPM_V3_SRI_PLATFORM_ABSENCE_V1','host':{'platform':'win32','arch':'x64','nodeSha256':'b'*64},'artifacts':[{'instance':'node_modules/foreign','archivePath':'C:/owned/foreign.tgz'}]}},'checkBindings':[]}
 def test_only_explicit_v3_typed_host_rules_are_admitted(self):
  self.assertIsNotNone(validate_normative_admission(self.profile,[]))
  for bad in [{},[{'incompatible':True}],True]:
   p=copy.deepcopy(self.profile);p['admission']['optionalAbsenceRules']=bad
   with self.subTest(bad=bad),self.assertRaises(GoalContractError):validate_normative_admission(p,[])
 def test_v2_rules_and_unknown_host_do_not_become_supported(self):
  p=copy.deepcopy(self.profile);p['admission']['scope']='ISOLATED_NPM_V2_CANDIDATE'
  with self.assertRaises(GoalContractError):validate_normative_admission(p,[])
  p=copy.deepcopy(self.profile);p['admission']['optionalAbsenceRules']['host']['platform']='linux'
  with self.assertRaises(GoalContractError):validate_normative_admission(p,[])
 def test_empty_defaults_remain_available_but_not_omission_proof(self):
  for scope in ['ISOLATED_NPM_V2_CANDIDATE','ISOLATED_NPM_V3_CANDIDATE']:
   p=copy.deepcopy(self.profile);p['admission'].update(scope=scope,optionalAbsenceRules=[]);self.assertEqual(validate_normative_admission(p,[])['optionalAbsenceRules'],[])
 def test_explicit_v2_kind_must_match_profile(self):
  p=copy.deepcopy(self.profile);p['admission']['scope']='ISOLATED_NPM_V2_CANDIDATE';p['admission']['optionalAbsenceRules']['kind']='NPM_V2_SRI_PLATFORM_ABSENCE_V1';self.assertIsNotNone(validate_normative_admission(p,[]))
  p['admission']['scope']='ISOLATED_NPM_V3_CANDIDATE'
  with self.assertRaises(GoalContractError):validate_normative_admission(p,[])
 def test_untyped_bundle_rules_never_admit(self):
  for bad in ({},[],True,{'passed':True}):
   p=copy.deepcopy(self.profile);p['admission'].update(scope='ISOLATED_NPM_V2_CANDIDATE',optionalAbsenceRules=[],bundledOwnerRules=bad)
   with self.subTest(bad=bad),self.assertRaises(GoalContractError):validate_normative_admission(p,[])
if __name__=='__main__':unittest.main()
