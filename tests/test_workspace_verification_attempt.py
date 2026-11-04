import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from deploom_mcp.workspace_verification_attempt import read_workspace_attempt, run_workspace_attempt


class WorkspaceVerificationAttemptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.output = Path(temp.name) / "receipt.json"
        self.project = Path(temp.name) / "project"
        self.project.mkdir()
        self.plan = {"obligationPlanDigest": "a" * 64, "resolverIdentitySha256": "b" * 64,
                     "resolverToolchain": {}, "approvedLockSha256": "c" * 64,
                     "baselineLockSha256": "d" * 64, "registrySha256": "e" * 64}

    def run_attempt(self, verify):
        return run_workspace_attempt(self.output, self.project, source_key="f" * 32, plan=self.plan,
                                     commands=["npm ls"], install=True, verify=verify)

    def test_unbound_observation_cannot_close_successfully_or_run_twice(self):
        verify = Mock(return_value={"status": "CURRENT_WORKSPACE_PROFILE_OBSERVED"})
        receipt = self.run_attempt(verify)
        self.assertEqual(receipt["status"], "WORKSPACE_VERIFICATION_UNPROVEN")
        self.assertEqual(read_workspace_attempt(self.output)["attemptId"], receipt["verificationAttemptId"])
        with self.assertRaises(ValueError):
            self.run_attempt(verify)
        verify.assert_called_once()
        self.output.write_text(json.dumps({**receipt, "status": "CURRENT_WORKSPACE_PROFILE_OBSERVED"}))
        self.assertEqual(read_workspace_attempt(self.output)["status"], "WORKSPACE_ATTEMPT_UNPROVEN")

    def test_interrupt_keeps_original_attempt_and_does_not_claim_child_exit(self):
        def interrupt(_):
            self.assertEqual(read_workspace_attempt(self.output)["status"], "WORKSPACE_ATTEMPT_INCOMPLETE")
            raise KeyboardInterrupt

        verify = Mock(side_effect=interrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.run_attempt(verify)
        state = read_workspace_attempt(self.output)
        self.assertEqual(state["status"], "WORKSPACE_ATTEMPT_CANCELLING_UNCONFIRMED")
        self.assertEqual(state["processLiveness"], "UNOBSERVED")
        self.assertFalse(self.output.exists())
        with self.assertRaises(FileExistsError):
            self.run_attempt(verify)
        verify.assert_called_once()
