"""No-model D1 A/B safety regressions; NEVER call Codex inference."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from d1_pilot import (
    UsageCollector, Ledger, execute, codex_command, check_limits, check_host_location,
    validate_gate, clone_seed, source_is_safe, DEFAULT_PROMPT, MODEL, EFFORT,
)
from d1_chalk6_scorer import (
    D1_AB_CHALK6_TARGET, EXPECTED_SEED_SHA256, verify_chalk6_frozen,
)
from correctness_oracle import (
    CHALK_TARGET, chalk_static_contract, OracleFailure, CHALK_BASELINE,
)
from test_d1_yellow_policy_demo import synthetic_receipt


def make_preflight_fixture(root: Path):
    seed=root/"frozen"; seed.mkdir()
    wheel=root/"wheel.whl"; wheel.write_bytes(b"public-wheel-synthetic")
    import hashlib
    digest=hashlib.sha256(wheel.read_bytes()).hexdigest()
    d1=synthetic_receipt()
    d1.update(wheelSha256=digest)
    p2={"status":"PASS",
        "checks":dict.fromkeys(("baselinePASS","versionOnlyInstallPASS",
        "versionOnlyRuntimeFAIL","referenceCodePatchPASS",
        "hardcodedSuccessRejected","unsafeControlsFAIL","realPackagedStdioPASS"), True),
        "provenance":{"mcpWheelSha256":digest}}
    good={"status":"PASS","wheelSha256":digest,
        "modelCalls":0,"codexSessions":0,
        "independentRuntime":{"status":"PASS","attribution":"EVALUATOR_ONLY_NOT_AGENT",
            "checks":dict.fromkeys(("lockGeneration","cleanInstall","build",
                                    "test","runtimeMultiInput"),True)}}
    paths=[]
    for label,value in (("p2",p2),("d1",d1),("yellow",good)):
        path=root/(label+".json");path.write_text(json.dumps(value));paths.append(path)
    return seed,wheel,paths


class NativeEventTests(unittest.TestCase):
    def test_unique_ids_and_completed_dedupe(self):
        c=UsageCollector(max_tools=2)
        events=[
            {"type":"thread.started","thread_id":"synthetic-thread-001"},
            {"type":"item.started","item":{"id":"cmd1","type":"command_execution"}},
            {"type":"item.completed","item":{"id":"cmd1","type":"command_execution"}},
            {"type":"item.completed","item":{"id":"mcp1","type":"mcp_tool_call"}},
            {"type":"turn.completed","usage":{"input_tokens":100,
                "cached_input_tokens":20,"output_tokens":10,"reasoning_output_tokens":4}},
        ]
        for e in events:self.assertTrue(c.ingest(e))
        out=c.result()
        self.assertEqual(out["toolCallTotal"],2)
        self.assertEqual(out["toolCalls"],{"command_execution":1,"mcp_tool_call":1})
        self.assertEqual(out["nativeThreadId"],"synthetic-thread-001")
        self.assertTrue(out["eventSchemaSafe"])
        self.assertEqual(out["nativeUsage"]["reasoning_output_tokens"],4)

    def test_missing_usage_and_unknown_thread_fail_closed(self):
        c=UsageCollector()
        c.ingest({"type":"turn.completed","usage":{"input_tokens":99}})
        self.assertIsNone(c.result()["nativeUsage"]["output_tokens"])
        self.assertFalse(c.result()["eventSchemaSafe"])
        self.assertFalse(c.result()["usageComplete"])
        x=UsageCollector()
        self.assertFalse(x.ingest({"type":"thread.started","thread_id":123}))

    def test_tools_41st_item_fails_at_soft_cap_boundary(self):
        c=UsageCollector(max_tools=40)
        for i in range(40):
            self.assertTrue(c.ingest({"type":"item.started",
                "item":{"id":str(i),"type":"command_execution"}}))
        self.assertFalse(c.ingest({"type":"item.started",
            "item":{"id":"41","type":"mcp_tool_call"}}))
        self.assertEqual(c.result()["toolCallTotal"],41)
        self.assertFalse(c.result()["eventSchemaSafe"])

    def test_malformed_jsonl_rejected_without_model(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"stream.py"
            path.write_text("print('not jsonl')\n",encoding="utf-8")
            v=execute([sys.executable,str(path)],Path(td),os.environ.copy(),
                      wall_limit=3,tools_limit=40,max_input=20000,max_output=4000)
            self.assertEqual(v["stopReason"],"MALFORMED_JSONL")
            self.assertFalse(v["exitSuccess"])
            self.assertIsNone(v["nativeBillingUsd"])

    def test_successful_stream_without_native_usage_is_unsafe(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/"stream.py"
            f.write_text("import json\n"
              "print(json.dumps(dict(type='thread.started',thread_id='synthetic-thread-009')))\n"
              "print(json.dumps(dict(type='turn.completed',usage={})))\n",encoding="utf-8")
            v=execute([sys.executable,str(f)],Path(td),os.environ.copy(),
                      3,40,20000,4000)
            self.assertEqual(v["cliExitCode"],0)
            self.assertFalse(v["exitSuccess"])
            self.assertFalse(v["usageComplete"])

    def test_token_overshoot_is_not_a_hard_budget(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/"stream.py"
            f.write_text("import json\n"
              "print(json.dumps(dict(type='thread.started',thread_id='synthetic-thread-999')))\n"
              "print(json.dumps(dict(type='turn.completed',usage={'input_tokens':20001,'output_tokens':3})))\n",encoding="utf-8")
            v=execute([sys.executable,str(f)],Path(td),os.environ.copy(),
                      3,40,20000,4000)
            self.assertTrue(v["tokenOvershoot"])
            self.assertFalse(v["exitSuccess"])
            self.assertIsNone(v["nativeBillingUsd"])

    def test_config_disables_apps_plugins_not_rules(self):
        a=codex_command("codex",Path("public"),MODEL,EFFORT,None,None)
        b=codex_command("codex",Path("public"),MODEL,EFFORT,Path("mcp-python"),Path("mcp-state"))
        self.assertEqual(a[-1],b[-1])
        self.assertIn("chalk 6.0.0",DEFAULT_PROMPT)
        self.assertIn("is-number@7.0.0",DEFAULT_PROMPT)
        self.assertNotIn("--ignore-rules",a+b)
        self.assertNotIn("mcp_servers.deploom", " ".join(a))
        self.assertIn("mcp_servers.deploom", " ".join(b))
        for flag in ("--ignore-user-config","--ephemeral","--strict-config","--json"):
            self.assertIn(flag,a)
        self.assertEqual(a.count("--disable"),3)

    def test_public_registry_403_is_no_go_without_proxy_changes(self):
        import urllib.error
        from d1_pilot import probe_public_network
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.HTTPError(
                       "https://registry.npmjs.org/chalk/6.0.0",
                       403, "Forbidden", {}, None)):
            with self.assertRaisesRegex(RuntimeError,"NPM_REGISTRY_UNAVAILABLE"):
                probe_public_network()

    def test_wrong_model_and_overlarge_budgets_rejected(self):
        from types import SimpleNamespace
        base=dict(wall=600,tools=40,input_cap=20000,output_cap=4000,
                  model=MODEL,effort=EFFORT)
        for arg,value in (("wall",901),("tools",45),("input_cap",25000),
                          ("output_cap",8000),("model","not-terra")):
            trial=dict(base);trial[arg]=value
            with self.subTest(arg=arg),self.assertRaisesRegex(
                    RuntimeError,"LIMIT_OUTSIDE_APPROVED_BOUND|TASK_MODEL_EFFORT_NOT_LOCKED"):
                check_limits(SimpleNamespace(**trial))


class LedgerTests(unittest.TestCase):
    def test_claim_before_any_run_no_duplicate_even_if_interrupted(self):
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/"pair"
            x=Ledger(target,MODEL,EFFORT,EXPECTED_SEED_SHA256)
            self.assertTrue((target/"NO_AUTOMATIC_RETRY.claim").exists())
            self.assertNotEqual(x.doc["sessions"]["A"]["sessionId"],
                                x.doc["sessions"]["B"]["sessionId"])
            x.mark("A","PREPARED")
            x.mark("A","RUNNING")
            stored=json.loads((target/"session-ledger.json").read_text())
            self.assertEqual(stored["sessions"]["A"]["state"],"RUNNING")
            self.assertIsNone(stored["sessions"]["A"]["nativeThreadId"])
            x.record_native_thread("A","synthetic-thread-actual")
            stored=json.loads((target/"session-ledger.json").read_text())
            self.assertEqual(stored["sessions"]["A"]["nativeThreadId"],
                             "synthetic-thread-actual")
            with self.assertRaisesRegex(RuntimeError,"NATIVE_THREAD_ID_CONFLICT"):
                x.record_native_thread("A","synthetic-thread-changed")
            with self.assertRaisesRegex(RuntimeError,"RUN_LEDGER_EXISTS_NO_RETRY"):
                Ledger(target,MODEL,EFFORT,EXPECTED_SEED_SHA256)
            with self.assertRaisesRegex(RuntimeError,"UNSAFE_LEDGER_STATE_TRANSITION"):
                x.mark("A","PREPARED")

    def test_no_agent_outside_authorized_temp_with_parent_rules(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            seed=root/"seed";seed.mkdir()
            (root/"AGENTS.md").write_text("Follow applicable user instructions.")
            with self.assertRaisesRegex(RuntimeError,"APPLICABLE_PARENT_PROJECT_RULES"):
                check_host_location(seed,root/"pair")


class NoLeakTests(unittest.TestCase):
    def test_seed_clone_does_not_copy_ignored_reference_or_tools(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            seed=root/"seed"
            seed.mkdir()
            # Only the fixed public eight files may enter the workspace.
            for rel in (".npmrc","README.md","build.cjs","test.cjs",
                        "src/index.cjs","src/format.cjs","package.json",
                        "package-lock.json"):
                p=seed/rel
                p.parent.mkdir(parents=True,exist_ok=True)
                p.write_text(rel,encoding="utf-8")
            hidden=seed/"dist"
            hidden.mkdir()
            (hidden/"reference.cjs").write_text("synthetic reference not for agent")
            (seed/"node_modules").mkdir()
            (seed/"node_modules"/"oracle.txt").write_text("synthetic oracle")
            dest=root/"agent"
            with patch("d1_pilot.source_is_safe",return_value=True):
                clone_seed(seed,dest,EXPECTED_SEED_SHA256)
            self.assertFalse((dest/"dist").exists())
            self.assertFalse((dest/"node_modules").exists())
            self.assertFalse((dest/"reference.cjs").exists())
            self.assertEqual(
                sorted(str(p.relative_to(dest)).replace("\\","/")
                       for p in dest.rglob("*") if p.is_file()),
                sorted((".npmrc","README.md","build.cjs","test.cjs",
                        "src/index.cjs","src/format.cjs","package.json",
                        "package-lock.json")))

    def test_auth_stays_outside_benchmark_and_user_rules_are_same(self):
        from d1_pilot import private_home, validate_private_auth_source
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            origin=root/"auth-source";origin.mkdir()
            (origin/"auth.json").write_text('{"test":"SYNTHETIC_NOT_REAL"}')
            (origin/"AGENTS.md").write_text("Synthetic user restriction")
            (origin/"rules").mkdir()
            (origin/"rules"/"safety.rules").write_text("Synthetic tool rule")
            desc=validate_private_auth_source(origin)
            priv=root/"private-temp";priv.mkdir()
            one,d1=private_home(priv,desc)
            two,d2=private_home(priv,desc)
            self.assertEqual(d1,d2)
            self.assertNotEqual(one,two)
            out=root/"benchmark-output";out.mkdir()
            self.assertFalse(any(p.name=="auth.json" for p in out.rglob("*")))
            self.assertEqual((one/"AGENTS.md").read_text(),"Synthetic user restriction")
            self.assertEqual((two/"rules"/"safety.rules").read_text(),"Synthetic tool rule")


class CheckerTests(unittest.TestCase):
    def test_separate_objective6_requires_independent_runtime_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            seed,wheel,(p2,d1,yellow)=make_preflight_fixture(Path(td))
            with patch("d1_pilot.source_is_safe",return_value=True):
                p=validate_gate(seed,wheel,p2,d1,yellow)
                self.assertEqual(p["chosenTask"]["exactTargets"],{"chalk":"6.0.0"})
                self.assertEqual(p["originalP2"]["policyStatus"],"BLOCKED_1_OF_2")
                self.assertEqual(p["chosenTask"]["policyStatus"],"FEASIBLE_2_OF_2")
                bad=json.loads(yellow.read_text());bad["independentRuntime"]["status"]="FAIL"
                yellow.write_text(json.dumps(bad))
                with self.assertRaisesRegex(RuntimeError,"CHALK6_INDEPENDENT_RUNTIME"):
                    validate_gate(seed,wheel,p2,d1,yellow)

    def test_objective_mismatch_fails_without_model(self):
        with tempfile.TemporaryDirectory() as td:
            seed,wheel,(p2,d1,yellow)=make_preflight_fixture(Path(td))
            data=json.loads(d1.read_text())
            next(r for r in data["requests"] if r["scenarioId"] ==
                 "SEPARATE_CURRENT_POLICY_MIN_6_0_0")["chalkTarget"]="5.3.0"
            d1.write_text(json.dumps(data))
            with patch("d1_pilot.source_is_safe",return_value=True):
                with self.assertRaisesRegex(RuntimeError,"D1_SEPARATE_CHALK6_POLICY"):
                    validate_gate(seed,wheel,p2,d1,yellow)

    def test_original_p2_oracle_still_requires_chalk5(self):
        # Synthetic source/lock; no npm/network. Existing P2 target remains 5.
        from test_correctness_oracle import PublicChalkBreakingApiPolicyTests
        case=PublicChalkBreakingApiPolicyTests()
        case.setUp()
        try:
            with self.assertRaisesRegex(OracleFailure,"CHALK_TARGET_OR_EXCLUSION_VIOLATION"):
                chalk_static_contract(case.base,case.candidate,
                                      target_dependencies=D1_AB_CHALK6_TARGET)
            self.assertEqual(CHALK_TARGET,{"chalk":"5.3.0","is-number":"7.0.0"})
            self.assertEqual(D1_AB_CHALK6_TARGET["is-number"],"7.0.0")
        finally:
            case.doCleanups()


class OwnedTreeTests(unittest.TestCase):
    def test_watchdog_terminates_only_owned_descendants(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            pidfile=root/"owned-child.pid"
            # Child deliberately ignores SIGTERM on POSIX. Windows Job Object
            # must still terminate child, not just the Python parent.
            child=root/"child.py"
            child.write_text("import os,sys,time,signal\n"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN) if os.name!='nt' else None\n"
                "open(sys.argv[1],'w').write(str(os.getpid()))\n"
                "time.sleep(30)\n",encoding="utf-8")
            parent=root/"parent.py"
            parent.write_text(
                "import json,subprocess,sys,time\n"
                "subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]],"
                "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                "print(json.dumps({'type':'thread.started','thread_id':'synthetic-thread-888'}),flush=True)\n"
                "time.sleep(30)\n",encoding="utf-8")
            foreign=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"],
                                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                out=execute([sys.executable,str(parent),str(child),str(pidfile)],
                             root,os.environ.copy(),1,40,20000,4000)
                self.assertEqual(out["stopReason"],"WALL_WATCHDOG")
                self.assertFalse(out["exitSuccess"])
                self.assertIsNone(out["nativeBillingUsd"])
                self.assertIsNone(foreign.poll(),"Watchdog killed foreign process")
                if pidfile.is_file():
                    pid=int(pidfile.read_text())
                    def alive():
                        if os.name=="nt":
                            import ctypes
                            k=ctypes.windll.kernel32
                            k.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
                            k.OpenProcess.restype=ctypes.c_void_p
                            h=k.OpenProcess(0x1000,0,pid)
                            if not h:return False
                            val=ctypes.c_ulong(0)
                            ok=k.GetExitCodeProcess(h,ctypes.byref(val))
                            k.CloseHandle(ctypes.c_void_p(h))
                            return bool(ok and val.value==259)
                        try:os.kill(pid,0)
                        except ProcessLookupError:return False
                        # A reaped child might briefly be a zombie; not running.
                        try:
                            state=Path(f"/proc/{pid}/stat").read_text().split()[2]
                            return state not in ("Z","X")
                        except OSError:
                            return True
                    for _ in range(30):
                        if not alive():break
                        time.sleep(0.1)
                    self.assertFalse(alive(),"Owned descendant survived watchdog")
            finally:
                foreign.terminate()
                foreign.wait(timeout=4)


if __name__=="__main__":
    unittest.main()
