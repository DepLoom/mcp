import copy
import unittest
from deploom_mcp.goal_contract import (GoalContractError, assert_current_binding, bind_goal,
    validate_goal, validate_manifest_change, validate_root_lock_versions)

class GoalContractTests(unittest.TestCase):
    def setUp(self):
        self.goal={"schemaVersion":1,"goalKind":"functional-maintenance","healthPolicy":"REPORT_ONLY","goalId":"maintenance","exactTargets":{"express":"4.18.3"},
            "mutablePackages":["express"],"immutablePackages":["body-parser"],"excludedPackages":[],
            "requiredChecks":["install","build","tests","runtime"],"acceptPartial":False,
            "allowSourceRepair":False,"policyDigest":"a"*64}
        self.identity={k:"a"*64 for k in ["sourceDigest","manifestDigest","lockDigest","toolchainDigest","policyDigest","implementationDigest"]}
        self.before={"name":"real-project","scripts":{"test":"unchanged"},"dependencies":{"express":"^4.17.1","body-parser":"^1.19.0"}}
        self.after=copy.deepcopy(self.before);self.after["dependencies"]["express"]="4.18.3"
    def rejects(self,code,fn,*args):
        with self.assertRaises(GoalContractError) as caught:fn(*args)
        self.assertEqual(caught.exception.code,code)
    def test_baseline_observation_holds_all_targets_immutable(self):
        g={**self.goal,"goalKind":"baseline-observation","exactTargets":{"express":"4.18.2"},"mutablePackages":[],"immutablePackages":["express","body-parser"]}
        validate_goal(g)
        validate_manifest_change(self.before,self.before,g)
        self.rejects("BASELINE_OBSERVATION_CHANGED_MANIFEST",validate_manifest_change,self.before,self.after,g)
    def test_baseline_observation_never_authorizes_a_mutable_target(self):
        g={**self.goal,"goalKind":"baseline-observation"}
        self.rejects("BASELINE_OBSERVATION_MUST_BE_IMMUTABLE",validate_goal,g)
    def test_baseline_observation_requires_matching_original_lock(self):
        g={**self.goal,"goalKind":"baseline-observation","mutablePackages":[],"immutablePackages":["express","body-parser"]}
        lock={"lockfileVersion":2,"packages":{"":self.before,"node_modules/express":{"version":"4.18.2"},"node_modules/body-parser":{"version":"1.20.2"}}}
        self.rejects("BASELINE_OBJECTIVE_MISMATCH",validate_root_lock_versions,lock,lock,g)
    def test_exact_plan_never_grants_verification(self):
        b=bind_goal(self.goal,self.identity)
        self.assertEqual(b["authority"],"PLANNING_ONLY")
        self.assertEqual(set(b["remainingChecks"]),set(self.goal["requiredChecks"]))
        assert_current_binding(b,self.goal,self.identity)
    def test_bound_identity_rejects_source_lock_toolchain_and_implementation_drift(self):
        b=bind_goal(self.goal,self.identity)
        for key in ["sourceDigest","lockDigest","toolchainDigest","implementationDigest"]:
            with self.subTest(key=key):
                changed={**self.identity,key:"b"*64}
                self.rejects("STALE_OR_INVALID_GOAL_BINDING",assert_current_binding,b,self.goal,changed)
    def test_policy_cannot_be_replaced_by_green_label(self):
        changed={**self.identity,"policyDigest":"b"*64}
        self.rejects("POLICY_BINDING_MISMATCH",bind_goal,self.goal,changed)
    def test_passed_boolean_is_not_authority(self):
        b={**bind_goal(self.goal,self.identity),"passed":True}
        self.rejects("STALE_OR_INVALID_GOAL_BINDING",assert_current_binding,b,self.goal,self.identity)
    def test_required_check_cannot_be_dropped_after_plan(self):
        b=bind_goal(self.goal,self.identity);changed={**self.goal,"requiredChecks":["install"]}
        self.rejects("STALE_OR_INVALID_GOAL_BINDING",assert_current_binding,b,changed,self.identity)
    def test_scope_cannot_target_immutable_or_excluded(self):
        for key in ["immutablePackages","excludedPackages"]:
            g={**self.goal,key:["express"]}
            self.rejects("TARGET_NOT_AUTHORIZED",validate_goal,g)
    def test_partial_repair_and_unknown_fields_fail_closed(self):
        for key in ["acceptPartial","allowSourceRepair"]:
            self.rejects("UNSUPPORTED_GOAL_MODE",validate_goal,{**self.goal,key:True})
        self.rejects("INVALID_GOAL",validate_goal,{**self.goal,"passed":True})
    def test_nonexact_and_unbounded_mutable_scope_rejected(self):
        self.rejects("INVALID_GOAL",validate_goal,{**self.goal,"exactTargets":{"express":"^4.18.3"}})
        self.rejects("EXACT_SCOPE_MISMATCH",validate_goal,{**self.goal,"mutablePackages":["express","other"]})
    def test_manifest_allows_only_authorized_exact_target(self):
        validate_manifest_change(self.before,self.after,self.goal)
        self.assertEqual(self.before["dependencies"]["express"],"^4.17.1")
    def test_manifest_rejects_script_weakening_held_root_and_wrong_target(self):
        for kind in ["script","held","target"]:
            changed=copy.deepcopy(self.after)
            if kind=="script":changed["scripts"]["test"]="echo pass"
            elif kind=="held":changed["dependencies"]["body-parser"]="2.0.0"
            else:changed["dependencies"]["express"]="4.18.2"
            self.rejects("MANIFEST_SCOPE_VIOLATION",validate_manifest_change,self.before,changed,self.goal)
    def test_ambiguous_root_is_unsupported(self):
        before={**self.before,"devDependencies":{"express":"4.18.2"}}
        self.rejects("AMBIGUOUS_ROOT_DECLARATION",validate_manifest_change,before,self.after,self.goal)
    def test_lock_v2_requires_exact_integer_on_both_sides(self):
        old={"lockfileVersion":2,"packages":{"":self.before,"node_modules/express":{"version":"4.18.2"},"node_modules/body-parser":{"version":"1.20.2"}}}
        new=copy.deepcopy(old);new["packages"][""]=self.after;new["packages"]["node_modules/express"]["version"]="4.18.3"
        for index in (0,1):
            for version in (2.0,True,"2",None):
                pair=[copy.deepcopy(old),copy.deepcopy(new)];pair[index]["lockfileVersion"]=version
                self.rejects("UNSUPPORTED_LOCK_FORMAT",validate_root_lock_versions,*pair,self.goal)
    def test_root_lock_checks_exact_and_held_versions_not_only_http(self):
        old={"lockfileVersion":2,"packages":{"":self.before,"node_modules/express":{"version":"4.18.2"},"node_modules/body-parser":{"version":"1.20.2"}}}
        new=copy.deepcopy(old);new["packages"][""]=self.after;new["packages"]["node_modules/express"]["version"]="4.18.3"
        validate_root_lock_versions(old,new,self.goal)
        for name in ["express","body-parser"]:
            changed=copy.deepcopy(new);changed["packages"]["node_modules/"+name]["version"]="0.0.0"
            self.rejects("ROOT_LOCK_SCOPE_VIOLATION",validate_root_lock_versions,old,changed,self.goal)

if __name__=="__main__":unittest.main()
