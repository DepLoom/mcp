"""Post-return publication controls with isolated doubles, not project evidence."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
import test_goal_invocation_adapter as fixtures
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_invocation_adapter import InvocationObservationCollector
from verification_invocation import run_observed,observe_command,defer_workspace_cleanup,InvocationObservationError

class CollectorLifecycleTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.InvocationCollectorTests();fixture.setUp()
        self.registration=fixture.registration;self.fp=fixture.fp;self.result=fixture.result
    def action(self,cleanup=lambda:None):
        defer_workspace_cleanup(cleanup)
        for index,command in enumerate(self.registration.profile["commands"],1):
            observe_command(index,command,SimpleNamespace(returncode=0,supervision=SimpleNamespace(quality="guaranteed-tree",descendants_remaining=0)),guarded=True,
                identities={"sourceSnapshotKey":"a"*32,"targetOnlyAssignmentFingerprint":self.fp,"resolvedStateKey":"b"*64,"resolvedLockfileHash":"c"*64})
        return self.result
    def test_preliminary_complete_event_cannot_publish_before_outer_return(self):
        with tempfile.TemporaryDirectory() as raw:
            state=Path(raw);blocked=[]
            class EagerCollector(InvocationObservationCollector):
                def __call__(this,event):
                    super(EagerCollector,this).__call__(event)
                    if event["type"]=="INVOCATION_RESULT_OBSERVED":
                        with self.assertRaises(GoalContractError) as error:this.save_observation(self.result,state)
                        blocked.append(error.exception.code)
            collector=EagerCollector(self.registration,"a"*32,self.fp,engineering_control=True)
            with self.assertRaisesRegex(GoalContractError,"INCOMPLETE_INVOCATION_OBSERVATION"):
                collector.invoke(lambda callback:run_observed(callback,self.fp,self.registration.profile["commands"],self.action))
            self.assertEqual(blocked,["OUTER_INVOCATION_NOT_RETURNED"])
            self.assertEqual(list(state.iterdir()),[])
            self.assertTrue(collector.failed)
            self.assertFalse(collector.returned)
    def test_cleanup_failure_after_preliminary_result_poisoned_collector_cannot_publish(self):
        collector=InvocationObservationCollector(self.registration,"a"*32,self.fp,engineering_control=True)
        def fail():raise OSError("injected cleanup failure")
        with self.assertRaises(InvocationObservationError):
            collector.invoke(lambda callback:run_observed(callback,self.fp,self.registration.profile["commands"],lambda:self.action(fail)))
        self.assertTrue(collector.finished);self.assertFalse(collector.returned);self.assertTrue(collector.failed)
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(GoalContractError):collector.save_observation(self.result,Path(raw))
            self.assertEqual(list(Path(raw).iterdir()),[])
    def test_event_payload_cannot_claim_outer_call_returned(self):
        fixture=fixtures.InvocationCollectorTests();fixture.setUp();prior=fixture.collect()
        collector=InvocationObservationCollector(self.registration,"a"*32,self.fp,engineering_control=True)
        for event in prior.events:
            event=dict(event);event["invocationReturned"]=True
            collector(event)
        with self.assertRaisesRegex(GoalContractError,"OUTER_INVOCATION_NOT_RETURNED"):collector.finalize(self.result)
    def test_shortened_core_command_list_does_not_shrink_registered_profile(self):
        collector=InvocationObservationCollector(self.registration,"a"*32,self.fp,engineering_control=True)
        command=self.registration.profile["commands"][0]
        def action():
            defer_workspace_cleanup(lambda:None)
            observe_command(1,command,SimpleNamespace(returncode=0,supervision=SimpleNamespace(quality="guaranteed-tree",descendants_remaining=0)),guarded=True,
                identities={"sourceSnapshotKey":"a"*32,"targetOnlyAssignmentFingerprint":self.fp,"resolvedStateKey":"b"*64,"resolvedLockfileHash":"c"*64})
            return self.result
        collector.invoke(lambda callback:run_observed(callback,self.fp,[command],action))
        # A missing registered command now invalidates the exact event set;
        # the shorter Core profile cannot publish even an incomplete receipt.
        self.assertEqual(len(self.registration.profile["commands"]),2)
        with self.assertRaisesRegex(GoalContractError,"INVALID_INVOCATION_COMMAND_SET"):
            collector.finalize(self.result)
        self.assertTrue(collector.failed)
    def test_single_use_invocation_cannot_repeat_execution(self):
        fixture=fixtures.InvocationCollectorTests();fixture.setUp();collector=fixture.collect()
        called=[]
        with self.assertRaisesRegex(GoalContractError,"INVOCATION_ALREADY_STARTED"):collector.invoke(lambda callback:called.append(True))
        self.assertEqual(called,[])

if __name__=="__main__":unittest.main()
