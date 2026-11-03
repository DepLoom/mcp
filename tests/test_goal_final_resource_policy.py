"""Opt-in resource propagation and legacy rejection; no fake project acceptance."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.goal_final_resource_policy import (FinalResourceSpec,
    parse_final_resource_policy, registered_final_resource_policy)
from deploom_mcp.goal_current_proof_budget import (CurrentProofSpec,
    parse_current_proof_spec, registered_current_proof_spec)
from deploom_mcp import goal_current_proof_process as process
from deploom_mcp import goal_final_authorization as authority
from deploom_mcp.process_runner import ProcessRun


def registration():
    policy = {'hardPolicyRequired': False, 'finalResources': FinalResourceSpec().as_policy(),
              'currentProof': CurrentProofSpec(300, 2).as_policy()}
    digest = canonical_digest(policy)
    return SimpleNamespace(policy=policy, goal={'policyDigest': digest},
        binding={'identity': {'policyDigest': digest}, 'bindingDigest': 'b'*64},
        delta_authorization={'origin': 'PREAPPROVED_HOST_POLICY'},
        run_id='resource-run', candidate_id='resource-candidate', deadline=5000)


class PolicyTests(unittest.TestCase):
    def test_new_class_and_proof_are_explicit_digest_bound(self):
        reg=registration()
        self.assertEqual(registered_final_resource_policy(reg), FinalResourceSpec())
        self.assertEqual(registered_current_proof_spec(reg), CurrentProofSpec(300, 2))
        self.assertIsNone(parse_final_resource_policy({}))
        self.assertIsNone(parse_current_proof_spec({}))
        with self.assertRaises(GoalContractError): CurrentProofSpec(300)

    def test_strict_class_rejects_bad_types_unknown_fields_and_expanded_limits(self):
        valid=registration().policy
        for changes in ({'schemaVersion': True}, {'schemaVersion': 3},
                {'resourceClass': 'ANY'}, {'workerMaxSeconds': 1800},
                {'finalMaxSeconds': 1801}, {'returnHeadroomSeconds': 0},
                {'workerMaxSeconds': 1200.0}, {'extra': 1}):
            policy=copy.deepcopy(valid);policy['finalResources'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(GoalContractError):
                parse_current_proof_spec(policy)
        for bad in (None, [], {}, True):
            policy=copy.deepcopy(valid);policy['finalResources']=bad
            with self.subTest(bad=bad), self.assertRaises(GoalContractError):
                parse_current_proof_spec(policy)

    def test_v2_proof_and_resource_class_cannot_be_selected_independently(self):
        policy=registration().policy
        for bad in ({'currentProof': policy['currentProof']},
                    {'finalResources': policy['finalResources']},
                    dict(policy,currentProof=CurrentProofSpec(120).as_policy())):
            with self.subTest(bad=bad), self.assertRaises(GoalContractError): parse_current_proof_spec(bad)
        for seconds in (True, 0, 301, 300.0):
            bad=copy.deepcopy(policy);bad['currentProof']['maxSeconds']=seconds
            with self.subTest(seconds=seconds), self.assertRaises(GoalContractError): parse_current_proof_spec(bad)

    def test_old_digest_and_engineering_cannot_gain_resource_authority(self):
        for mode in ('goal','binding','origin'):
            reg=registration()
            if mode=='goal':reg.goal['policyDigest']='a'*64
            elif mode=='binding':reg.binding['identity']['policyDigest']='a'*64
            else:reg.delta_authorization['origin']='ENGINEERING_CONTROL'
            with self.subTest(mode=mode), self.assertRaises(GoalContractError): registered_final_resource_policy(reg)

    def test_legacy_cap_above600_and_v2_above1800_reject_before_receipt(self):
        for v2,seconds in ((False,601),(False,1800),(True,1801),(True,True)):
            reg=registration()
            if not v2:reg.policy={}
            reg.final_authorization={'schemaVersion':1,'attemptId':'attempt','budgetSeconds':seconds,'acceptedReceiptDigest':'a'*64}
            with patch.object(authority,'load_host_registration',return_value=reg), \
                    patch.object(authority,'require_registered_execution_class',return_value='PREAPPROVED_HOST_POLICY'), \
                    self.subTest(v2=v2,seconds=seconds), self.assertRaisesRegex(GoalContractError,'INVALID_FINAL_AUTHORIZATION'):
                authority.load_final_grant('r','c','b'*64,authority.FINAL_OPERATION)

    def test_v2_1800_is_admitted_but_still_requires_real_historical_receipt(self):
        reg=registration();reg.final_authorization={'schemaVersion':1,'attemptId':'attempt','budgetSeconds':1800,'acceptedReceiptDigest':'a'*64}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);receipt=root/'receipt.json';receipt.write_text('{}')
            with patch.object(authority,'load_host_registration',return_value=reg), \
                    patch.object(authority,'require_registered_execution_class',return_value='PREAPPROVED_HOST_POLICY'), \
                    patch.object(authority,'configured_state_root',return_value=root), \
                    patch('deploom_mcp.goal_verifier_bridge.trial_ledger_path',return_value=receipt), \
                    self.assertRaisesRegex(GoalContractError,'FINAL_HISTORICAL_RECEIPT_UNPROVEN'):
                authority.load_final_grant('r','c','b'*64,authority.FINAL_OPERATION)


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.reg=registration();self.context=SimpleNamespace(registration=self.reg,
            attempt_id='resource-attempt',authorization_digest='c'*64,budget_seconds=1800)
        self.key=canonical_digest({'runId':self.reg.run_id,'candidateId':self.reg.candidate_id,'attemptId':self.context.attempt_id})
        self.path=self.root/'final-apply-attempts'/self.key/'transaction.json'

    def reserve(self,now=1000):
        with patch('deploom_mcp.goal_execution_registry.configured_state_root',return_value=self.root):
            return process.final_worker_reservation(self.context,now)

    def test_new_worker_gets1200_without_starving_parent_and_without_durable_write(self):
        self.assertEqual(self.reserve(),{'workerTimeout':1200,'originalDeadlineUTC':2800})
        self.assertFalse(self.path.exists())

    def test_host_deadline_clamps_worker_before_spawn(self):
        self.reg.deadline=1500
        self.assertEqual(self.reserve(),{'workerTimeout':140,'originalDeadlineUTC':1500})
        self.reg.deadline=1360
        with self.assertRaisesRegex(GoalContractError,'FINAL_CURRENT_PROOF_BUDGET_INSUFFICIENT'):self.reserve()

    def test_resume_uses_original_deadline_and_rejects_extended_foreign_journal(self):
        record={'runId':self.reg.run_id,'candidateId':self.reg.candidate_id,
            'attemptId':self.context.attempt_id,'attemptKey':self.key,'bindingDigest':'b'*64,
            'authorizationDigest':'c'*64,'startedAt':600,'deadline':2400}
        self.path.parent.mkdir(parents=True)
        def save():self.path.write_text(json.dumps({'schemaVersion':1,'payload':record,'sha256':canonical_digest(record)}))
        save();before=self.path.read_bytes()
        self.assertEqual(self.reserve(),{'workerTimeout':1040,'originalDeadlineUTC':2400})
        with self.assertRaises(GoalContractError):self.reserve(2040)
        self.assertEqual(self.path.read_bytes(),before)
        for change in ({'deadline':2800},{'authorizationDigest':'d'*64}):
            record.update(change);save()
            with self.subTest(change=change),self.assertRaisesRegex(GoalContractError,'FINAL_TRANSACTION_IDENTITY_CHANGED'):self.reserve()


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_300_second_allowance_reaches_fixed_child_watchdog_and_utc_argument(self):
        reg=registration();result={'currentWorkspaceVerified':True}
        native=ProcessRun(0,json.dumps({'ok':True,'result':result,'resultDigest':canonical_digest(result)}),'')
        start=time.time()
        with patch.object(process,'run_process_async',new=AsyncMock(return_value=native)) as launch:
            self.assertEqual(await process.supervised_current_read('r','c','b'*64,reg),result)
        self.assertGreater(launch.call_args.kwargs['timeout'],290)
        self.assertLessEqual(launch.call_args.kwargs['timeout'],300)
        child_deadline=float(launch.call_args.args[0][-1]);self.assertGreater(child_deadline,start+290)
        self.assertLessEqual(child_deadline,time.time()+300)

    async def test_larger_allowance_still_rejects_timeout_and_original_expired_deadline(self):
        reg=registration();native=ProcessRun(0,'{}','',timed_out=True)
        with patch.object(process,'run_process_async',new=AsyncMock(return_value=native)) as launch:
            with self.assertRaises(GoalContractError):await process.supervised_current_read('r','c','b'*64,reg)
        with patch.object(process,'run_process_async',new=AsyncMock()) as launch:
            with self.assertRaises(GoalContractError):
                await process.supervised_current_read('r','c','b'*64,reg,original_deadline_utc=time.time()-1)
        launch.assert_not_called()
