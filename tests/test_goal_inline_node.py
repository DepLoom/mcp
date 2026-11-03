import hashlib,copy,unittest
import test_goal_check_bindings as fixtures
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_check_bindings import build_inline_node_command,validate_check_bindings,observe_oracles

class InlineHostOracleTests(unittest.TestCase):
    setUp=fixtures.NamedInvocationTests.setUp
    collect=fixtures.NamedInvocationTests.collect
    def inline_profile(self):
        body=b"console.log('pinned body');"
        (self.root/"runtime.cjs").write_bytes(body)
        self.registration.profile["commands"][0]=build_inline_node_command(body)
        row=self.registration.profile["checkBindings"][0]
        row["commandSha256"]=hashlib.sha256(self.registration.profile["commands"][0].encode()).hexdigest()
        row["oracle"]={"mode":"INLINE_NODE_V1","hostPath":"runtime.cjs","sha256":hashlib.sha256(body).hexdigest(),"bodyBytes":len(body)}
        return row
    def test_matching_command_and_catalog_are_bound_but_shell_event_is_insufficient(self):
        self.inline_profile();validate_check_bindings(self.registration.profile,self.registration.goal["requiredChecks"])
        self.assertEqual(observe_oracles(self.registration.profile,self.root,self.registration.project_path),{"build":"MATCH"})
        record=self.collect().finalize(self.result)
        self.assertFalse(record["observationComplete"])
        self.assertEqual(record["namedCheckObservations"]["checks"][0]["oracleExecutedBytes"],"NOT_VERIFIED")
    def test_wrong_command_cannot_borrow_matching_catalog_hash(self):
        self.inline_profile();self.registration.profile["commands"][0]="node --version"
        with self.assertRaisesRegex(GoalContractError,"INLINE_ORACLE_COMMAND_MISMATCH"):
            observe_oracles(self.registration.profile,self.root,self.registration.project_path)
    def test_body_size_utf8_and_exact_integer_constraints(self):
        row=self.inline_profile()
        for size in (True,1.0,0,2049):
            row["oracle"]["bodyBytes"]=size
            with self.assertRaisesRegex(GoalContractError,"INVALID_INLINE_NODE_ORACLE"):
                validate_check_bindings(self.registration.profile,self.registration.goal["requiredChecks"])
        for body in (b"",b"\xff",b"x"*2049):
            with self.assertRaisesRegex(GoalContractError,"INVALID_INLINE_NODE_ORACLE"):build_inline_node_command(body)

if __name__=="__main__":unittest.main()
