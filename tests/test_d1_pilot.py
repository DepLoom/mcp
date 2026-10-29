import sys, json, unittest, tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from d1_pilot import UsageCollector, dry_run, price_usd, codex_command
from d1_mcp_value_preflight import evaluate

class PilotContractTests(unittest.TestCase):
    def test_native_usage_missing_does_not_become_zero(self):
        c=UsageCollector()
        self.assertIsNone(c.result()["nativeUsage"]["input_tokens"])
        c.ingest({"type":"turn.completed","usage":{"input_tokens":1000,"output_tokens":200}})
        self.assertIsNone(c.result()["nativeUsage"]["cached_input_tokens"])
        self.assertIsNone(c.result()["nativeUsage"]["reasoning_output_tokens"])
        self.assertIsNone(price_usd(c.result()["nativeUsage"],(1,0.1,2)))
    def test_fake_dry_run_is_not_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            result=dry_run(Path(td)/"fake")
            self.assertEqual("FAKE_DRY_RUN_NO_MODEL_CALLS",result["mode"])
            self.assertEqual("SYNTHETIC_NOT_EVIDENCE",result["truth"])
            self.assertFalse(result["cliInvoked"])
    def test_mcp_difference_is_config_only(self):
        p=Path("seed")
        a=codex_command("codex",p,"FAKE","medium",None,None)
        b=codex_command("codex",p,"FAKE","medium",Path("venv/python"),Path("mcp-state"))
        self.assertEqual(a[-1],b[-1])
        self.assertNotIn("mcp_servers.deploom",str(a))
        self.assertIn("mcp_servers.deploom",str(b))
    def test_specific_actionability_gate(self):
        core={"ok":True,"authority":"PLANNING_ONLY","verificationStatus":"NOT_VERIFIED",
              "draftStatus":"DRAFT_READY","chalkStatus":"PROPOSED","chalkTarget":"5.3.0",
              "companionStatus":"NO_CHANGE","companionTarget":None}
        self.assertEqual("GO",evaluate({"responses":[core]})["gate"])
        self.assertEqual("NO_EXACT_CHALK_5_3_0_TARGET",
            evaluate({"responses":[dict(core,chalkTarget="5.6.2")]})["productGap"])
        self.assertEqual("NO_GO",evaluate({"responses":[dict(core,companionStatus="PROPOSED")]} )["gate"])

if __name__=="__main__":unittest.main()
