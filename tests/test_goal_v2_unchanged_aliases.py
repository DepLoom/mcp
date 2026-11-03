import copy,unittest
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_lock_scope import _scope_packages,_legacy

def fixture():return {'lockfileVersion':2,'packages':{'':{'dependencies':{'owner':'1.0.0'}},'node_modules/owner':{'version':'1.0.0','dependencies':{'alias':'npm:real@^1'}},'node_modules/alias':{'name':'real','version':'1.2.3','integrity':'held'}},'dependencies':{'owner':{'version':'1.0.0','requires':{'alias':'npm:real@^1'}},'alias':{'version':'npm:real@1.2.3','integrity':'held'}}}
class V2AliasScopeTests(unittest.TestCase):
 def test_unchanged_alias_matches_views_without_mutating_raw_version(self):
  old=fixture();new=copy.deepcopy(old);_scope_packages(old,new)
  self.assertEqual(_legacy(old)['node_modules/alias']['version'],'npm:real@1.2.3');self.assertEqual(old,new)
 def test_missing_or_inconsistent_legacy_view_rejects_even_when_unchanged(self):
  missing=fixture();del missing['dependencies']
  wrong=fixture();wrong['dependencies']['alias']['version']='npm:real@9.9.9'
  for lock in [missing,wrong]:
   with self.subTest(lock=lock),self.assertRaisesRegex(GoalContractError,'ALIAS_V2_VIEW'):_scope_packages(lock,copy.deepcopy(lock))
 def test_record_or_incoming_change_not_accepted(self):
  old=fixture();new=copy.deepcopy(old);new['packages']['node_modules/alias']['integrity']='changed'
  with self.assertRaisesRegex(GoalContractError,'ALIAS_DELTA'):_scope_packages(old,new)
  new=copy.deepcopy(old);new['packages']['node_modules/owner']['dependencies']['alias']='npm:real@^1.2'
  with self.assertRaisesRegex(GoalContractError,'ALIAS_DELTA'):_scope_packages(old,new)
 def test_new_alias_or_identity_mismatch_rejected(self):
  old=fixture();new=copy.deepcopy(old);new['packages']['node_modules/second']={'name':'real','version':'1.2.3'}
  with self.assertRaises(GoalContractError):_scope_packages(old,new)
  new=copy.deepcopy(old);new['packages']['node_modules/alias']['name']='wrong'
  with self.assertRaises(GoalContractError):_scope_packages(old,new)
 def test_raw_legacy_flags_and_scoped_alias_projection(self):
  old=fixture();new=copy.deepcopy(old);new['dependencies']['alias']['peer']=True
  with self.assertRaisesRegex(GoalContractError,'ALIAS_DELTA'):_scope_packages(old,new)
  scoped=fixture();scoped['packages']['node_modules/alias']['name']='@scope/real';scoped['dependencies']['alias']['version']='npm:@scope/real@1.2.3';scoped['packages']['node_modules/owner']['dependencies']['alias']='npm:@scope/real@^1';scoped['dependencies']['owner']['requires']['alias']='npm:@scope/real@^1'
  _scope_packages(scoped,copy.deepcopy(scoped));self.assertEqual(_legacy(scoped)['node_modules/alias']['version'],'npm:@scope/real@1.2.3')
 def test_legacy_alias_canonical_path_and_exact_pin_required(self):
  for value in ['npm:../real@1.2.3','npm:real@^1','npm:real@npm:wrong@1.2.3']:
   lock=fixture();lock['dependencies']['alias']['version']=value
   with self.subTest(value=value),self.assertRaises(GoalContractError):_legacy(lock)
if __name__=='__main__':unittest.main()
