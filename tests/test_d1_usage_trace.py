import sys,json,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from d1_usage_trace import UsageTrace
class UsageTraceTests(unittest.TestCase):
 def test_retains_counts_chronology_without_commands_or_outputs(self):
  t=UsageTrace();t.ingest({'method':'item/completed','params':{'item':{'type':'commandExecution','id':'1','command':'npm ci SECRET_TOKEN','aggregatedOutput':'SECRET_OUTPUT','exitCode':0,'durationMs':7}}})
  t.ingest({'method':'thread/tokenUsage/updated'}, {'input_tokens':100,'cached_input_tokens':40,'output_tokens':10,'reasoning_output_tokens':2})
  t.ingest({'method':'item/completed','params':{'item':{'type':'mcpToolCall','id':'2','tool':'get_upgrade_plan','result':{'content':[{'type':'text','text':'secret-body'}],'structuredContent':{'secret':'private-body'}}}}})
  result=t.result();wire=json.dumps(result)
  for secret in ('SECRET_TOKEN','SECRET_OUTPUT','secret-body','private-body'):self.assertNotIn(secret,wire)
  self.assertEqual(result['records'][0]['phaseHints'],['install']);self.assertEqual(result['records'][1]['total']['input_tokens'],100);self.assertEqual(result['records'][2]['textUtf8Bytes'],11)
 def test_size_only_accepts_snake_case_structured_result(self):
  t=UsageTrace();t.ingest({'method':'item/completed','params':{'item':{'type':'mcpToolCall','result':{'structured_content':{'ok':True}}}}})
  self.assertEqual(t.result()['records'][0]['structuredJsonUtf8Bytes'],11)
if __name__=='__main__':unittest.main()
