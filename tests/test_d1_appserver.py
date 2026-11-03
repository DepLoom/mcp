import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from d1_appserver import Monitor,budget_feasible,parse_tool_attempt,merge_tool_counts
from d1_pilot import Ledger,MODEL,EFFORT,EXPECTED_SEED_SHA256,check_ready
from types import SimpleNamespace

class AppServerBudgetTests(unittest.TestCase):
    def event(self,n=19000):
        return {'method':'thread/tokenUsage/updated','params':{'threadId':'t','turnId':'u','tokenUsage':{'total':{'inputTokens':n,'cachedInputTokens':12000,'outputTokens':8,'reasoningOutputTokens':0}}}}
    def test_interrupt_once_persists_all_updates_and_waits_for_completion(self):
        sent=[];saved=[];m=Monitor('t','u',25000,4000,sent.append,saved.append)
        m.ingest(self.event());m.ingest(self.event(21000))
        self.assertEqual(len(sent),1);self.assertEqual(len(saved),2)
        self.assertIsNone(m.status)
        m.ingest({'id':9001,'result':{}})
        m.ingest({'method':'turn/completed','params':{'threadId':'t','turn':{'id':'u','status':'interrupted'}}})
        self.assertTrue(m.ack);self.assertEqual(m.status,'interrupted')
    def test_malformed_identity_and_decreasing_usage_fail_closed(self):
        m=Monitor('t','u',25000,4000,lambda x:None,lambda x:None)
        m.ingest(self.event())
        with self.assertRaisesRegex(RuntimeError,'NON_MONOTONIC'):m.ingest(self.event(13000))
        e=self.event();e['params']['threadId']='wrong'
        with self.assertRaisesRegex(RuntimeError,'IDENTITY'):m.ingest(e)
        e=self.event();e['params']['tokenUsage']['total']['cachedInputTokens']=20000
        with self.assertRaisesRegex(RuntimeError,'BREAKDOWN'):m.ingest(e)
    def test_budget_precheck_before_any_auth_network_or_model(self):
        self.assertTrue(budget_feasible(120000,8000))
        args=SimpleNamespace(wall=600,tools=40,input_cap=18000,output_cap=4000,model=MODEL,effort=EFFORT,transport='app-server')
        with self.assertRaisesRegex(RuntimeError,'INITIAL_CONTEXT_EXCEEDS'):check_ready(args)
    def test_prestart_attempts_count_without_retaining_commands(self):
        event={'target':'codex_core::tools::code_mode::delegate','fields':{'message':'new'},
               'span':{'name':'code_mode.broker.invoke_tool','tool_name':'exec_command',
                       'runtime_tool_call_id':'tool-1','cell.id':'1','conversation.id':'t'},
               'secret':'DO_NOT_RETAIN'}
        parsed=parse_tool_attempt(json.dumps(event))
        self.assertEqual(parsed['params']['category'],'command_execution')
        self.assertNotIn('DO_NOT_RETAIN',json.dumps(parsed))
        self.assertEqual(sum(merge_tool_counts({'command_execution':1},{'command_execution':2}).values()),2)
        self.assertIsNone(parse_tool_attempt('not json'))
        event['fields']['message']='close'
        self.assertIsNone(parse_tool_attempt(json.dumps(event)))

    def test_raw_mcp_name_and_v2_item_are_not_double_counted(self):
        e={'target':'codex_core::tools::code_mode::delegate','fields':{'message':'new'},
           'span':{'name':'code_mode.broker.invoke_tool','tool_name':'get_upgrade_plan',
                   'runtime_tool_call_id':'tool-1','cell.id':'2','conversation.id':'t'}}
        parsed=parse_tool_attempt(json.dumps(e))
        self.assertEqual(parsed['params']['toolName'],'get_upgrade_plan')
        self.assertEqual(parsed['params']['category'],'mcp_tool_call')
        self.assertEqual(sum(merge_tool_counts({'mcp_tool_call':1},{parsed['params']['category']:1}).values()),1)

    def test_relative_workspace_is_normalized_before_server_and_rpc(self):
        import io
        from types import SimpleNamespace
        from unittest.mock import patch
        from d1_appserver import execute_appserver
        responses=[{'id':1,'result':{}},{'id':4,'result':{'exitCode':0,'stdout':'D1_SANDBOX_OK'}},{'id':5,'result':{'exitCode':0,'stdout':'D1_NODE_CHILD_OK'}},
                   {'id':2,'error':{'code':-1}}]
        proc=SimpleNamespace(stdin=io.StringIO(),stdout=io.StringIO(''.join(json.dumps(e)+'\n' for e in responses)),stderr=io.StringIO())
        owned=SimpleNamespace(proc=proc,stop=lambda:None)
        context=SimpleNamespace(__enter__=lambda _:owned,__exit__=lambda *a:None)
        class Context:
            def __enter__(self):return owned
            def __exit__(self,*a):pass
        with patch('d1_appserver.OwnedProcess',return_value=Context()) as factory:
            result=execute_appserver(['codex','--model','gpt-6.1-terra','task'],Path('.'),{},5,40,120000,8000)
        expected=str(Path('.').resolve())
        self.assertEqual(factory.call_args.kwargs['cwd'],expected)
        self.assertEqual(factory.call_args.kwargs['encoding'],'utf-8')
        self.assertEqual(factory.call_args.kwargs['errors'],'strict')
        sent=[json.loads(x) for x in proc.stdin.getvalue().splitlines()]
        self.assertEqual(next(e for e in sent if e['method']=='command/exec')['params']['cwd'],expected)
        self.assertTrue(result['sandboxReadWritePreflight'])
        self.assertEqual(result['stopReason'],'APP_SERVER_RPC_FAILED_THREAD_START')
        self.assertFalse(any(e['method']=='turn/start' for e in sent))

    def test_node_spawn_failure_stops_before_model_thread(self):
        import io
        from types import SimpleNamespace
        from unittest.mock import patch
        from d1_appserver import execute_appserver
        responses=[{'id':1,'result':{}},{'id':4,'result':{'exitCode':0,'stdout':'D1_SANDBOX_OK'}},
                   {'id':5,'result':{'exitCode':2,'stdout':'','stderr':'spawn EPERM'}}]
        proc=SimpleNamespace(stdin=io.StringIO(),stdout=io.StringIO(''.join(json.dumps(e)+'\n' for e in responses)),stderr=io.StringIO())
        class Context:
            def __enter__(self):return SimpleNamespace(proc=proc,stop=lambda:None)
            def __exit__(self,*args):pass
        with patch('d1_appserver.OwnedProcess',return_value=Context()):
            result=execute_appserver(['codex','--model','gpt-6.1-terra','task'],Path('.'),{},5,40,120000,8000)
        self.assertEqual(result['stopReason'],'NODE_CHILD_PREFLIGHT_FAILED')
        self.assertIsNone(result['nativeThreadId'])
        self.assertNotIn('thread/start',proc.stdin.getvalue())

    def test_relative_environment_paths_never_land_inside_workspace(self):
        from d1_pilot import isolated_env,codex_command
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'arm';root.mkdir()
            # Deliberately supply a relative input as the CLI accepts it.
            import os
            relative=Path(os.path.relpath(root))
            env=isolated_env(relative,relative/'auth')
            for name in ('HOME','USERPROFILE','CODEX_HOME','npm_config_cache'):
                if name in env:self.assertTrue(Path(env[name]).is_absolute(),name)
            self.assertEqual(Path(env['npm_config_cache']).parent,root.resolve())
            argv=codex_command('codex',relative/'workspace',MODEL,EFFORT,None,None)
            self.assertTrue(Path(argv[argv.index('-C')+1]).is_absolute())

    def test_native_usage_is_durable_while_running(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'run';ledger=Ledger(p,MODEL,EFFORT,EXPECTED_SEED_SHA256)
            ledger.mark('A','PREPARED');ledger.mark('A','RUNNING')
            ledger.record_usage('A',{'input_tokens':19000,'output_tokens':8})
            read=json.loads((p/'session-ledger.json').read_text())
            self.assertEqual(read['sessions']['A']['nativeUsage']['input_tokens'],19000)
            self.assertEqual(read['sessions']['B']['state'],'PENDING')

class Utf8ProtocolTests(unittest.TestCase):
    def test_invalid_utf8_fails_before_any_model_session(self):
        import io
        from types import SimpleNamespace
        from unittest.mock import patch
        from d1_appserver import execute_appserver
        proc=SimpleNamespace(stdin=io.StringIO(),stdout=io.TextIOWrapper(io.BytesIO(b'\xff'),encoding='utf-8'),stderr=io.StringIO())
        class Context:
            def __enter__(self):self.proc=proc;return self
            def __exit__(self,*args):return False
            def stop(self):pass
        with patch('d1_appserver.OwnedProcess',return_value=Context()):
            result=execute_appserver(['codex','--model',MODEL,'task'],Path('.'),{},2,40,650000,10000)
        self.assertEqual(result['stopReason'],'SERVER_EOF')
        self.assertIsNone(result['nativeThreadId'])
        self.assertIsNone(result['nativeUsage'])

if __name__=='__main__':unittest.main()
