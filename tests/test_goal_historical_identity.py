import copy,unittest
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_lock_scope import _packages,_legacy,_scope_packages,_instance_identity

class HistoricalScopeTests(unittest.TestCase):
 def old(self):return {'lockfileVersion':2,'packages':{'':{},'node_modules/JSONStream':{'version':'1.3.5','integrity':'held'}},'dependencies':{'JSONStream':{'version':'1.3.5','integrity':'held'}}}
 def test_existing_historical_name_keeps_exact_case_in_both_views(self):
  old=self.old();self.assertIn('node_modules/JSONStream',_packages(old));self.assertIn('node_modules/JSONStream',_legacy(old));self.assertEqual(_scope_packages(old,copy.deepcopy(old)),(old['packages'],old['packages']))
 def test_new_or_changed_historical_record_rejected(self):
  old=self.old()
  for field,value in [('version','1.3.6'),('integrity','changed')]:
   new=copy.deepcopy(old);new['packages']['node_modules/JSONStream'][field]=value
   with self.subTest(field=field),self.assertRaisesRegex(GoalContractError,'HISTORICAL_INSTANCE_DELTA'):_scope_packages(old,new)
  new=copy.deepcopy(old);new['packages']['node_modules/NewHistorical']={'version':'1.0.0'}
  with self.assertRaisesRegex(GoalContractError,'HISTORICAL_INSTANCE_DELTA'):_scope_packages(old,new)
 def test_both_graph_views_reject_casefold_collisions(self):
  old=self.old();old['packages']['node_modules/jsonstream']={'version':'1.3.5'};old['dependencies']['jsonstream']={'version':'1.3.5'}
  for fn in (_packages,_legacy):
   with self.subTest(fn=fn.__name__),self.assertRaises(GoalContractError):fn(old)
 def test_unsafe_paths_and_case_only_alias_not_accepted(self):
  for path in ['node_modules/../JSONStream','node_modules/JSONStream\x00','node_modules/JSONStream:evil','node_modules/JSONStream/other']:
   with self.subTest(path=path),self.assertRaises(GoalContractError):_instance_identity(path,{'version':'1.3.5'})
  with self.assertRaises(GoalContractError):_instance_identity('node_modules/JSONStream',{'name':'jsonstream','version':'1.3.5'})
if __name__=='__main__':unittest.main()
