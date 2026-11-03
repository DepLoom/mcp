"""Owning reader budget wiring, with physical gates mocked (not acceptance)."""
import json
import unittest
from unittest.mock import patch

import test_goal_current_read_trace as trace_fixture
from test_goal_current_proof_budget import Clock
from deploom_mcp import goal_final_checkpoint as owner
from deploom_mcp.goal_contract import canonical_digest


class SharedReaderBudgetTests(unittest.TestCase):
    def setUp(self):
        self.fixture = trace_fixture.CurrentReadTraceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.clock = Clock()

    def invoke(self, budget, **kwargs):
        original = owner.inspect_checkpoint
        with patch.object(owner, 'inspect_checkpoint',
                          side_effect=lambda *a, **kw: original(*a, **kw, _budget=budget)):
            raw, diagnostics = self.fixture.invoke(**kwargs)
        return json.loads(raw), diagnostics

    def test_complete_budget_path_preserves_canonical_legacy_response(self):
        old, _ = self.fixture.invoke()
        current, _ = self.invoke(self.clock.budget())
        self.assertEqual(canonical_digest(json.loads(old)), canonical_digest(current))
        self.assertTrue(current['currentWorkspaceVerified'])

    def test_source_consumes_budget_before_tree(self):
        def source(*args,**kwargs):
            self.clock.advance(20)
            return 'sealed-test-source'
        self.fixture.core.source_snapshot_fingerprint.side_effect = source
        value, diagnostics = self.invoke(self.clock.budget())
        self.assertFalse(value['currentWorkspaceVerified'])
        self.assertFalse(any(e['gate'] == 'tree' for e in diagnostics[0]['events']))

    def test_tree_consumes_same_budget_without_fresh_environment_window(self):
        def tree(*args, **kwargs):
            self.clock.advance(20)
        value, _ = self.invoke(self.clock.budget(), tree_error=tree)
        self.assertFalse(value['currentWorkspaceVerified'])
        self.assertFalse(self.fixture.current_done)

    def test_diagnostic_write_after_deadline_cannot_return_current_true(self):
        budget = self.clock.budget()
        from deploom_mcp.goal_current_read_trace import CurrentReadTrace
        original = CurrentReadTrace.persist
        def delayed(*a, **kw):
            result = original(*a, **kw)
            self.clock.advance(20)
            return result
        with patch.object(CurrentReadTrace, 'persist', delayed):
            value, _ = self.invoke(budget)
        self.assertFalse(value['currentWorkspaceVerified'])
        self.assertTrue(value['checkpointVerifiedHistorical'])

    def test_source_drift_during_first_tree_cannot_borrow_earlier_hash(self):
        self.fixture.core.source_snapshot_fingerprint.side_effect=['sealed-test-source','changed-source']
        value,_=self.invoke(self.clock.budget())
        self.assertFalse(value['currentWorkspaceVerified'])
        self.assertTrue(value['checkpointVerifiedHistorical'])

    def test_second_full_tree_failure_cannot_borrow_first_tree_success(self):
        calls=[]
        def tree(*a,**kw):
            calls.append(1)
            if len(calls)==2:raise OSError('changed backup')
        value,_=self.invoke(self.clock.budget(),tree_error=tree)
        self.assertFalse(value['currentWorkspaceVerified']);self.assertEqual(len(calls),2)

    def registered(self,*,incumbent=True,changed_checkpoint=False):
        from test_goal_current_proof_process import registration
        self.fixture.context.registration=registration()
        cp=self.fixture.cp
        current={'currentWorkspaceVerified':True,'isCurrentIncumbent':True,
                 'checkpointId':cp['checkpointId'],'checkpointDigest':canonical_digest(cp),
                 'attemptId':self.fixture.context.attempt_id,'checkpointVerifiedHistorical':True}
        identity={'attemptId':current['attemptId'],'checkpointId':current['checkpointId'],
                  'checkpointDigest':current['checkpointDigest']}
        closing_cp={**cp,'sourceSnapshotKey':'changed'} if changed_checkpoint else cp
        evidence=(self.fixture.root,self.fixture.record,closing_cp,incumbent,self.fixture.core)
        with patch('deploom_mcp.goal_current_proof_process.terminal_read_identity',return_value=identity), \
             patch.object(owner,'inspect_checkpoint',return_value=current) as inspect, \
             patch.object(owner,'inspect_checkpoint_evidence',return_value=evidence):
            value=owner.inspect_registered_checkpoint(self.fixture.context)
        return value,inspect

    def test_registered_authoritative_path_disables_synchronous_diagnostic_persistence(self):
        _,inspect=self.registered()
        self.assertFalse(inspect.call_args.kwargs['_diagnostics'])
        self.assertIsNotNone(inspect.call_args.kwargs['_budget'])

    def test_ordinary_current_query_cannot_keep_earlier_incumbent_after_pointer_moves(self):
        value,_=self.registered(incumbent=False)
        self.assertFalse(value['currentWorkspaceVerified']);self.assertFalse(value['isCurrentIncumbent'])
        self.assertTrue(value['checkpointVerifiedHistorical'])

    def test_closing_checkpoint_replacement_rejected_even_with_same_checkpoint_id(self):
        with self.assertRaisesRegex(ValueError,'FINAL_CHECKPOINT_IDENTITY_UNPROVEN'):
            self.registered(changed_checkpoint=True)

    def test_tap_receives_shared_fractional_deadline_without_reset(self):
        self.fixture.context.final_profile = {}
        budget = self.clock.budget()
        self.clock.advance(19.75)
        with patch.object(owner, 'current_environment_matches', return_value=True), \
             patch('deploom_mcp.goal_tap_nyc_execution.physically_current', return_value=True) as tap:
            self.assertTrue(owner.current_state_matches(self.fixture.context, self.fixture.record,
                            self.fixture.root, _budget=budget))
        self.assertEqual(tap.call_args.kwargs['deadline'], 1020)

    def test_tap_late_true_is_rejected(self):
        self.fixture.context.final_profile = {}
        def tap(*a, **kw):
            self.clock.advance(20)
            return True
        with patch.object(owner, 'current_environment_matches', return_value=True), \
             patch('deploom_mcp.goal_tap_nyc_execution.physically_current', side_effect=tap):
            with self.assertRaisesRegex(ValueError, 'CURRENT_PROOF_DEADLINE_EXCEEDED'):
                owner.current_state_matches(self.fixture.context, self.fixture.record,
                            self.fixture.root, _budget=self.clock.budget())


if __name__ == '__main__':
    unittest.main()
