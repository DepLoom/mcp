import copy
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from deploom_mcp.distribution import resolve_core_dir
sys.path.insert(0,str(resolve_core_dir()))
from verification_invocation import run_observed,observe_command,observe_reused_profile,defer_workspace_cleanup
from baseline_constraint_verifier import BaselineVerifyResult,assignment_fingerprint
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_invocation_adapter import InvocationObservationCollector
from test_goal_candidate import fixture_registration

class InvocationCollectorTests(unittest.TestCase):
    def setUp(self):
        self.registration=fixture_registration(Path.cwd()/"synthetic-engineering-project")
        self.fp=assignment_fingerprint(self.registration.goal["exactTargets"])
        self.result=BaselineVerifyResult(True,"passed","isolated test double, NOT project evidence",verified_source_snapshot_key="a"*32,
            verified_assignment_fingerprint=self.fp,resolved_state_key="b"*64,resolved_lockfile_hash="c"*64,observed_resolved_hash="d"*64)
    def collect(self,reused=False):
        collector=InvocationObservationCollector(self.registration,"a"*32,self.fp,engineering_control=True)
        def action():
            defer_workspace_cleanup(lambda:None)
            if reused:observe_reused_profile(self.registration.profile["commands"],{})
            else:
                for index,command in enumerate(self.registration.profile["commands"],1):
                    observe_command(index,command,SimpleNamespace(returncode=0,stdout="test control",supervision=SimpleNamespace(quality="guaranteed-tree",descendants_remaining=0)),guarded=True,
                        identities={"sourceSnapshotKey":"a"*32,"targetOnlyAssignmentFingerprint":self.fp,"resolvedStateKey":"b"*64,"resolvedLockfileHash":"c"*64})
            return self.result
        collector.invoke(lambda observer:run_observed(observer,self.fp,self.registration.profile["commands"],action))
        return collector
    def test_complete_test_double_remains_non_project_evidence(self):
        record=self.collect().finalize(self.result)
        self.assertEqual(record["verificationAuthority"],"TEST_DOUBLE_CONTROL_FLOW_ONLY")
        self.assertFalse(record["fullAcceptance"]);self.assertFalse(record["currentWorkspaceVerified"])
        self.assertEqual(record["metadataNecessityCheck"],"UNKNOWN")
    def test_incomplete_final_observation_cannot_be_promoted_even_with_all_commands_passed(self):
        from deploom_mcp.goal_verifier_bridge import _require_complete_observation
        for final_status, before_delete in [("INCOMPLETE_UNKNOWN",True),("RESULT_OBSERVED_COMPLETE",False),("INCOMPLETE_UNKNOWN",False)]:
            with self.subTest(status=final_status,before_delete=before_delete):
                collector=self.collect();collector.events[-1]["status"]=final_status
                collector.events[-1]["beforePrivateWorkspaceDeletion"]=before_delete
                record=collector.finalize(self.result)
                self.assertEqual(record["commandChecks"]["freshConfirmed"],[1,2])
                self.assertFalse(record["observationComplete"])
                with self.assertRaisesRegex(GoalContractError,"CORE_CANDIDATE_VERIFICATION_INCOMPLETE"):
                    _require_complete_observation(self.result,record,self.registration.profile["commands"])

    def test_test_controls_cannot_publish_into_project_observation_store(self):
        collector=self.collect()
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(GoalContractError) as caught:collector.save_observation(self.result,Path(raw))
            self.assertEqual(caught.exception.code,"ENGINEERING_CONTROLS_CANNOT_PUBLISH_PROJECT_OBSERVATIONS")
            self.assertEqual(list(Path(raw).iterdir()),[])
    def test_changed_result_source_or_lock_rejected(self):
        import dataclasses
        for field in ["verified_source_snapshot_key","verified_assignment_fingerprint","resolved_state_key","resolved_lockfile_hash"]:
            with self.subTest(field=field),self.assertRaises(GoalContractError):self.collect().finalize(dataclasses.replace(self.result,**{field:"wrong"}))
    def test_reused_commands_do_not_fulfil_fresh_obligations(self):
        record=self.collect(reused=True).finalize(self.result)
        self.assertEqual(record["commandChecks"]["unconfirmed"],[1,2]);self.assertFalse(record["fullAcceptance"])
    def test_duplicate_or_claimed_authority_event_rejected(self):
        collector=self.collect()
        with self.assertRaises(GoalContractError):collector(copy.deepcopy(collector.events[-1]))
        fresh=InvocationObservationCollector(self.registration,"a"*32,self.fp,engineering_control=True)
        event=copy.deepcopy(collector.events[0]);event["authority"]="GOAL_VERIFIED"
        with self.assertRaises(GoalContractError):fresh(event)

if __name__=="__main__":unittest.main()
