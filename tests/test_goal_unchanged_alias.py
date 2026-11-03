import unittest,copy
from deploom_mcp.goal_lock_scope import _scope_packages,_packages,_instance_identity
from deploom_mcp.goal_contract import GoalContractError
class UnchangedAliasTests(unittest.TestCase):
 def setUp(self):
  self.lock={'lockfileVersion':3,'packages':{'':{'dependencies':{'holder':'1.0.0'}},'node_modules/holder':{'version':'1.0.0','dependencies':{'canonical-cjs':'npm:canonical@^1.0.0'}},'node_modules/canonical-cjs':{'name':'canonical','version':'1.2.0','integrity':'unchanged'}}}
 def test_unchanged_explicit_alias_admitted_but_default_stays_strict(self):
  self.assertEqual(_scope_packages(self.lock,copy.deepcopy(self.lock)),(self.lock['packages'],self.lock['packages']))
  with self.assertRaises(GoalContractError):_packages(self.lock)
  with self.assertRaises(GoalContractError):_instance_identity('node_modules/canonical-cjs',self.lock['packages']['node_modules/canonical-cjs'])
 def test_changed_record_and_new_alias_rejected(self):
  for key,value in [('version','1.3.0'),('integrity','foreign')]:
   new=copy.deepcopy(self.lock);new['packages']['node_modules/canonical-cjs'][key]=value
   with self.subTest(key=key),self.assertRaises(GoalContractError):_scope_packages(self.lock,new)
  with self.assertRaises(GoalContractError):_scope_packages({'lockfileVersion':3,'packages':{'':{}}},self.lock)
 def test_complete_incoming_set_not_single_record(self):
  new=copy.deepcopy(self.lock);new['packages']['node_modules/new-parent']={'version':'1.0.0','dependencies':{'canonical-cjs':'npm:canonical@^1'}}
  with self.assertRaisesRegex(GoalContractError,'ALIAS_DELTA'):_scope_packages(self.lock,new)
 def test_ordinary_bad_range_wrong_canonical_and_orphan_reject(self):
  for spec in ['^1','npm:canonical@^2','npm:other@^1','npm:canonical@']:
   new=copy.deepcopy(self.lock);new['packages']['node_modules/holder']['dependencies']['canonical-cjs']=spec
   with self.subTest(spec=spec),self.assertRaises(GoalContractError):_scope_packages(new,new)
  new=copy.deepcopy(self.lock);new['packages']['node_modules/holder']['dependencies']={}
  with self.assertRaisesRegex(GoalContractError,'ORPHAN'):_scope_packages(new,new)
 def test_v2_not_extended_and_malformed_name_controlled(self):
  new=copy.deepcopy(self.lock);new['lockfileVersion']=2
  with self.assertRaises(GoalContractError):_scope_packages(new,new)
  for name in [None, 123, '../escape', 'outer/node_modules/inner']:
   new=copy.deepcopy(self.lock);new['packages']['node_modules/canonical-cjs']['name']=name
   with self.subTest(name=name),self.assertRaises(GoalContractError):_scope_packages(new,new)
 def test_peer_uses_parent_not_own_nested(self):
  new=copy.deepcopy(self.lock);new['packages']['node_modules/holder']['peerDependencies']={'canonical-cjs':'^1'}
  with self.assertRaisesRegex(GoalContractError,'ALIAS_EDGE'):_scope_packages(new,new)
if __name__=='__main__':unittest.main()
