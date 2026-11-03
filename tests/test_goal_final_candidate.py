"""Parent response controls, never a substitute for actual installed MCP evidence."""
import copy,json,unittest
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from deploom_mcp.goal_contract import canonical_digest
import deploom_mcp.goal_final_candidate as parent

class FinalParentReceiptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ids=('registered-run','registered-candidate','a'*64,'apply-final-registered-candidate')
        self.observation={'ok':True,'checkpointId':'b'*64,'checkpointVerifiedHistorical':True,'currentWorkspaceVerified':True,'fullAcceptance':False}
    async def invoke(self,*,worker=None,current=None,code=0,timeout=False):
        from time import time
        payload=self.observation if worker is None else worker
        process=SimpleNamespace(stdout=json.dumps({'ok':True,'result':payload,'resultDigest':canonical_digest(payload)}),returncode=code,timed_out=timeout)
        context=SimpleNamespace(registration=SimpleNamespace(deadline=time()+60))
        with patch.object(parent,'_completed_replay',return_value=None),patch.object(parent,'load_final_grant',return_value=object()),patch.object(parent,'consume_final_grant',return_value=context),patch.object(parent,'run_process_async',new=AsyncMock(return_value=process)) as launch,patch.object(parent,'read_final_checkpoint',return_value=self.observation if current is None else current) as read:
            result=await parent.apply_goal_candidate(*self.ids)
            return result,launch.call_count,read.call_count
    async def test_only_matching_physically_reopened_current_result_is_returned(self):
        result,_,read=await self.invoke();self.assertEqual(result,self.observation);self.assertEqual(read,1)
    async def test_old_checkpoint_stdout_cannot_substitute_current_candidate(self):
        result,_,_=await self.invoke(worker={**self.observation,'checkpointId':'c'*64})
        self.assertFalse(result['ok']);self.assertEqual(result['error']['code'],'FINAL_PARENT_RECEIPT_MISMATCH')
    async def test_historical_only_even_matching_stdout_is_not_final_success(self):
        historical={**self.observation,'currentWorkspaceVerified':False}
        result,_,_=await self.invoke(worker=historical,current=historical)
        self.assertFalse(result['ok']);self.assertEqual(result['error']['code'],'FINAL_CURRENT_WORKSPACE_CONTINUITY_UNPROVEN')
    async def test_changed_owner_result_after_child_completion_is_not_accepted(self):
        result,_,_=await self.invoke(current={**self.observation,'fullAcceptance':True})
        self.assertFalse(result['ok']);self.assertEqual(result['error']['code'],'FINAL_PARENT_RECEIPT_MISMATCH')
    async def test_child_nonzero_cannot_borrow_a_prior_checkpoint(self):
        result,_,read=await self.invoke(code=73);self.assertFalse(result['ok']);self.assertEqual(read,0)
    async def test_timeout_cannot_borrow_a_prior_checkpoint(self):
        result,_,read=await self.invoke(timeout=True);self.assertFalse(result['ok']);self.assertEqual(read,0)
    async def test_query_does_not_launch_worker_or_project_commands(self):
        with patch('deploom_mcp.goal_execution_registry.load_host_registration',return_value=SimpleNamespace(policy={})),patch.object(parent,'run_process_async',new=AsyncMock()) as launch,patch.object(parent,'read_final_checkpoint',return_value=self.observation):
            self.assertEqual(await parent.get_goal_checkpoint(*self.ids[:3]),self.observation)
        launch.assert_not_called()

if __name__=='__main__':unittest.main()
