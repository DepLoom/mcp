import copy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.goal_check_bindings import validate_check_bindings
import test_goal_invocation_adapter as invocation_tests
import test_goal_execution_registry as registry_tests


def mapped(profile, required):
    return {**profile,"schemaVersion":2,"checkBindings":[
        ({"checkId":name,"kind":"command","commandIndex":i,
          "commandSha256":hashlib.sha256(profile["commands"][i-1].encode()).hexdigest()}
         if i<=len(profile["commands"]) else
         {"checkId":name,"kind":"core","evidence":"SOURCE_LOCK_CONTINUITY"})
        for i,name in enumerate(required,1)]}


class CheckBindingValidationTests(unittest.TestCase):
    def setUp(self):
        self.profile=mapped({"commands":["node build.js","node tests.js"]},["build","test"])
    def rejects(self, profile):
        with self.assertRaises(GoalContractError):validate_check_bindings(profile,["build","test"])
    def test_missing_extra_duplicate_ids_and_ambiguous_command_rejected(self):
        for mutation in (lambda p:p["checkBindings"].pop(),
                         lambda p:p["checkBindings"].append(copy.deepcopy(p["checkBindings"][0])),
                         lambda p:p["checkBindings"][1].update(checkId="build"),
                         lambda p:p["checkBindings"][1].update(checkId="unknown"),
                         lambda p:p["checkBindings"][1].update(commandIndex=1),
                         lambda p:p["checkBindings"][0].update(commandIndex=True),
                         lambda p:p["checkBindings"][0].update(commandIndex=3),
                         lambda p:p["checkBindings"][0].update(commandSha256="0"*64)):
            p=copy.deepcopy(self.profile);mutation(p);self.rejects(p)
    def test_unmapped_command_and_repeated_identical_command_rejected(self):
        p=copy.deepcopy(self.profile);p["commands"].append("node extra.js");self.rejects(p)
        p=copy.deepcopy(self.profile);p["commands"][1]=p["commands"][0]
        p["checkBindings"][1]["commandSha256"]=p["checkBindings"][0]["commandSha256"]
        self.rejects(p)
    def test_core_evidence_cannot_name_arbitrary_shell_output(self):
        self.profile["checkBindings"][0]={"checkId":"build","kind":"core","evidence":"echo PASS"}
        self.rejects(self.profile)
    def test_oracle_path_and_digest_are_pinned_host_relative(self):
        for name in ("../oracle.py","C:/oracle.py","/oracle.py","x\\oracle.py","x//oracle.py","x/./oracle.py"):
            p=copy.deepcopy(self.profile);p["checkBindings"][0]["oracle"]={"hostPath":name,"sha256":"a"*64};self.rejects(p)
    def test_schema_bool_not_version(self):
        self.profile["schemaVersion"]=True;self.rejects(self.profile)


class NamedInvocationTests(unittest.TestCase):
    collect=invocation_tests.InvocationCollectorTests.collect
    def setUp(self):
        invocation_tests.InvocationCollectorTests.setUp(self)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{"DEPLOOM_MCP_STATE_DIR":str(self.root)});self.env.start();self.addCleanup(self.env.stop)
        # Two command obligations plus a structured Core obligation. No command
        # (including an echo) may fulfill the latter.
        self.registration.goal["requiredChecks"]=["build","test","continuity"]
        profile=mapped(self.registration.profile,["build","test"])
        profile["checkBindings"].append({"checkId":"continuity","kind":"core","evidence":"SOURCE_LOCK_CONTINUITY"})
        self.registration.profile.update(profile)
    def test_changed_mapping_after_execution_invalidates_observation(self):
        collector=self.collect()
        self.registration.profile["checkBindings"][0]["checkId"]="changed"
        with self.assertRaisesRegex(GoalContractError,"PROFILE_CHANGED_DURING_INVOCATION"):
            collector.finalize(self.result)
    def test_named_commands_do_not_publish_goal_success_or_core_pass(self):
        record=self.collect().finalize(self.result)
        checks=record["namedCheckObservations"]["checks"]
        self.assertEqual([c["status"] for c in checks],["COMMAND_OBSERVED_PASS","COMMAND_OBSERVED_PASS","NOT_VERIFIED"])
        self.assertFalse(record["namedCheckObservations"]["goalChecksComplete"])
        self.assertFalse(record["fullAcceptance"])
    def test_oracle_mutation_after_command_cannot_be_hidden_by_passed_exit(self):
        oracle=self.root/"oracle.py";oracle.write_bytes(b"expected")
        self.registration.profile["checkBindings"][0]["oracle"]={"hostPath":"oracle.py","sha256":hashlib.sha256(b"expected").hexdigest()}
        collector=self.collect();oracle.write_bytes(b"tampered")
        record=collector.finalize(self.result);item=record["namedCheckObservations"]["checks"][0]
        self.assertEqual(item["status"],"UNKNOWN");self.assertEqual(item["oracleIdentity"],"UNCONFIRMED")
        self.assertTrue(record["observationComplete"])
        self.assertFalse(record["fullAcceptance"])
    def test_missing_oracle_identity_remains_unknown(self):
        self.registration.profile["checkBindings"][0]["oracle"]={"hostPath":"missing.py","sha256":"a"*64}
        with self.assertRaisesRegex(GoalContractError,"HOST_CHECK_ORACLE_UNAVAILABLE_OR_CHANGED"):
            self.collect()
    def test_matched_oracle_identity_does_not_claim_applicability(self):
        oracle=self.root/"oracle.py";oracle.write_bytes(b"expected")
        self.registration.profile["checkBindings"][0]["oracle"]={"hostPath":"oracle.py","sha256":hashlib.sha256(b"expected").hexdigest()}
        item=self.collect().finalize(self.result)["namedCheckObservations"]["checks"][0]
        self.assertEqual(item["oracleIdentity"],"MATCH_BEFORE_AND_AFTER")
        self.assertEqual(item["oracleApplicability"],"NOT_VERIFIED")


class MappedRegistryTests(unittest.TestCase):
    setUp=registry_tests.HostExecutionRegistryTests.setUp
    publish_fixture=registry_tests.HostExecutionRegistryTests.publish_fixture
    load=registry_tests.HostExecutionRegistryTests.load
    rejects=registry_tests.HostExecutionRegistryTests.rejects
    def contextual_entry(self):
        from deploom_mcp.goal_check_bindings import initial_verification_context
        from deploom_mcp.goal_contract import bind_goal
        self.entry["profile"]=mapped(self.profile,self.goal["requiredChecks"])
        self.entry["goal"]["verificationContext"]=initial_verification_context(self.entry["profile"],self.binding["identity"])
        self.entry["binding"]=bind_goal(self.entry["goal"],self.binding["identity"])
        self.binding=self.entry["binding"]
        self.entry["authorization"].update(bindingDigest=self.binding["bindingDigest"],profileDigest=canonical_digest(self.entry["profile"]))
    def test_goal_context_binds_full_profile_and_initial_baseline_identity(self):
        self.contextual_entry();self.publish_fixture();self.load()
        from deploom_mcp.goal_contract import bind_goal
        self.entry["binding"]=bind_goal(self.entry["goal"],{**self.binding["identity"],"sourceDigest":"f"*64})
        self.binding=self.entry["binding"]
        self.entry["authorization"]["bindingDigest"]=self.binding["bindingDigest"]
        self.publish_fixture();self.rejects("GOAL_VERIFICATION_CONTEXT_MISMATCH")
    def test_profile_drift_cannot_keep_old_goal_even_after_host_reauthorizes_profile(self):
        self.contextual_entry()
        self.entry["profile"]["commands"][0]="node --version"
        self.entry["profile"]["checkBindings"][0]["commandSha256"]=hashlib.sha256(b"node --version").hexdigest()
        self.entry["authorization"]["profileDigest"]=canonical_digest(self.entry["profile"])
        self.publish_fixture();self.rejects("GOAL_VERIFICATION_CONTEXT_MISMATCH")
    def test_profile_mapping_is_in_operator_authorization_digest(self):
        self.entry["profile"]=mapped(self.profile,self.goal["requiredChecks"])
        self.entry["authorization"]["profileDigest"]=canonical_digest(self.entry["profile"])
        self.publish_fixture();registration=self.load()
        self.assertEqual(registration.profile["schemaVersion"],2)
        self.entry["profile"]["checkBindings"][0]["commandSha256"]="0"*64
        self.publish_fixture();self.rejects("CHECK_COMMAND_DIGEST_MISMATCH")

if __name__=="__main__":unittest.main()
