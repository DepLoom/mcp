import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from d1_observation import Observation,decode_result
class ObservationTests(unittest.TestCase):
    def test_plan_before_edits_and_final_manifest_are_separate_observations(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'package.json';p.write_text(json.dumps({'dependencies':{'react':'18.3.1'}}))
            o=Observation(root)
            response={'ok':True,'dependencies':[{'package':'react','target':'19.3.1','status':'PROPOSED'}]}
            item={'type':'mcpToolCall','tool':'get_upgrade_plan','server':'deploom','status':'completed','arguments':{'project_path':str(root),'registry':'https://private.invalid/secret','mutable_packages':['react']},'result':{'content':[{'type':'text','text':json.dumps(response)}]}}
            o.ingest({'method':'item/completed','params':{'item':item}})
            p.write_text(json.dumps({'dependencies':{'react':'19.3.1'}}))
            o.ingest({'method':'item/completed','params':{'item':{'type':'commandExecution'}}})
            r=o.result();self.assertTrue(r['mcpCalls'][0]['beforeObservedEdit'])
            self.assertEqual(r['firstObservedEditSequence'],2)
            self.assertEqual(r['finalVersions']['react'],'19.3.1')
            self.assertNotIn('private.invalid',json.dumps(r))
    def test_common_launcher_authorizes_only_the_planning_tool(self):
        import tomllib
        from d1_pilot import codex_command,MODEL,EFFORT
        argv=codex_command('codex',Path('.'),MODEL,EFFORT,Path('python.exe'),Path('state'))
        config=next(v for v in argv if v.startswith('mcp_servers.deploom='))
        parsed=tomllib.loads(config)['mcp_servers']['deploom']
        self.assertEqual(set(parsed['tools']),{'get_upgrade_plan'})
        self.assertEqual(parsed['tools']['get_upgrade_plan']['approval_mode'],'approve')
        self.assertNotIn('default_tools_approval_mode',parsed)
        bare=codex_command('codex',Path('.'),MODEL,EFFORT,None,None)
        self.assertFalse(any(v.startswith('mcp_servers.') for v in bare))

    def test_error_is_never_made_into_a_plan(self):
        self.assertEqual(decode_result({'content':[{'type':'text','text':'not JSON'}]}),{})
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'package.json').write_text('{}');o=Observation(root)
            o.ingest({'method':'item/completed','params':{'item':{'type':'mcpToolCall','tool':'get_upgrade_plan','error':{'message':'failure'},'arguments':{},'result':None}}})
            c=o.result()['mcpCalls'][0];self.assertTrue(c['transportErrorPresent']);self.assertIsNone(c['responseOk']);self.assertEqual(c['targets'],[])
if __name__=='__main__':unittest.main()
