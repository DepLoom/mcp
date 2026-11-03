"""Integration keeps the original fresh Core and module-origin denials before model use."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import test_goal_choice as fixture
from deploom_mcp.goal_choice_lineage import build_model
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.distribution import resolve_core_dir


class GoalChoiceIntegrityTests(unittest.TestCase):
    setUp = fixture.CoupledGoalTests.setUp
    save = fixture.CoupledGoalTests.save

    def test_changed_core_bytes_after_prior_model_never_reuses_integrity_pass(self):
        build_model(self.goal, self.current, self.inputs, self.project)
        target = resolve_core_dir().resolve() / 'peer_solver_goal.py'
        actual = Path.read_bytes
        def changed(path):
            body = actual(path)
            return body + b'\n' if path == target else body
        with patch.object(Path, 'read_bytes', changed):
            with self.assertRaisesRegex(GoalContractError, 'CORE_INTEGRITY_MISMATCH'):
                build_model(self.goal, self.current, self.inputs, self.project)

    def test_loaded_foreign_core_origin_still_rejected_after_file_checks(self):
        build_model(self.goal, self.current, self.inputs, self.project)
        module = sys.modules['peer_solver_goal']
        with patch.object(module, '__file__', str(self.project / 'foreign.py')):
            with self.assertRaisesRegex(GoalContractError, 'CHOICE_CORE_MODULE_ORIGIN_MISMATCH'):
                build_model(self.goal, self.current, self.inputs, self.project)


if __name__ == '__main__':
    unittest.main()