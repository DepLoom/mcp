import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import test_goal_contract
from deploom_mcp.goal_contract import GoalContractError,bind_goal,canonical_digest
from deploom_mcp.goal_report import load_run,summarize_imported

class GoalReportTests(unittest.TestCase):
    def setUp(self):
        f=test_goal_contract.GoalContractTests();f.setUp();self.goal=f.goal
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.runs=self.root/"goal-runs";self.runs.mkdir()
        self.binding=bind_goal(self.goal,f.identity)
        self.current={"ok":True,"binding":self.binding,"lockedTargets":{"express":"4.18.3"}}
        self.record={"schemaVersion":1,"runId":"bounded-1","profileIdentity":{"id":"fixed-profile","sha256":"a"*64},"goal":self.goal,"binding":self.binding,"observations":{}}
    def report(self):return summarize_imported(self.record,self.goal,self.current,self.root)
    def test_all_claimed_pass_never_becomes_controlled_or_full(self):
        for name in self.goal["requiredChecks"]:
            path=self.runs/(name+".log");path.write_text("claimed pass")
            self.record["observations"][name]={"status":"PASS","artifact":{"relativePath":path.name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}}
        self.record["verificationAuthority"]="CONTROLLED_LOCAL_VERIFIER";self.record["passed"]=True
        r=self.report()
        self.assertEqual(r["attainment"],"unknown");self.assertEqual(r["verificationAuthority"],"CLIENT_REPORTED")
        self.assertEqual(r["verification"],"NOT_VERIFIED");self.assertEqual(r["checks"]["required"],4)
        self.assertEqual(len(r["remainingWork"]),4)
    def test_missing_checks_retain_original_denominator(self):
        r=self.report();self.assertEqual(r["checks"]["required"],4)
        self.assertEqual([x["reportedStatus"] for x in r["checks"]["items"]],["NOT_RUN"]*4)
    def test_old_target_not_met_even_when_no_reported_test_failures(self):
        self.current["lockedTargets"]["express"]="4.18.2"
        self.assertEqual(self.report()["attainment"],"not_met")
    def test_failure_with_artifact_is_preserved(self):
        path=self.runs/"failure.log";path.write_text("assertion failed")
        self.record["observations"]["tests"]={"status":"FAIL","artifact":{"relativePath":path.name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}}
        self.assertEqual(self.report()["attainment"],"not_met")
    def test_missing_or_changed_artifact_never_retains_pass(self):
        self.record["observations"]["tests"]={"status":"PASS","artifact":{"relativePath":"missing.log","sha256":"a"*64}}
        tests=next(c for c in self.report()["checks"]["items"] if c["id"]=="tests")
        self.assertEqual(tests["reportedStatus"],"UNKNOWN")
    def test_new_reporting_implementation_does_not_claim_historical_execution(self):
        identity={**self.binding["identity"],"implementationDigest":"c"*64}
        self.current["binding"]=bind_goal(self.goal,identity)
        r=self.report()
        self.assertNotEqual(r["planningImplementationDigest"],r["reportingImplementationDigest"])
        self.assertEqual(r["verificationAuthority"],"CLIENT_REPORTED")
        self.assertFalse(r["fullAcceptance"])
    def test_stale_source_binding_rejected(self):
        self.current["binding"]={**self.binding,"bindingDigest":"b"*64}
        with self.assertRaises(GoalContractError):self.report()
    def test_arbitrary_artifact_paths_rejected(self):
        self.record["observations"]["tests"]={"status":"PASS","artifact":{"relativePath":"../../secret","sha256":"a"*64}}
        with self.assertRaises(GoalContractError):self.report()
    def test_record_lookup_cannot_traverse_or_replace_goal_digest(self):
        (self.runs/"bounded-1.json").write_text(json.dumps(self.record),encoding="utf-8")
        load_run("bounded-1",canonical_digest(self.binding["goal"]),self.root)
        for run_id,digest in [("../elsewhere",canonical_digest(self.binding["goal"])),("bounded-1","f"*64)]:
            with self.assertRaises(GoalContractError):load_run(run_id,digest,self.root)
    def test_additional_check_cannot_change_obligation_denominator(self):
        self.record["observations"]["optional-instead"]={"status":"PASS"}
        with self.assertRaises(GoalContractError):self.report()

if __name__=="__main__":unittest.main()
