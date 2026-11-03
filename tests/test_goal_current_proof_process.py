"""Host resource binding, reservation, and supervised-reader rejection controls."""
import asyncio
import dataclasses
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.goal_current_proof_budget import CurrentProofSpec, registered_current_proof_spec
from deploom_mcp import goal_current_proof_process as process
from deploom_mcp import goal_final_candidate as parent
from deploom_mcp.process_runner import ProcessRun


def registration(seconds=20):
    policy = {'hardPolicyRequired': False, 'currentProof': CurrentProofSpec(seconds).as_policy()}
    digest = canonical_digest(policy)
    return SimpleNamespace(run_id='budget-run', candidate_id='budget-candidate',
        policy=policy, goal={'policyDigest':digest}, binding={'bindingDigest':'b'*64,'identity':{'policyDigest':digest}},
        delta_authorization={'origin':'PREAPPROVED_HOST_POLICY'}, deadline=2000)


class HostSpecTests(unittest.TestCase):
    def test_pinned_policy_accepted_and_legacy_absence_unchanged(self):
        self.assertEqual(registered_current_proof_spec(registration(90)).max_seconds,90)
        self.assertIsNone(registered_current_proof_spec(SimpleNamespace(policy={})))

    def test_engineering_origin_cannot_select_new_budget(self):
        reg=registration();reg.delta_authorization={'origin':'ENGINEERING_CONTROL'}
        with self.assertRaisesRegex(GoalContractError,'CURRENT_PROOF_HOST_POLICY_REQUIRED'):
            registered_current_proof_spec(reg)

    def test_grant_consumption_is_local_opaque_handle_not_persisted_attempt_spending(self):
        from deploom_mcp import goal_final_authorization as authority
        context=object()
        with patch('deploom_mcp.goal_execution_registry.configured_state_root',side_effect=AssertionError('no durable state access')):
            for _ in range(2):
                grant=authority._FinalGrant();authority._GRANTED[grant]=context
                self.assertIs(authority.consume_final_grant(grant),context)
                with self.assertRaises(GoalContractError):authority.consume_final_grant(grant)

    def test_old_goal_or_binding_cannot_gain_larger_budget(self):
        for field in ('goal','binding'):
            reg=registration()
            if field=='goal':reg.goal['policyDigest']='a'*64
            else:reg.binding['identity']['policyDigest']='a'*64
            with self.subTest(field=field),self.assertRaisesRegex(GoalContractError,'CURRENT_PROOF_POLICY_BINDING_MISMATCH'):
                registered_current_proof_spec(reg)


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.context=SimpleNamespace(registration=registration(90),attempt_id='budget-attempt',authorization_digest='c'*64,budget_seconds=600)
        reg=self.context.registration
        self.key=canonical_digest({'runId':reg.run_id,'candidateId':reg.candidate_id,'attemptId':self.context.attempt_id})
        self.path=self.root/'final-apply-attempts'/self.key/'transaction.json'

    def reserve(self,now=1000):
        with patch('deploom_mcp.goal_execution_registry.configured_state_root',return_value=self.root):
            return process.final_worker_reservation(self.context,now)

    def save(self,**changes):
        reg=self.context.registration
        record={'runId':reg.run_id,'candidateId':reg.candidate_id,'attemptId':self.context.attempt_id,
            'attemptKey':self.key,'bindingDigest':'b'*64,'authorizationDigest':'c'*64,'startedAt':600,'deadline':1200}
        record.update(changes);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.path.write_text(json.dumps({'schemaVersion':1,'payload':record,'sha256':canonical_digest(record)}),encoding='utf-8')

    def test_fresh_attempt_reserves_parent_proof_and_return(self):
        self.assertEqual(self.reserve(),{'workerTimeout':450,'originalDeadlineUTC':1600})
        self.assertFalse(self.path.exists())

    def test_restarted_attempt_uses_original_persisted_deadline(self):
        self.save();self.assertEqual(self.reserve(),{'workerTimeout':50,'originalDeadlineUTC':1200})

    def test_insufficient_original_time_rejects_without_mutation(self):
        self.save();before=self.path.read_bytes()
        with self.assertRaisesRegex(GoalContractError,'FINAL_CURRENT_PROOF_BUDGET_INSUFFICIENT'):
            self.reserve(now=1050)
        self.assertEqual(self.path.read_bytes(),before)

    def test_terminal_identity_detects_corrupt_foreign_and_nonterminal_after_probe(self):
        reg=self.context.registration
        reg.final_authorization={'attemptId':self.context.attempt_id,'budgetSeconds':600}
        self.context.authorization_digest=canonical_digest(reg.final_authorization)
        self.save(authorizationDigest=self.context.authorization_digest,phase='TERMINAL_PASS',checkpointId='d'*64,checkpointDigest='e'*64)
        with patch('deploom_mcp.goal_execution_registry.configured_state_root',return_value=self.root):
            expected=process.terminal_read_identity(reg)
            for changes in ({'phase':'DEPENDENCIES_READY'},{'checkpointId':'f'*64},{'runId':'foreign'}):
                values={'authorizationDigest':self.context.authorization_digest,'phase':'TERMINAL_PASS','checkpointId':'d'*64,'checkpointDigest':'e'*64};values.update(changes);self.save(**values)
                with self.subTest(changes=changes),self.assertRaises(GoalContractError):process.terminal_read_identity(reg,expected)
            for raw in ('{}','[]','null','1'):
                self.path.write_text(raw,encoding='utf-8')
                with self.subTest(raw=raw),self.assertRaises(GoalContractError):process.terminal_read_identity(reg,expected)

    def test_foreign_and_extended_journal_reject_without_mutation(self):
        for changes in ({'authorizationDigest':'d'*64},{'deadline':1300},{'startedAt':True}):
            self.save(**changes);before=self.path.read_bytes()
            with self.subTest(changes=changes),self.assertRaises(GoalContractError):self.reserve()
            self.assertEqual(self.path.read_bytes(),before)


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.reg=registration();self.ids=('budget-run','budget-candidate','b'*64)
        self.result={'ok':True,'currentWorkspaceVerified':True,'checkpointVerifiedHistorical':True}

    def payload(self,**changes):
        data={'ok':True,'result':self.result,'resultDigest':canonical_digest(self.result)};data.update(changes)
        return json.dumps(data)

    async def invoke(self,payload=None,*,code=0,timeout=False):
        native=ProcessRun(code,self.payload() if payload is None else payload,'',timed_out=timeout)
        with patch.object(process,'run_process_async',new=AsyncMock(return_value=native)) as launch:
            value=await process.supervised_current_read(*self.ids,self.reg)
        return value,launch.call_args

    async def test_fixed_read_only_command_and_positive_bounded_watchdog(self):
        value,call=await self.invoke();self.assertEqual(value,self.result)
        command=call.args[0]
        self.assertEqual(command[3:5],['deploom_mcp.goal_final_worker','read-budgeted'])
        self.assertEqual(command[5:8],list(self.ids));self.assertEqual(len(command),9)
        self.assertGreater(call.kwargs['timeout'],0);self.assertLessEqual(call.kwargs['timeout'],20)

    async def test_timeout_nonzero_malformed_and_digest_mismatch_never_accept(self):
        for kwargs in ({'timeout':True},{'code':1},{'payload':'[]'},
                       {'payload':self.payload(resultDigest='d'*64)},{'payload':self.payload(ok=1)}):
            with self.subTest(kwargs=kwargs),self.assertRaises(GoalContractError):await self.invoke(**kwargs)

    async def test_expired_operation_rejects_before_spawn(self):
        with patch.object(process,'run_process_async',new=AsyncMock()) as launch:
            with self.assertRaises(GoalContractError):
                await process.supervised_current_read(*self.ids,self.reg,original_deadline_utc=time.time()-1)
        launch.assert_not_called()

    async def test_parent_keeps_worker_and_independent_reader_digest_equality(self):
        context=SimpleNamespace(registration=self.reg)
        result={**self.result,'checkpointId':'c'*64}
        native=ProcessRun(0,self.payload(),'')
        with patch.object(parent,'_completed_replay',return_value=None), \
             patch.object(parent,'load_final_grant',return_value=object()), \
             patch.object(parent,'consume_final_grant',return_value=context), \
             patch.object(parent,'final_worker_reservation',return_value={'workerTimeout':100,'originalDeadlineUTC':time.time()+120}), \
             patch.object(parent,'run_process_async',new=AsyncMock(return_value=native)), \
             patch.object(parent,'supervised_current_read',new=AsyncMock(return_value=result)):
            value=await parent.apply_goal_candidate(*self.ids,'apply-final-registered-candidate')
        self.assertEqual(value['error']['code'],'FINAL_PARENT_RECEIPT_MISMATCH')

    async def test_terminal_replay_uses_reader_only_and_does_not_renew_grant(self):
        result={**self.result,'isCurrentIncumbent':True}
        with patch.object(parent,'_completed_replay',return_value=process.PendingCurrentReplay(self.reg)), \
             patch.object(parent,'load_final_grant') as grant, \
             patch.object(parent,'run_process_async',new=AsyncMock()) as writer, \
             patch.object(parent,'supervised_current_read',new=AsyncMock(return_value=result)) as reader:
            value=await parent.apply_goal_candidate(*self.ids,'apply-final-registered-candidate')
        self.assertEqual(value,result);grant.assert_not_called();writer.assert_not_called();reader.assert_awaited_once()

    async def test_bounded_reap_cannot_report_unconfirmed_stop_as_success(self):
        from deploom_mcp import process_runner
        proc=SimpleNamespace(wait=AsyncMock(return_value=0))
        async def expired(awaitable,*,timeout):
            self.assertEqual(timeout,10.0)
            awaitable.close()
            raise asyncio.TimeoutError
        with patch.object(process_runner.asyncio,'wait_for',side_effect=expired):
            with self.assertRaisesRegex(RuntimeError,'PROCESS_TREE_STOP_UNCONFIRMED'):
                await process_runner._wait_killed_process(proc)

    async def test_real_worker_seventh_argument_dispatch_preserves_terminal_pin(self):
        import os,subprocess,sys
        expected={'attemptId':'pinned-attempt','authorizationDigest':'a'*64,
                  'journalDigest':'b'*64,'checkpointId':'c'*64,'checkpointDigest':'d'*64}
        code = "\n".join(['import sys,json', 'from deploom_mcp import goal_final_worker as w', 'expected=json.loads(sys.argv[1])', 'def read(*args,**kwargs):', "    assert args==('run','candidate','a'*64)", "    assert kwargs=={'_proof_deadline_utc':123.5,'_expected_terminal':expected}", "    return {'dispatch':'CONTROL_ONLY_NO_PHYSICAL_PROOF'}", 'w.read_final_checkpoint=read', "sys.argv=['worker','read-budgeted','run','candidate','a'*64,'123.5',json.dumps(expected)]", 'raise SystemExit(w.main())'])
        result=subprocess.run([sys.executable,'-B','-c',code,json.dumps(expected)],
                              capture_output=True,text=True,timeout=10,env=dict(os.environ))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['result']['dispatch'],'CONTROL_ONLY_NO_PHYSICAL_PROOF')

    async def test_no_time_reserve_never_starts_mutating_worker(self):
        with patch.object(parent,'_completed_replay',return_value=None), \
             patch.object(parent,'load_final_grant',return_value=object()), \
             patch.object(parent,'consume_final_grant',return_value=SimpleNamespace(registration=self.reg)), \
             patch.object(parent,'final_worker_reservation',side_effect=GoalContractError('FINAL_CURRENT_PROOF_BUDGET_INSUFFICIENT')), \
             patch.object(parent,'run_process_async',new=AsyncMock()) as writer:
            value=await parent.apply_goal_candidate(*self.ids,'apply-final-registered-candidate')
        self.assertEqual(value['error']['code'],'FINAL_CURRENT_PROOF_BUDGET_INSUFFICIENT');writer.assert_not_called()


if __name__=='__main__':unittest.main()
