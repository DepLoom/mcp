"""Deterministic resource-policy tests; no filesystem proof or acceptance claim."""
import unittest
from threading import Event

from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.goal_current_proof_budget import (
    CurrentProofSpec, CurrentProofBudget, parse_current_proof_spec,
    reserved_worker_timeout,
)


class Clock:
    def __init__(self):
        self.mono = 10.0
        self.wall = 1000.0

    def advance(self, seconds):
        self.mono += seconds
        self.wall += seconds

    def budget(self, **kwargs):
        return CurrentProofBudget(CurrentProofSpec(), monotonic=lambda: self.mono,
                                  wall_clock=lambda: self.wall, **kwargs)


class SpecTests(unittest.TestCase):
    def test_missing_spec_is_legacy_and_does_not_mutate_policy(self):
        policy = {'hardPolicyRequired': False}
        before = canonical_digest(policy)
        self.assertIsNone(parse_current_proof_spec(policy))
        self.assertEqual(canonical_digest(policy), before)

    def test_explicit_default_and_host_ceiling(self):
        self.assertEqual(CurrentProofSpec().max_seconds, 20)
        for seconds in (1, 20, 90, 120):
            spec = CurrentProofSpec(seconds)
            self.assertEqual(parse_current_proof_spec({'currentProof': spec.as_policy()}), spec)

    def test_malformed_limits_reject(self):
        for value in (True, False, 0, -1, 121, 20.0, '20', None, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(GoalContractError):
                CurrentProofSpec(value)

    def test_malformed_envelope_rejects_including_null_and_unknown_keys(self):
        valid = CurrentProofSpec().as_policy()
        bad = [None, [], {}, dict(valid, schemaVersion=True), dict(valid, schemaVersion=2),
               dict(valid, scope='APPLY'), dict(valid, timeout=90)]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(GoalContractError):
                parse_current_proof_spec({'currentProof': value})

    def test_spec_changes_existing_policy_digest(self):
        policy = {'hardPolicyRequired': False}
        self.assertNotEqual(canonical_digest(policy), canonical_digest(dict(policy, currentProof=CurrentProofSpec().as_policy())))


class DeadlineTests(unittest.TestCase):
    def test_one_budget_consumed_across_phases_without_roundup(self):
        c = Clock(); b = c.budget()
        c.advance(8); self.assertEqual(b.remaining(), 12)
        c.advance(11.75); self.assertEqual(b.remaining(), .25)
        self.assertEqual(b.wall_deadline(), 1020)
        c.advance(.25)
        with self.assertRaisesRegex(GoalContractError, 'CURRENT_PROOF_DEADLINE_EXCEEDED'): b.remaining()

    def test_original_deadline_clamps_and_never_renews(self):
        c = Clock(); b = c.budget(original_deadline_utc=1003)
        self.assertEqual(b.remaining(), 3)
        c.advance(3)
        with self.assertRaises(GoalContractError): b.remaining()

    def test_wall_clock_rollback_does_not_extend_monotonic_budget(self):
        c = Clock(); b = c.budget(original_deadline_utc=1100)
        c.advance(19); c.wall -= 100
        self.assertEqual(b.remaining(), 1)
        c.mono += 1
        with self.assertRaises(GoalContractError): b.remaining()

    def test_rollback_cannot_extend_shorter_original_utc_allowance(self):
        c = Clock(); b = c.budget(original_deadline_utc=1003)
        c.wall -= 100
        self.assertEqual(b.remaining(), 3)
        c.mono += 3
        with self.assertRaises(GoalContractError): b.remaining()

    def test_wall_clock_forward_jump_shrinks_budget(self):
        c = Clock(); b = c.budget(original_deadline_utc=1010)
        c.wall = 1010
        with self.assertRaises(GoalContractError): b.remaining()

    def test_cancellation_before_and_after_phase(self):
        c = Clock(); event = Event(); b = c.budget(cancelled=event)
        event.set()
        with self.assertRaisesRegex(GoalContractError, 'CURRENT_PROOF_CANCELLED'): b.remaining()
        with self.assertRaises(GoalContractError): c.budget(cancelled=event)
        b = c.budget(); b.cancel()
        with self.assertRaises(GoalContractError): b.wall_deadline()

    def test_invalid_or_expired_original_deadlines_fail_closed(self):
        c = Clock()
        for deadline in (999, 1000, True, '1001', float('nan'), float('inf')):
            with self.subTest(deadline=deadline), self.assertRaises(GoalContractError):
                c.budget(original_deadline_utc=deadline)


class ReservationTests(unittest.TestCase):
    def test_parent_proof_and_return_time_reserved_before_worker(self):
        self.assertEqual(reserved_worker_timeout(CurrentProofSpec(90), now=1000,
                         original_deadlines=[1500, 1400, 1600]), 250)
        self.assertEqual(reserved_worker_timeout(CurrentProofSpec(), now=1000,
                         original_deadlines=[3000]), 600)

    def test_fractional_remainder_is_not_rounded_up(self):
        self.assertEqual(reserved_worker_timeout(CurrentProofSpec(), now=1000,
                         original_deadlines=[1080.25]), .25)

    def test_insufficient_and_invalid_reservations_fail_closed(self):
        for deadlines in ([1080], [1079], [], [float('inf')], [True]):
            with self.subTest(deadlines=deadlines), self.assertRaises(GoalContractError):
                reserved_worker_timeout(CurrentProofSpec(), now=1000, original_deadlines=deadlines)


if __name__ == '__main__':
    unittest.main()
