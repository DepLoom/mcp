"""Read-only terminal dispatch boundaries; fixtures are not project acceptance."""
import asyncio,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from contextlib import ExitStack
from deploom_mcp.goal_contract import canonical_digest,GoalContractError
import deploom_mcp.goal_final_candidate as parent
import deploom_mcp.goal_execution_registry as registry

class TerminalReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.ids=('run','candidate','a'*64,'apply-final-registered-candidate')
        self.cap={'attemptId':'one-attempt','budgetSeconds':5}
        self.key=canonical_digest({'runId':self.ids[0],'candidateId':self.ids[1],'attemptId':self.cap['attemptId']})
        self.path=self.root/'final-apply-attempts'/self.key/'transaction.json';self.path.parent.mkdir(parents=True)
        self.record={'runId':self.ids[0],'candidateId':self.ids[1],'attemptKey':self.key,'attemptId':self.cap['attemptId'],
            'bindingDigest':self.ids[2],'authorizationDigest':canonical_digest(self.cap),'phase':'TERMINAL_PASS',
            'startedAt':time.time()-60,'deadline':time.time()-55,'npmAttempts':1,'liveCheckAttempts':1}
        self.reg=SimpleNamespace(final_authorization=self.cap)
        self.current={'ok':True,'currentWorkspaceVerified':True,'isCurrentIncumbent':True,'checkpointId':'b'*64}
        self.save()
    def save(self):
        self.path.write_text(json.dumps({'schemaVersion':1,'payload':self.record,'sha256':canonical_digest(self.record)}),encoding='utf-8')
    async def invoke(self,*,current=None,reader_error=None):
        before=self.path.read_bytes() if self.path.exists() else None
        with ExitStack() as s:
            s.enter_context(patch.object(registry,'configured_state_root',return_value=self.root))
            s.enter_context(patch.object(registry,'load_host_registration',return_value=self.reg))
            reader=s.enter_context(patch.object(parent,'read_final_checkpoint',return_value=self.current if current is None else current,side_effect=reader_error))
            normal=s.enter_context(patch.object(parent,'load_final_grant',side_effect=GoalContractError('EXECUTION_DEADLINE_EXCEEDED')))
            worker=s.enter_context(patch.object(parent,'run_process_async',new=AsyncMock()))
            result=await parent.apply_goal_candidate(*self.ids)
        self.assertEqual(before,self.path.read_bytes() if self.path.exists() else None)
        worker.assert_not_called()
        return result,reader,normal
    async def test_expired_terminal_returns_same_current_without_worker_or_mutation(self):
        result,reader,normal=await self.invoke();self.assertEqual(result,self.current);reader.assert_called_once_with(*self.ids[:3]);normal.assert_not_called()
    async def test_terminal_not_current_or_not_incumbent_never_falls_back(self):
        for field in ['currentWorkspaceVerified','isCurrentIncumbent']:
            with self.subTest(field=field):
                result,_,normal=await self.invoke(current={**self.current,field:False});self.assertFalse(result['ok']);normal.assert_not_called()
    async def test_reader_failure_never_falls_back(self):
        result,_,normal=await self.invoke(reader_error=GoalContractError('FINAL_IMPLEMENTATION_OR_TOOLCHAIN_CHANGED'))
        self.assertFalse(result['ok']);normal.assert_not_called()
    async def test_missing_expired_attempt_cannot_start_worker(self):
        self.path.unlink();result,reader,normal=await self.invoke();self.assertFalse(result['ok']);reader.assert_not_called();normal.assert_called_once()
    async def test_inflight_expired_keeps_normal_deadline_gate(self):
        self.record['phase']='DEPENDENCIES_READY';self.save()
        result,reader,normal=await self.invoke();self.assertFalse(result['ok']);reader.assert_not_called();normal.assert_called_once()
    async def test_failed_journal_rejected_without_execution_fallback(self):
        self.record['phase']='FAILED';self.save();result,reader,normal=await self.invoke();self.assertFalse(result['ok']);reader.assert_not_called();normal.assert_not_called()
    async def test_corrupted_seal_rejected_without_fallback(self):
        value=json.loads(self.path.read_bytes());value['sha256']='f'*64;self.path.write_text(json.dumps(value),encoding='utf-8')
        result,reader,normal=await self.invoke();self.assertFalse(result['ok']);reader.assert_not_called();normal.assert_not_called()
    async def test_foreign_identity_and_authorization_rejected(self):
        for field in ['attemptKey','attemptId','runId','candidateId','bindingDigest','authorizationDigest']:
            old=self.record[field];self.record[field]='foreign';self.save()
            with self.subTest(field=field):
                result,reader,normal=await self.invoke();self.assertFalse(result['ok']);reader.assert_not_called();normal.assert_not_called()
            self.record[field]=old
    async def test_wrong_external_authorization_rejected_before_probe(self):
        self.ids=(*self.ids[:3],'apply-private-candidate');result,reader,normal=await self.invoke();self.assertFalse(result['ok']);reader.assert_not_called();normal.assert_not_called()

if __name__=='__main__':unittest.main()
