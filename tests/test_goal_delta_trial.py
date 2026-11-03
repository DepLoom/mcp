"""Real Core source/materialization + synthetic host-policy grants, no npm runs."""
import copy
import dataclasses
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from deploom_mcp.distribution import resolve_core_dir
sys.path.insert(0,str(resolve_core_dir()))
import source_snapshot as core
from deploom_mcp.goal_worker import prepare_goal_plan
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_execution_registry import OPERATION,load_host_registration
import deploom_mcp.goal_delta_trial as trial
import test_goal_lock_scope as scope_fixtures

class HostDeltaTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.project=self.root/"source";self.project.mkdir();self.state=self.root/"host-state";self.state.mkdir()
        subprocess.run([shutil.which("git"),"-C",str(self.project),"init","-q"],check=True,capture_output=True,timeout=5)
        fixture=scope_fixtures.LockInstanceScopeTests();fixture.setUp()
        self.goal={**fixture.goal,"immutablePackages":[]};self.old=fixture.old;self.new=fixture.removal_candidate
        self.before_manifest=json.dumps(self.old["packages"][""],indent=2).encode()+b"\n"
        self.before_lock=json.dumps(self.old,indent=2).encode()+b"\n"
        self.after_manifest=json.dumps(self.new["packages"][""],indent=2).encode()+b"\n"
        self.after_lock=json.dumps(self.new,indent=2).encode()+b"\n"
        (self.project/"package.json").write_bytes(self.before_manifest);(self.project/"package-lock.json").write_bytes(self.before_lock)
        (self.project/"index.js").write_text("module.exports = 'engineering-fixture'\n")
        self.policy={"healthPolicy":"REPORT_ONLY","hardPolicyRequired":False,"transitiveChangePolicy":"EXACT_HOST_ENUMERATED"}
        self.goal={**self.goal,"policyDigest":canonical_digest(self.policy)}
        subprocess.run([shutil.which("git"),"-C",str(self.project),"add","--","package.json","package-lock.json","index.js"],check=True,capture_output=True,timeout=5)
        subprocess.run([shutil.which("git"),"-C",str(self.project),"-c","user.name=DepLoom Engineering Fixture",
            "-c","user.email=fixture@example.invalid","-c","commit.gpgSign=false","commit","-q","-m","engineering baseline"],check=True,capture_output=True,timeout=5)
        self.source_scope=core.verification_source_snapshot_scope(self.project,timeout_seconds=10)
        self.snapshot=self.source_scope.__enter__();self.addCleanup(self.source_scope.__exit__,None,None,None)
        self.binding=prepare_goal_plan(self.project,self.goal)["binding"]
        self.profile={"commands":["__ENGINEERING_CONTROL_NEVER_EXECUTE__"],"requiredChecks":self.goal["requiredChecks"],"projectChecks":"strict","verificationPurpose":"intermediate-candidate"}
        self.entry={"runId":"delta-control","candidateId":"candidate-1","projectPath":str(self.project),"goal":self.goal,"binding":self.binding,"profile":self.profile,"policy":self.policy,
            "authorization":{"operation":OPERATION,"bindingDigest":self.binding["bindingDigest"],"profileDigest":canonical_digest(self.profile),"targetMapDigest":canonical_digest(self.goal["exactTargets"])},"status":"OPEN","deadlineUTC":(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat()}
        self.manifest=self.state/"execution-manifest.json";self.env={"DEPLOOM_MCP_EXECUTION_MANIFEST":str(self.manifest),"DEPLOOM_MCP_STATE_DIR":str(self.state)}
        self.publish()
        with patch.dict(os.environ,self.env):self.registration=load_host_registration("delta-control","candidate-1",self.binding["bindingDigest"],OPERATION,state_root=self.state)
    def publish(self):
        self.manifest.write_text(json.dumps({"schemaVersion":1,"entries":[self.entry]}))
        self.env["DEPLOOM_MCP_EXECUTION_MANIFEST_SHA256"]=hashlib.sha256(self.manifest.read_bytes()).hexdigest()
    def proposal(self,manifest=None,lock=None):
        return trial.propose_exact_delta(self.registration,self.snapshot,manifest or self.after_manifest,lock or self.after_lock)
    def grant(self,proposal,approve=True):
        if approve:
            self.entry["deltaAuthorization"]={"schemaVersion":1,"operation":trial.APPLY_OPERATION,"origin":"ENGINEERING_CONTROL",
                "candidateDigest":proposal.candidate_digest,"deltaDigest":proposal.delta_digest,"bindingDigest":self.binding["bindingDigest"],
                "profileDigest":canonical_digest(self.profile),"sourceSnapshotKey":self.snapshot.key,"transitiveScope":"EXACT_HOST_ENUMERATED",
                "stateRootDigest":canonical_digest({"hostStateRoot":str(self.state.resolve())})}
        self.publish()
        with patch.dict(os.environ,self.env):
            return trial.load_host_delta_grant("delta-control","candidate-1",self.binding["bindingDigest"],trial.APPLY_OPERATION,proposal,state_root=self.state)
    def test_real_core_private_apply_matches_exact_candidate_and_preserves_source(self):
        proposal=self.proposal();result=trial.apply_authorized_private_trial(self.grant(proposal),proposal,self.snapshot)
        self.assertEqual(result["status"],"TRIAL_APPLIED");self.assertEqual(result["authorizationOrigin"],"ENGINEERING_CONTROL")
        self.assertFalse(result["fullAcceptance"]);self.assertFalse(result["projectChecksExecuted"])
        self.assertEqual((self.project/"package.json").read_bytes(),self.before_manifest)
        self.assertEqual((self.project/"package-lock.json").read_bytes(),self.before_lock)
        self.assertEqual(list((self.state/"delta-trial-workspaces").iterdir()),[])
        self.assertEqual(result["exactFileDelta"],["package-lock.json","package.json"])
    def test_verify_only_host_entry_has_no_apply_authority(self):
        with self.assertRaisesRegex(GoalContractError,"AUTHORIZATION_UNAVAILABLE"):self.grant(self.proposal(),approve=False)
    def test_grant_shaped_json_or_unissued_opaque_object_cannot_apply(self):
        proposal=self.proposal()
        for grant in ({"passed":True,"origin":"PREAPPROVED_HOST_POLICY"},trial._DeltaGrant(),None):
            with self.assertRaisesRegex(GoalContractError,"TRUSTED_HOST_DELTA_GRANT_REQUIRED"):
                trial.apply_authorized_private_trial(grant,proposal,self.snapshot)
    def test_grant_cannot_apply_another_candidate_even_if_scope_would_match(self):
        proposal=self.proposal();grant=self.grant(proposal)
        changed=self.proposal(manifest=self.after_manifest+b" ")
        with self.assertRaisesRegex(GoalContractError,"GRANT_CANDIDATE_MISMATCH"):
            trial.apply_authorized_private_trial(grant,changed,self.snapshot)
    def test_original_source_change_after_grant_rejects_stale_baseline(self):
        proposal=self.proposal();grant=self.grant(proposal)
        (self.project/"index.js").write_text("module.exports = 'changed'\n")
        with self.assertRaises(GoalContractError):trial.apply_authorized_private_trial(grant,proposal,self.snapshot)
    def test_changed_shared_immutable_or_unrelated_instance_cannot_be_granted(self):
        for location in ("node_modules/body-parser","node_modules/other/node_modules/body-parser"):
            candidate=copy.deepcopy(self.new);candidate["packages"][location]["integrity"]="mutated"
            with self.assertRaises(GoalContractError):self.proposal(lock=json.dumps(candidate).encode())
    def test_host_listed_transitive_delta_cannot_override_global_protection(self):
        original=self.proposal()
        for field in ("immutablePackages","excludedPackages"):
            self.goal={**self.goal,"immutablePackages":[],"excludedPackages":[],field:["body-parser"]}
            self.binding=prepare_goal_plan(self.project,self.goal)["binding"]
            self.entry["goal"]=self.goal;self.entry["binding"]=self.binding
            self.entry["authorization"]={"operation":OPERATION,"bindingDigest":self.binding["bindingDigest"],"profileDigest":canonical_digest(self.profile),"targetMapDigest":canonical_digest(self.goal["exactTargets"])}
            descriptor=original.descriptor;descriptor["goalDigest"]=self.binding["goalDigest"];descriptor["bindingDigest"]=self.binding["bindingDigest"]
            encoded=json.dumps(descriptor,sort_keys=True,separators=(",", ":")).encode()
            host_listed=dataclasses.replace(original,descriptor_json=encoded,delta_digest=hashlib.sha256(encoded).hexdigest())
            with self.subTest(field=field),self.assertRaisesRegex(GoalContractError,"IMMUTABLE_OR_EXCLUDED_INSTANCE_SCOPE_VIOLATION"):
                self.grant(host_listed)
    def test_extra_manifest_script_is_rejected_before_grant(self):
        value=json.loads(self.after_manifest);value["scripts"]={"test":"echo forged"}
        with self.assertRaisesRegex(GoalContractError,"MANIFEST_SCOPE_VIOLATION"):
            self.proposal(manifest=json.dumps(value).encode())
    def test_after_apply_extra_file_is_detected_by_real_core_inventory(self):
        proposal=self.proposal();grant=self.grant(proposal);original=trial._write_files
        def corrupt(project,proposal):original(project,proposal);(project/"unexpected.js").write_text("unauthorized")
        with patch.object(trial,"_write_files",side_effect=corrupt),self.assertRaisesRegex(GoalContractError,"TRIAL_UNAUTHORIZED_FILE_DELTA"):
            trial.apply_authorized_private_trial(grant,proposal,self.snapshot)
        saved=json.loads(next((self.state/"delta-trial-attempts").glob("*.json")).read_text())
        self.assertEqual(saved["status"],"FAILED");self.assertFalse(saved["fullAcceptance"])
    def test_cleanup_failure_never_publishes_trial_applied(self):
        proposal=self.proposal();grant=self.grant(proposal)
        with patch.object(trial,"_cleanup",side_effect=OSError("injected cleanup error")),self.assertRaises(OSError):
            trial.apply_authorized_private_trial(grant,proposal,self.snapshot)
        saved=json.loads(next((self.state/"delta-trial-attempts").glob("*.json")).read_text())
        self.assertEqual(saved["status"],"CLEANUP_FAILED");self.assertFalse(saved["fullAcceptance"])
    def test_deadline_crossed_during_cleanup_cannot_publish_applied(self):
        proposal=self.proposal();grant=self.grant(proposal);original=trial._cleanup
        deadline=datetime.fromisoformat(self.entry["deadlineUTC"]).timestamp()
        clock_patch=patch.object(trial.time,"time",return_value=deadline+1)
        def cleanup_then_expire(*args):original(*args);clock_patch.start()
        try:
            with patch.object(trial,"_cleanup",side_effect=cleanup_then_expire),self.assertRaisesRegex(GoalContractError,"EXECUTION_DEADLINE_EXCEEDED"):
                trial.apply_authorized_private_trial(grant,proposal,self.snapshot)
        finally:clock_patch.stop()
        saved=json.loads(next((self.state/"delta-trial-attempts").glob("*.json")).read_text())
        self.assertEqual(saved["status"],"DEADLINE_EXCEEDED");self.assertFalse(saved["fullAcceptance"])
    def test_duplicate_attempt_is_blocked_even_after_new_capability_lookup(self):
        proposal=self.proposal();trial.apply_authorized_private_trial(self.grant(proposal),proposal,self.snapshot)
        with self.assertRaisesRegex(GoalContractError,"TRIAL_ALREADY_REGISTERED"):
            trial.apply_authorized_private_trial(self.grant(proposal),proposal,self.snapshot)
    def test_same_manifest_cannot_redirect_ledger_to_another_ancestor_root(self):
        proposal=self.proposal();trial.apply_authorized_private_trial(self.grant(proposal),proposal,self.snapshot)
        with patch.dict(os.environ,self.env),self.assertRaisesRegex(GoalContractError,"DELTA_STATE_ROOT_MISMATCH"):
            trial.load_host_delta_grant("delta-control","candidate-1",self.binding["bindingDigest"],trial.APPLY_OPERATION,proposal,state_root=self.root)
        redirected={**self.env,"DEPLOOM_MCP_STATE_DIR":str(self.root)}
        with patch.dict(os.environ,redirected),self.assertRaisesRegex(GoalContractError,"DELTA_AUTHORIZATION_BINDING_MISMATCH"):
            trial.load_host_delta_grant("delta-control","candidate-1",self.binding["bindingDigest"],trial.APPLY_OPERATION,proposal,state_root=self.root)
        self.assertFalse((self.root/"delta-trial-attempts").exists())
        self.assertEqual(len(list((self.state/"delta-trial-attempts").glob("*.json"))),1)
    def test_explicit_operation_and_exact_host_policy_are_required(self):
        proposal=self.proposal()
        with patch.dict(os.environ,self.env),self.assertRaisesRegex(GoalContractError,"EXPLICIT_DELTA_AUTHORIZATION_REQUIRED"):
            trial.load_host_delta_grant("delta-control","candidate-1",self.binding["bindingDigest"],"",proposal,state_root=self.state)
        self.policy.pop("transitiveChangePolicy");self.entry["policy"]=self.policy
        # The old binding/policy identity cannot be repaired by an approval flag.
        with self.assertRaises(GoalContractError):self.grant(proposal)
    def test_candidate_dataclass_digest_is_recomputed_not_trusted(self):
        proposal=self.proposal();grant=self.grant(proposal)
        mutated=dataclasses.replace(proposal,manifest_bytes=self.after_manifest+b" ")
        with self.assertRaisesRegex(GoalContractError,"STALE_OR_MUTATED_DELTA_PROPOSAL"):
            trial.apply_authorized_private_trial(grant,mutated,self.snapshot)

if __name__=="__main__":unittest.main()
