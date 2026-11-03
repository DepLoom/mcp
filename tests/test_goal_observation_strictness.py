"""Malformed authoritative event stream and post-closure deadline regressions."""
import copy
import hashlib
import json
import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import test_goal_invocation_adapter as invocation
import test_goal_execution_registry as registry
import test_goal_delta_trial as delta
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_invocation_adapter import InvocationObservationCollector


class StrictEventSetTests(unittest.TestCase):
    setUp=invocation.InvocationCollectorTests.setUp
    collect=invocation.InvocationCollectorTests.collect
    def replay(self, rows):
        collector=InvocationObservationCollector(self.registration,"a"*32,self.fp,engineering_control=True)
        def action(observer):
            for row in rows:observer(row)
            return self.result
        collector.invoke(action)
        return collector.finalize(self.result)
    def test_extra_failed_wrong_digest_or_unexpected_index_cannot_hide_behind_pass(self):
        original=self.collect().events
        for index,digest in ((17,original[0]["commandDigest"]),(1,"0"*64),(1,original[0]["commandDigest"])):
            rows=copy.deepcopy(original)
            rows.insert(-1,{**copy.deepcopy(rows[0]),"commandIndex":index,"commandDigest":digest,"status":"FAIL","returnCode":1})
            for i,row in enumerate(rows,1):row["sequence"]=i
            with self.subTest(index=index,digest=digest),self.assertRaisesRegex(GoalContractError,"INVALID_INVOCATION_COMMAND_SET"):
                self.replay(rows)
    def test_exact_integer_event_versions_sequences_and_indices(self):
        for field,values in (("schemaVersion",(2.0,True)),("sequence",(1.0,True)),("commandIndex",(1.0,True))):
            for value in values:
                rows=copy.deepcopy(self.collect().events);rows[0][field]=value
                with self.subTest(field=field,value=value),self.assertRaises(GoalContractError):self.replay(rows)
    def test_timeout_and_cancellation_require_explicit_false(self):
        for field in ("timedOut","cancelled"):
            for value in (None,0,"",True,"MISSING"):
                rows=copy.deepcopy(self.collect().events)
                if value=="MISSING":rows[0].pop(field)
                else:rows[0][field]=value
                with self.subTest(field=field,value=value):
                    record=self.replay(rows);self.assertFalse(record["observationComplete"])
                    self.assertIn(1,record["commandChecks"]["unconfirmed"])
    def test_malformed_identity_is_diagnostic_not_attribute_error(self):
        for value in (None,[],"claimed"):
            rows=copy.deepcopy(self.collect().events);rows[0]["identities"]=value
            with self.subTest(value=value),self.assertRaisesRegex(GoalContractError,"INVALID_INVOCATION_IDENTITIES"):
                self.replay(rows)


class StrictRegistryVersionTests(unittest.TestCase):
    setUp=registry.HostExecutionRegistryTests.setUp
    publish_fixture=registry.HostExecutionRegistryTests.publish_fixture
    load=registry.HostExecutionRegistryTests.load
    rejects=registry.HostExecutionRegistryTests.rejects
    def test_manifest_version_bool_and_float_rejected(self):
        for value in (True,1.0):
            self.publish_fixture();data=json.loads(self.path.read_text(encoding="utf-8"));data["schemaVersion"]=value
            self.path.write_text(json.dumps(data),encoding="utf-8")
            self.env["DEPLOOM_MCP_EXECUTION_MANIFEST_SHA256"]=hashlib.sha256(self.path.read_bytes()).hexdigest()
            self.rejects("INVALID_EXECUTION_REGISTRY")
    def test_delta_version_bool_and_float_rejected(self):
        for value in (True,1.0):
            self.entry["deltaAuthorization"]={"schemaVersion":value,"operation":"apply-private-candidate","origin":"ENGINEERING_CONTROL",
                **{k:"a"*64 for k in ("candidateDigest","deltaDigest","bindingDigest","profileDigest","stateRootDigest")},
                "sourceSnapshotKey":"s","transitiveScope":"ROOT_TARGETS_ONLY"}
            self.publish_fixture();self.rejects("INVALID_DELTA_AUTHORIZATION")
    def test_unhashable_structured_kind_gets_goal_diagnostic(self):
        from deploom_mcp.goal_check_bindings import validate_check_bindings
        profile={"schemaVersion":2,"commands":["node --version"],"checkBindings":[{"checkId":"source","kind":"core","evidence":[]}]}
        with self.assertRaisesRegex(GoalContractError,"INVALID_CORE_CHECK_BINDING"):
            validate_check_bindings(profile,["source"])


class DeadlineAfterClosureTests(unittest.TestCase):
    setUp=delta.HostDeltaTrialTests.setUp
    publish=delta.HostDeltaTrialTests.publish
    proposal=delta.HostDeltaTrialTests.proposal
    grant=delta.HostDeltaTrialTests.grant
    def test_expired_post_closure_is_not_reported_as_failed_cleanup(self):
        import deploom_mcp.goal_verifier_bridge as bridge
        from deploom_mcp.goal_execution_registry import load_host_registration,OPERATION
        proposal=self.proposal();self.grant(proposal)
        cache=self.state/"owned-cache";cache.mkdir()
        env={**self.env,"DEPLOOM_MCP_OFFLINE_CACHE":str(cache)}
        closed=[]
        @contextmanager
        def healthy_scope(*args,**kwargs):
            yield self.snapshot
            closed.append(True)
        record={"status":"VERIFIER_TRIAL_CLOSED_AWAITING_BASELINE_CLEANUP","fullAcceptance":False}
        (self.state/"delta-trial-attempts").mkdir()
        with patch.dict(os.environ,env):
            registration=load_host_registration("delta-control","candidate-1",self.binding["bindingDigest"],OPERATION)
            with patch.object(bridge,"load_host_registration",return_value=registration),patch.object(bridge,"_core",return_value=SimpleNamespace(verification_source_snapshot_scope=healthy_scope)),patch.object(bridge,"_candidate_bytes",return_value=(self.after_manifest,self.after_lock)),patch.object(bridge,"propose_exact_delta",return_value=proposal),patch.object(bridge,"load_host_delta_grant",return_value=object()),patch.object(bridge,"_apply_authorized_private_trial",return_value=record),patch.object(bridge,"_remaining",side_effect=[15,GoalContractError("EXECUTION_DEADLINE_EXCEEDED")]):
                with self.assertRaisesRegex(GoalContractError,"EXECUTION_DEADLINE_EXCEEDED"):
                    bridge.execute_registered_engineering_candidate("delta-control","candidate-1",self.binding["bindingDigest"],OPERATION)
            saved=json.loads(bridge.trial_ledger_path(registration).read_text(encoding="utf-8"))
        self.assertEqual(closed,[True]);self.assertEqual(saved["status"],"POST_CLOSURE_DEADLINE_EXCEEDED")
        self.assertTrue(saved["allOwnedSnapshotCleanupConfirmed"]);self.assertFalse(saved["fullAcceptance"])

if __name__=="__main__":unittest.main()
