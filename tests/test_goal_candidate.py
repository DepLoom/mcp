import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
import test_goal_contract
from test_process_runner import _pid_alive
from process_witness import ProcessWitness
from deploom_mcp.goal_contract import bind_goal,canonical_digest
from deploom_mcp.goal_execution_registry import HostRegistration
from deploom_mcp.goal_candidate import invoke_target_only,assess_target_only_result,verify_goal_candidate


def fixture_registration(root):
    data=test_goal_contract.GoalContractTests();data.setUp()
    binding=bind_goal(data.goal,data.identity)
    return HostRegistration("fixture-run","fixture-candidate",str(root),data.goal,binding,{"commands":["npm run build","npm test"]},{},time.time()+60,"a"*64)

class TargetOnlyBridgeTests(unittest.TestCase):
    def test_strict_bridge_passes_one_target_not_all_held_roots(self):
        registration=fixture_registration(Path.cwd())
        verifier=Mock(return_value=SimpleNamespace(ok=True));factory=Mock(side_effect=lambda **kw:SimpleNamespace(**kw))
        result=invoke_target_only(registration,verifier=verifier,config_factory=factory,proof_cache_dir=Path("test-only-proof"),remaining_seconds=12)
        args,kwargs=verifier.call_args
        self.assertEqual(args[1],{"express":"4.18.3"});self.assertTrue(kwargs["run_project_checks"])
        self.assertEqual(kwargs["config"].project_checks,"strict")
        self.assertEqual(kwargs["config"].verification_purpose,"intermediate-candidate")
        self.assertFalse(kwargs["config"].unbounded_preparation)
        self.assertFalse(kwargs["config"].publish_durable_prepared_artifact)
        self.assertEqual(kwargs["config"].commands,("npm run build","npm test"))
    def test_code_only_observer_is_forwarded_without_becoming_client_input(self):
        registration=fixture_registration(Path.cwd());callback=lambda event:None
        verifier=Mock(return_value=SimpleNamespace(ok=True))
        invoke_target_only(registration,verifier=verifier,config_factory=lambda **kw:SimpleNamespace(**kw),
            proof_cache_dir=Path("test-only-proof"),remaining_seconds=10,invocation_observer=callback)
        self.assertIs(verifier.call_args.kwargs["invocation_observer"],callback)
    def test_actual_core_apply_preserves_43_other_root_declarations(self):
        from deploom_mcp.distribution import resolve_core_dir
        sys.path.insert(0,str(resolve_core_dir()))
        from baseline_constraint_verifier import _apply_assignment
        with tempfile.TemporaryDirectory() as raw:
            project=Path(raw);held={"held-"+str(n):"^1.0.0" for n in range(43)}
            before={"name":"engineering-control","scripts":{"test":"unchanged"},"dependencies":{"express":"^4.17.1",**held}}
            (project/"package.json").write_text(json.dumps(before,indent=2)+"\n",encoding="utf-8")
            self.assertEqual(_apply_assignment(project,{"express":"4.18.3"}),["express"])
            after=json.loads((project/"package.json").read_text())
            self.assertEqual({n:after["dependencies"][n] for n in held},held)
            self.assertEqual(after["scripts"],before["scripts"])
    def test_target_only_fingerprint_is_not_full_assignment_proof(self):
        target={"express":"4.18.3"};full={**target,"held":"1.0.0"}
        result=SimpleNamespace(ok=True,kind="passed",verified_source_snapshot_key="source",verified_assignment_fingerprint=canonical_digest(full))
        assessed=assess_target_only_result(result,target_assignment=target,expected_source_key="source",fingerprint=canonical_digest)
        self.assertEqual(assessed["targetOnlyFingerprintStatus"],"MISSING_OR_MISMATCH");self.assertFalse(assessed["fullAcceptance"])
    def test_ok_true_without_command_lock_held_root_evidence_is_incomplete(self):
        target={"express":"4.18.3"}
        result=SimpleNamespace(ok=True,kind="passed",verified_source_snapshot_key="source",verified_assignment_fingerprint=canonical_digest(target))
        assessed=assess_target_only_result(result,target_assignment=target,expected_source_key="different-source",fingerprint=canonical_digest)
        self.assertFalse(assessed["fullAcceptance"]);self.assertEqual(assessed["sourceSnapshotStatus"],"MISSING_OR_MISMATCH")
        self.assertEqual(assessed["commandReceiptClosure"],"NOT_VERIFIED")
    def test_failure_and_infrastructure_remain_distinct(self):
        for kind,wanted in [("project","CHECK_FAILED"),("infrastructure","UNKNOWN"),("budget","UNKNOWN")]:
            result=SimpleNamespace(ok=False,kind=kind)
            self.assertEqual(assess_target_only_result(result,target_assignment={"x":"1.0.0"},expected_source_key="s",fingerprint=canonical_digest)["invocationOutcome"],wanted)

class CandidateProcessBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_arbitrary_stdout_success_cannot_become_verifier_proof(self):
        from deploom_mcp.process_runner import ProcessRun
        reg=fixture_registration(Path.cwd())
        with patch("deploom_mcp.goal_candidate.load_host_registration",return_value=reg),patch("deploom_mcp.goal_candidate.run_process_async",new_callable=AsyncMock,return_value=ProcessRun(0,'{"passed":true,"code":"PASS"}',"")):
            result=await verify_goal_candidate("fixture-run","fixture-candidate",reg.binding["bindingDigest"],"verify-private-candidate")
            self.assertFalse(result["ok"]);self.assertFalse(result["fullAcceptance"])
    async def test_actual_adapter_cancellation_kills_its_child_tree(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);pidfile=root/"pids.json";checkpoint=root/"verified-checkpoint.txt";checkpoint.write_text("preserve")
            reg=fixture_registration(root)
            code="import json,os,pathlib,subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); p=pathlib.Path(sys.argv[1]); p.with_suffix('.tmp').write_text(json.dumps([os.getpid(),child.pid])); p.with_suffix('.tmp').replace(p); time.sleep(120)"
            with patch("deploom_mcp.goal_candidate.load_host_registration",return_value=reg),patch("deploom_mcp.goal_candidate._worker_argv",return_value=[sys.executable,"-c",code,str(pidfile)]):
                task=asyncio.create_task(verify_goal_candidate("fixture-run","fixture-candidate",reg.binding["bindingDigest"],"verify-private-candidate"))
                try:
                    for _ in range(100):
                        if pidfile.exists():break
                        await asyncio.sleep(.05)
                    self.assertTrue(pidfile.exists(),"controlled child did not start")
                    pids=json.loads(pidfile.read_text())
                    witnesses=[]
                    if os.name == "nt":
                        for pid in pids:
                            handle=ProcessWitness(pid)
                            self.addCleanup(handle.close)
                            self.assertTrue(handle.alive(),"fixture object was not alive before cancellation")
                            witnesses.append(handle)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
                    if os.name == "nt":
                        for handle in witnesses:
                            self.assertFalse(handle.alive(),"bound fixture object still alive")
                    else:
                        for pid in pids:self.assertFalse(_pid_alive(pid),"owned child still alive")
                    self.assertEqual(checkpoint.read_text(),"preserve")
                finally:
                    if not task.done():
                        task.cancel()
                        try:await task
                        except asyncio.CancelledError:pass

if __name__=="__main__":unittest.main()
