"""Passive reader contract tests; mocked readers do not verify a real project."""
import hashlib,json,tempfile,unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from deploom_mcp import goal_final_checkpoint as owner
from deploom_mcp.goal_current_read_trace import CurrentReadTrace

class CurrentReadTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.node=self.root/'node.exe';self.node.write_bytes(b'node')
        self.npm=self.root/'node_modules/npm/bin/npm-cli.js';self.npm.parent.mkdir(parents=True);self.npm.write_bytes(b'npm')
        self.context=SimpleNamespace(attempt_id='test-attempt',authorization_digest='a'*64,
            registration=SimpleNamespace(run_id='test-run',candidate_id='test-candidate',policy={},
                binding={'bindingDigest':'b'*64,'identity':{'implementationDigest':'c'*64}}))
        self.cp={'checkpointId':'d'*64,'sourceSnapshotKey':'sealed-test-source'}
        self.record={'windowsMoves':{'package':{}},'dependencyTreeMove':{},
            'liveObservation':{'materializedSourceOrigin':{'toolchain':{
                'nodeSha256':hashlib.sha256(b'node').hexdigest(),'npmCliSha256':hashlib.sha256(b'npm').hexdigest()}}}}
        self.core=Mock();self.core.source_snapshot_fingerprint.return_value='sealed-test-source'
        self.current_done=False
    def invoke(self,enabled=True,*,fingerprint='sealed-test-source',swap='DELIVERED',tree_error=None,current=True,write_error=False):
        self.core.source_snapshot_fingerprint.return_value=fingerprint
        captures=[];self.current_done=False
        def final_state(*a,**kw):
            self.current_done=True
            if kw.get('_trace'):
                kw['_trace'].mark('environment',True);kw['_trace'].mark('tap',current)
            return current
        def publish(identity,value):
            captures.append(value)
            if value['events'][-1]['result'] is True:self.assertTrue(self.current_done)
            if write_error:raise OSError('private path must not affect verdict')
        with ExitStack() as stack:
            stack.enter_context(patch.object(owner,'inspect_checkpoint_evidence',return_value=(self.root,self.record,self.cp,True,self.core)))
            stack.enter_context(patch.object(owner,'observe_swap',return_value=swap))
            stack.enter_context(patch.object(owner,'observe_delivered_tree',side_effect=tree_error))
            stack.enter_context(patch.object(owner.shutil,'which',return_value=str(self.node)))
            stack.enter_context(patch.object(owner,'current_state_matches',side_effect=final_state))
            stack.enter_context(patch('deploom_mcp.goal_current_read_trace._publish',side_effect=publish))
            value=owner.inspect_checkpoint(self.context,_diagnostics=enabled)
        return json.dumps(value,sort_keys=True,separators=(',',':')).encode(),captures
    def test_canonical_response_equal_success_mismatch_false_and_exception(self):
        for args in ({},{'fingerprint':'other'},{'swap':'PENDING'},{'tree_error':OSError('private secret path')},{'current':False}):
            with self.subTest(args=args):
                original,_=self.invoke(False,**args);observed,events=self.invoke(True,**args)
                self.assertEqual(original,observed);self.assertEqual(len(events),1)
                self.assertLessEqual(len(events[0]['events']),32)
                self.assertNotIn('private secret path',json.dumps(events))
    def test_diagnostic_io_failure_never_changes_true_or_false(self):
        for args in ({},{'fingerprint':'other'},{'current':False}):
            with self.subTest(args=args):
                expected,_=self.invoke(False,**args);observed,_=self.invoke(True,write_error=True,**args)
                self.assertEqual(expected,observed)
    def test_exception_preserves_safe_type_without_message(self):
        _,values=self.invoke(tree_error=OSError('credential-looking prose'))
        errors=[v for v in values[0]['events'] if v['result']=='EXCEPTION']
        self.assertEqual(errors[0]['gate'],'tree');self.assertEqual(errors[0]['exceptionType'],'OSError')
        self.assertNotIn('credential-looking',json.dumps(values))
        self.assertFalse(values[0]['events'][-1]['result'])
    def test_delayed_or_pending_swap_cannot_become_current(self):
        _,values=self.invoke(swap='PENDING')
        self.assertFalse(self.current_done);self.assertFalse(values[0]['events'][-1]['result'])
        self.assertEqual(values[0]['events'][-2]['gate'],'swaps')
    def test_disabled_recorder_does_not_publish(self):
        _,values=self.invoke(False);self.assertEqual(values,[])
    def test_actual_diagnostic_is_bounded_identity_joined_and_non_authoritative(self):
        trace=CurrentReadTrace()
        for _ in range(40):trace.mark('source',True)
        trace.exception(OSError('secret'))
        self.assertEqual(len(trace.events),32)
        with patch('deploom_mcp.goal_execution_registry.configured_state_root',return_value=self.root):
            trace.persist(self.context,self.cp)
            paths=list((self.root/'current-read-diagnostics').glob('*/latest.json'))
            self.assertEqual(len(paths),1);first=paths[0].read_bytes()
            value=json.loads(first);identity=value['identity']
            self.assertEqual(identity['implementationDigest'],'c'*64);self.assertEqual(identity['bindingDigest'],'b'*64)
            self.assertEqual(identity['authorizationDigest'],'a'*64);self.assertEqual(identity['runId'],'test-run')
            self.assertEqual(identity['candidateId'],'test-candidate');self.assertEqual(identity['attemptId'],'test-attempt')
            self.assertEqual(value['authority'],'DIAGNOSTIC_ONLY_NOT_ACCEPTANCE');self.assertNotIn('currentWorkspaceVerified',value)
            trace.persist(self.context,self.cp)
            self.assertEqual(paths[0].read_bytes(),first)
            self.assertEqual(len(list((self.root/'current-read-diagnostics').glob('*/latest.json'))),1)
            self.assertFalse(list(self.root.rglob('*.tmp')))
    def test_original_environment_and_tap_predicates_and_deadline_preserved(self):
        self.context.final_profile={}
        for environment,tap in ((False,True),(True,False),(True,True)):
            trace=CurrentReadTrace()
            with patch.object(owner,'current_environment_matches',return_value=environment) as env, \
                 patch('deploom_mcp.goal_tap_nyc_execution.physically_current',return_value=tap) as check, \
                 patch.object(owner.time,'time',return_value=100.):
                result=owner.current_state_matches(self.context,self.record,self.root,_trace=trace)
            self.assertEqual(result,environment and tap)
            self.assertEqual(check.call_count,int(environment));env.assert_called_once()
            if environment:self.assertEqual(check.call_args.kwargs['deadline'],120.)
            self.assertEqual(trace.events[-1]['gate'],'tap' if environment else 'environment')
