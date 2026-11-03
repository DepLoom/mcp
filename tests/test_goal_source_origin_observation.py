import copy,dataclasses,unittest
from types import SimpleNamespace
import test_goal_invocation_adapter as fixtures
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_invocation_adapter import InvocationObservationCollector
from verification_invocation import run_observed,observe_command,defer_workspace_cleanup

class SourceOriginObservationTests(unittest.TestCase):
    setUp=fixtures.InvocationCollectorTests.setUp
    def origin(self):
        return {"schemaVersion":1,"authority":"NON_REUSABLE_CORE_OBSERVATION","status":"SOURCE_ORIGIN_OBSERVED",
            "inputSourceSnapshotKey":"a"*32,"inputManifestKey":"e"*64,"sourcePolicyKey":"f"*64,
            "projectRelative":".","normalizedInputSourceKey":"1"*64,"normalizedPreparedSourceKey":"1"*64,
            "preparedSourceContentKey":"1"*64,"resolvedStateKey":"b"*64,"resolvedLockfilePath":"package-lock.json",
            "resolvedLockfileHash":"c"*64,"toolBuildId":"2"*64,"fullAcceptance":False}
    def collect_origin(self,origin=None,wrong_command_digest=False):
        origin=origin or self.origin();result=dataclasses.replace(self.result,materialized_source_origin=origin)
        collector=InvocationObservationCollector(self.registration,"a"*32,self.fp,engineering_control=True)
        def action():
            defer_workspace_cleanup(lambda:None)
            for i,command in enumerate(self.registration.profile["commands"],1):
                observe_command(i,command,SimpleNamespace(returncode=0,supervision=SimpleNamespace(quality="guaranteed-tree",descendants_remaining=0)),guarded=True,
                    identities={"sourceSnapshotKey":"a"*32,"targetOnlyAssignmentFingerprint":self.fp,"resolvedStateKey":"b"*64,"resolvedLockfileHash":"c"*64,
                        "sourceOriginObservationDigest":"0"*64 if wrong_command_digest else canonical_digest(origin),"preparedSourceContentKey":origin["preparedSourceContentKey"]})
            return result
        collector.invoke(lambda observer:run_observed(observer,self.fp,self.registration.profile["commands"],action))
        return collector,result
    def test_actual_owner_receipt_link_is_required_without_promoting_test_double(self):
        collector,result=self.collect_origin();record=collector.finalize(result)
        self.assertTrue(record["observationComplete"]);self.assertFalse(record["fullAcceptance"])
        self.assertEqual(record["materializedSourceOrigin"],self.origin())
        collector,result=self.collect_origin(wrong_command_digest=True)
        self.assertFalse(collector.finalize(result)["observationComplete"])
    def test_wrong_source_resolved_lock_or_normalized_identity_rejected(self):
        for field in ("inputSourceSnapshotKey","resolvedLockfileHash","resolvedStateKey","normalizedInputSourceKey"):
            origin=self.origin();origin[field]="0"*64
            collector,result=self.collect_origin(origin)
            with self.subTest(field=field),self.assertRaisesRegex(GoalContractError,"SOURCE_ORIGIN_OBSERVATION_MISMATCH"):
                collector.finalize(result)
    def test_origin_changed_after_final_event_cannot_be_reported(self):
        collector,result=self.collect_origin();result.materialized_source_origin["toolBuildId"]="changed"
        with self.assertRaisesRegex(GoalContractError,"SOURCE_ORIGIN_OBSERVATION_MISMATCH"):
            collector.finalize(result)

if __name__=="__main__":unittest.main()
