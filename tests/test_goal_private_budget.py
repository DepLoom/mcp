"""Operator time allowance and actual frozen Core forwarding, no real project."""
import copy,dataclasses,time,unittest
from unittest.mock import patch,AsyncMock
import test_goal_candidate as candidate_fixture
import test_goal_frozen_candidate as frozen_fixture
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_execution_registry import private_execution_budgets,private_core_deadline,FROZEN_ROUTE
from deploom_mcp.goal_candidate import verify_goal_candidate
from deploom_mcp.process_runner import ProcessRun
from pathlib import Path


def registration(seconds=300):
 r=candidate_fixture.fixture_registration(Path.cwd())
 return dataclasses.replace(r,policy={'privateVerificationRoute':FROZEN_ROUTE,'privateVerificationBudgetSeconds':seconds},profile={'admission':{}},candidate_files={'manifest':'fixture-only.json'},delta_authorization={'origin':'PREAPPROVED_HOST_POLICY'})


class BudgetPolicyTests(unittest.TestCase):
 def test_default_and_explicit_limits_and_deadline_clamp(self):
  r=candidate_fixture.fixture_registration(Path.cwd());self.assertEqual(private_execution_budgets(r),{'coreSeconds':120,'workerSeconds':180,'explicit':False})
  r=dataclasses.replace(registration(),deadline=2000);self.assertEqual(private_execution_budgets(r)['workerSeconds'],420)
  self.assertEqual(private_core_deadline(r,1000)['effectiveCoreDeadline'],1300)
  self.assertEqual(private_core_deadline(dataclasses.replace(r,deadline=1010),1000)['effectiveCoreDeadline'],1010)
  with self.assertRaises(GoalContractError):private_core_deadline(r,2000)
 def test_invalid_or_untrusted_allowance_rejected(self):
  for value in [True,False,119,301,300.0,'300',None]:
   with self.subTest(value=value),self.assertRaises(GoalContractError):private_execution_budgets(registration(value))
  for r in [dataclasses.replace(registration(),delta_authorization={'origin':'ENGINEERING_CONTROL'}),dataclasses.replace(registration(),policy={'privateVerificationBudgetSeconds':300}),dataclasses.replace(registration(),profile={})]:
   with self.assertRaises(GoalContractError):private_execution_budgets(r)


class BudgetWorkerTests(unittest.IsolatedAsyncioTestCase):
 async def test_worker_receives_effective_watchdog_and_timeout_never_accepts(self):
  for remaining,expected in [(600,420),(10,10)]:
   reg=dataclasses.replace(registration(),deadline=1000+remaining)
   with patch('deploom_mcp.goal_candidate.load_host_registration',return_value=reg),patch('deploom_mcp.goal_candidate.time.time',return_value=1000),patch('deploom_mcp.goal_candidate.run_process_async',new_callable=AsyncMock,return_value=ProcessRun(1,'','',timed_out=True)) as run:
    result=await verify_goal_candidate('fixture-run','fixture-candidate',reg.binding['bindingDigest'],'verify-private-candidate')
   self.assertEqual(run.call_count,1);self.assertEqual(run.call_args.kwargs['timeout'],expected)
   self.assertFalse(result['ok']);self.assertFalse(result['fullAcceptance']);self.assertIsNone(result['verifierInvoked']);self.assertEqual(result['invocationStatus'],'UNKNOWN_AFTER_WORKER_TIMEOUT')
 async def test_invalid_budget_never_starts_worker(self):
  reg=registration(True)
  with patch('deploom_mcp.goal_candidate.load_host_registration',return_value=reg),patch('deploom_mcp.goal_candidate.run_process_async',new_callable=AsyncMock) as run:
   result=await verify_goal_candidate('fixture-run','fixture-candidate',reg.binding['bindingDigest'],'verify-private-candidate')
  run.assert_not_called();self.assertFalse(result['ok'])


class BudgetRealOwnerTests(frozen_fixture.FrozenRealOwnerTests):
 private_budget_seconds=300
 @classmethod
 def setUpClass(cls):
  import materialized_workspace_verifier as owner
  with patch.object(owner,'verify_current_workspace',wraps=owner.verify_current_workspace) as spy,patch.object(owner,'run_supervised',wraps=owner.run_supervised) as native:
   super().setUpClass();cls.forwarded=list(spy.call_args_list);cls.native_deadlines=[c.kwargs['timeout_seconds'] for c in native.call_args_list]
 def test_actual_owner_forwarding_and_ledger_match(self):
  o=self.record['verificationObservation'];b=o['executionBudget'];self.assertEqual(b['coreSeconds'],300);self.assertEqual(b['workerSeconds'],420)
  self.assertEqual(len(self.forwarded),1);self.assertEqual(self.forwarded[0].kwargs['deadline'],b['effectiveCoreDeadline'])
  self.assertLessEqual(b['effectiveCoreDeadline'],self.registration.deadline);self.assertLessEqual(b['effectiveCoreDeadline'],b['coreStartedAt']+300)
  self.assertEqual(len(self.native_deadlines),2);self.assertTrue(all(0<v<=min(300,self.registration.deadline-b['coreStartedAt']) for v in self.native_deadlines))
  self.assertTrue(self.valid())
 def test_tampered_operational_budget_cannot_close_receipt(self):
  for key,value in [('coreSeconds',120),('workerSeconds',180),('effectiveCoreDeadline',self.registration.deadline+1)]:
   bad=copy.deepcopy(self.record);bad['verificationObservation']['executionBudget'][key]=value;self.assertFalse(self.valid(bad))

if __name__=='__main__':unittest.main()
