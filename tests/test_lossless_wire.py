import json,unittest
from deploom_mcp.server import _result
class LosslessWireTests(unittest.TestCase):
 def test_unicode_blockers_unknowns_and_nested_evidence_are_identical_in_both_channels(self):
  payload={'ok':True,'status':{'authority':'PLANNING_ONLY','verificationStatus':'NOT_VERIFIED'},'decisionEvidence':[{'code':'TARGET_POLICY_BLOCKED','message':'Ошибка: ≥80% != 50%','evidenceRef':'#/targetAssessment','lifecycle':'ACTIVE_FINAL'}],'unknowns':['security unknown'],'nested':{'false':False,'null':None,'rows':[1,2]}}
  r=_result(payload);self.assertEqual(json.loads(r.content[0].text),payload);self.assertEqual(r.structured_content,payload);self.assertNotIn('\n',r.content[0].text);self.assertLess(len(r.content[0].text),len(json.dumps(payload,ensure_ascii=False,indent=2)))
 def test_errors_remain_errors_with_full_details(self):
  p={'ok':False,'error':{'code':'scope-mismatch','detail':{'expected':9,'actual':8}}};r=_result(p);self.assertTrue(r.is_error);self.assertEqual(json.loads(r.content[0].text),p);self.assertEqual(r.structured_content,p)
if __name__=='__main__':unittest.main()
