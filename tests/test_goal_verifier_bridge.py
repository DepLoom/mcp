"""Private verifier lifetime negatives and host-only candidate admission."""
import dataclasses,json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock,patch
import test_goal_delta_trial as fixtures
from test_goal_candidate import fixture_registration
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_delta_trial import _apply_authorized_private_trial
from deploom_mcp.goal_candidate import verify_goal_candidate
from deploom_mcp.process_runner import ProcessRun

class PrivateVerifierLifetimeTests(unittest.TestCase):
    setUp=fixtures.HostDeltaTrialTests.setUp
    publish=fixtures.HostDeltaTrialTests.publish
    proposal=fixtures.HostDeltaTrialTests.proposal
    grant=fixtures.HostDeltaTrialTests.grant
    def test_verifier_exception_prevents_observation_and_cleans_workspace(self):
        proposal=self.proposal();grant=self.grant(proposal)
        def fail(project,*args):
            self.assertTrue(project.is_dir())
            self.assertEqual((project/'package-lock.json').read_bytes(),proposal.lock_bytes)
            raise RuntimeError('engineering verifier exception')
        with self.assertRaisesRegex(RuntimeError,'engineering verifier exception'):
            _apply_authorized_private_trial(grant,proposal,self.snapshot,_verification_action=fail)
        record=json.loads(next((self.state/'delta-trial-attempts').glob('*.json')).read_text())
        self.assertEqual(record['status'],'FAILED');self.assertNotIn('verificationObservation',record)
        self.assertEqual(list((self.state/'delta-trial-workspaces').iterdir()),[])
    def test_callback_cannot_change_outer_candidate_even_with_claimed_observation(self):
        proposal=self.proposal();grant=self.grant(proposal)
        def mutate(project,*args):
            (project/'index.js').write_text('unapproved mutation');return {'fullAcceptance':False}
        with self.assertRaisesRegex(GoalContractError,'PRIVATE_CANDIDATE_CHANGED_DURING_VERIFICATION'):
            _apply_authorized_private_trial(grant,proposal,self.snapshot,_verification_action=mutate)
    def test_baseline_scope_exit_failure_cannot_publish_completed_receipt(self):
        # Isolated failure-branch control; no actual verifier PASS is fabricated.
        from contextlib import contextmanager
        from types import SimpleNamespace
        import deploom_mcp.goal_verifier_bridge as bridge
        from deploom_mcp.goal_execution_registry import load_host_registration,OPERATION
        proposal=self.proposal();self.grant(proposal)
        cache=self.state/'owned-cache';cache.mkdir()
        env={**self.env,'DEPLOOM_MCP_OFFLINE_CACHE':str(cache)}
        @contextmanager
        def closing_failure(*args,**kwargs):
            yield self.snapshot
            raise OSError('engineering baseline retirement failure')
        record={'status':'VERIFIER_TRIAL_CLOSED_AWAITING_BASELINE_CLEANUP','fullAcceptance':False}
        (self.state/'delta-trial-attempts').mkdir()
        with patch.dict('os.environ',env):
            registration=load_host_registration('delta-control','candidate-1',self.binding['bindingDigest'],OPERATION)
            with patch.object(bridge,'load_host_registration',return_value=registration),patch.object(bridge,'_core',return_value=SimpleNamespace(verification_source_snapshot_scope=closing_failure)),patch.object(bridge,'_candidate_bytes',return_value=(self.after_manifest,self.after_lock)),patch.object(bridge,'propose_exact_delta',return_value=proposal),patch.object(bridge,'load_host_delta_grant',return_value=object()),patch.object(bridge,'_apply_authorized_private_trial',return_value=record):
                with self.assertRaisesRegex(OSError,'baseline retirement failure'):
                    bridge.execute_registered_engineering_candidate('delta-control','candidate-1',self.binding['bindingDigest'],OPERATION)
            saved=json.loads(bridge.trial_ledger_path(registration).read_text())
        self.assertEqual(saved['status'],'BASELINE_CLOSURE_FAILED')
        self.assertIsNot(saved.get('allOwnedSnapshotCleanupConfirmed'),True)
        self.assertFalse(saved['fullAcceptance'])

    def test_outer_cleanup_error_prevents_controlled_observation(self):
        import deploom_mcp.goal_delta_trial as trial
        proposal=self.proposal();grant=self.grant(proposal)
        with patch.object(trial,'_cleanup',side_effect=OSError('engineering outer cleanup failure')):
            with self.assertRaises(OSError):
                _apply_authorized_private_trial(grant,proposal,self.snapshot,_verification_action=lambda *a:{'fullAcceptance':False})
        record=json.loads(next((self.state/'delta-trial-attempts').glob('*.json')).read_text())
        self.assertEqual(record['status'],'CLEANUP_FAILED');self.assertFalse(record['fullAcceptance'])

class EngineeringWorkerAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdout_claim_without_durable_host_receipt_does_not_promote(self):
        registration=dataclasses.replace(fixture_registration(Path.cwd()),candidate_files={'manifest':'none.json','lock':'none-lock.json'})
        process=ProcessRun(0,json.dumps({'code':'ENGINEERING_CONTROLLED_VERIFICATION_OBSERVED','receiptDigest':'0'*64}),'')
        with patch('deploom_mcp.goal_candidate.load_host_registration',return_value=registration),patch('deploom_mcp.goal_candidate.run_process_async',new_callable=AsyncMock,return_value=process):
            result=await verify_goal_candidate(registration.run_id,registration.candidate_id,registration.binding['bindingDigest'],'verify-private-candidate')
        self.assertFalse(result['ok']);self.assertFalse(result['fullAcceptance'])
    async def test_receipt_cannot_publish_after_host_deadline(self):
        import tempfile,time
        from deploom_mcp.goal_contract import canonical_digest
        reg=fixture_registration(Path.cwd());approval={'candidateDigest':'a'*64,'deltaDigest':'b'*64,'origin':'ENGINEERING_CONTROL'}
        reg=dataclasses.replace(reg,candidate_files={'manifest':'none','lock':'none'},delta_authorization=approval)
        record={'status':'CONTROLLED_VERIFICATION_OBSERVED','runId':reg.run_id,'candidateId':reg.candidate_id,
            **approval,'authorizationOrigin':'ENGINEERING_CONTROL','fullAcceptance':False,'allOwnedSnapshotCleanupConfirmed':True,
            'verificationObservation':{'verificationAuthority':'ENGINEERING_CONTROLLED_VERIFICATION_OBSERVED',
                'bindingDigest':reg.binding['bindingDigest'],'fullAcceptance':False,'projectChecksExecuted':True,'observationComplete':True}}
        process=ProcessRun(0,json.dumps({'code':'ENGINEERING_CONTROLLED_VERIFICATION_OBSERVED','receiptDigest':canonical_digest(record)}),'')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'receipt.json';path.write_text(json.dumps(record))
            with patch('deploom_mcp.goal_candidate.load_host_registration',return_value=reg),patch('deploom_mcp.goal_candidate.run_process_async',new_callable=AsyncMock,return_value=process),patch('deploom_mcp.goal_verifier_bridge.trial_ledger_path',return_value=path),patch('deploom_mcp.goal_candidate.time.time',side_effect=[reg.deadline-10,reg.deadline+1]):
                result=await verify_goal_candidate(reg.run_id,reg.candidate_id,reg.binding['bindingDigest'],'verify-private-candidate')
        self.assertFalse(result['ok']);self.assertEqual(result['error']['code'],'EXECUTION_DEADLINE_EXCEEDED')

    async def test_nonzero_worker_cannot_replay_success_stdout(self):
        registration=dataclasses.replace(fixture_registration(Path.cwd()),candidate_files={'manifest':'none.json','lock':'none-lock.json'})
        process=ProcessRun(1,json.dumps({'code':'ENGINEERING_CONTROLLED_VERIFICATION_OBSERVED','receiptDigest':'0'*64}),'')
        with patch('deploom_mcp.goal_candidate.load_host_registration',return_value=registration),patch('deploom_mcp.goal_candidate.run_process_async',new_callable=AsyncMock,return_value=process):
            result=await verify_goal_candidate(registration.run_id,registration.candidate_id,registration.binding['bindingDigest'],'verify-private-candidate')
        self.assertFalse(result['ok']);self.assertFalse(result['fullAcceptance'])

if __name__=='__main__':unittest.main()
