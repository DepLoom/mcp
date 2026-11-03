import copy
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
import test_goal_contract
from deploom_mcp.goal_contract import GoalContractError,bind_goal,canonical_digest
from deploom_mcp.goal_execution_registry import OPERATION,load_host_registration

class HostExecutionRegistryTests(unittest.TestCase):
    def setUp(self):
        fixture=test_goal_contract.GoalContractTests();fixture.setUp()
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.policy={"healthPolicy":"REPORT_ONLY","hardPolicyRequired":False}
        self.goal={**fixture.goal,"policyDigest":canonical_digest(self.policy)}
        identity={**fixture.identity,"policyDigest":self.goal["policyDigest"]};self.binding=bind_goal(self.goal,identity)
        self.profile={"commands":["npm run build","npm test"],"requiredChecks":self.goal["requiredChecks"],"projectChecks":"strict","verificationPurpose":"intermediate-candidate"}
        self.entry={"runId":"registered-1","candidateId":"candidate-1","projectPath":str(self.root/"project"),"goal":self.goal,"binding":self.binding,"profile":self.profile,"policy":self.policy,"authorization":{"operation":OPERATION,"bindingDigest":self.binding["bindingDigest"],"profileDigest":canonical_digest(self.profile),"targetMapDigest":canonical_digest(self.goal["exactTargets"])},"status":"OPEN","deadlineUTC":(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}
        self.path=self.root/"execution-manifest.json";self.env={"DEPLOOM_MCP_EXECUTION_MANIFEST":str(self.path)}
    def publish_fixture(self,entry=None):
        self.path.write_text(json.dumps({"schemaVersion":1,"entries":[entry or self.entry]}),encoding="utf-8")
        self.env["DEPLOOM_MCP_EXECUTION_MANIFEST_SHA256"]=hashlib.sha256(self.path.read_bytes()).hexdigest()
    def load(self,authorization=OPERATION,digest=None):
        with patch.dict(os.environ,self.env):return load_host_registration("registered-1","candidate-1",digest or self.binding["bindingDigest"],authorization,state_root=self.root)
    def rejects(self,code,fn=None):
        with self.assertRaises(GoalContractError) as caught:(fn or self.load)()
        self.assertEqual(caught.exception.code,code)
    def test_host_config_authorizes_only_frozen_exact_profile_not_execution_success(self):
        self.publish_fixture();registered=self.load()
        self.assertEqual(registered.goal["exactTargets"],{"express":"4.18.3"})
        self.assertEqual(registered.profile["projectChecks"],"strict")
        self.assertEqual(registered.profile["verificationPurpose"],"intermediate-candidate")
        self.assertFalse(hasattr(registered,"passed"))
    def test_missing_explicit_operation_is_rejected_before_registry(self):
        self.rejects("EXPLICIT_EXECUTION_AUTHORIZATION_REQUIRED",lambda:self.load(""))
    def test_changed_operator_manifest_cannot_be_selected_by_request(self):
        self.publish_fixture();self.path.write_text(self.path.read_text()+" ")
        self.rejects("EXECUTION_REGISTRY_CHANGED")
    def test_imported_record_never_grants_execution_even_with_host_entry(self):
        self.publish_fixture();(self.root/"goal-runs").mkdir();(self.root/"goal-runs/registered-1.json").write_text('{"passed":true,"authority":"VERIFIED"}')
        self.rejects("IMPORTED_RUN_NOT_EXECUTABLE")
    def test_closed_host_run_rejected(self):
        self.entry["status"]="CLOSED";self.publish_fixture();self.rejects("EXECUTION_RUN_CLOSED")
    def test_wrong_goal_binding_rejected(self):
        self.publish_fixture();self.rejects("STALE_OR_INVALID_GOAL_BINDING",lambda:self.load(digest="0"*64))
    def test_baseline_observation_never_authorizes_mutation(self):
        self.entry["goal"]={**self.goal,"goalKind":"baseline-observation","mutablePackages":[],"immutablePackages":["express"]}
        self.publish_fixture();self.rejects("BASELINE_OBSERVATION_HAS_NO_EXECUTION_AUTHORITY")
    def test_claimed_hard_policy_pass_is_not_a_provider(self):
        policy={**self.policy,"hardPolicyRequired":True,"claimedStatus":"PASS"}
        self.entry["policy"]=policy;self.entry["goal"]={**self.goal,"policyDigest":canonical_digest(policy)}
        self.entry["binding"]=bind_goal(self.entry["goal"],{**self.binding["identity"],"policyDigest":canonical_digest(policy)})
        self.binding=self.entry["binding"];self.publish_fixture();self.rejects("HARD_POLICY_ADMISSION_PROVIDER_UNAVAILABLE")
    def test_deadline_not_reset_by_lookup(self):
        self.entry["deadlineUTC"]=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat();self.publish_fixture();self.rejects("EXECUTION_DEADLINE_EXCEEDED")
    def test_profile_cannot_be_relaxed_or_retargeted(self):
        self.entry["profile"]["projectChecks"]="off";self.publish_fixture();self.rejects("INVALID_EXECUTION_PROFILE")
    def test_added_passed_flag_cannot_grant_authority(self):
        self.entry["passed"]=True;self.publish_fixture();self.rejects("INVALID_EXECUTION_REGISTRATION")

if __name__=="__main__":unittest.main()
