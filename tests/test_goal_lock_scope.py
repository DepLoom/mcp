import copy
import unittest
import test_goal_contract
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_lock_scope import validate_npm_lock_scope

class LockInstanceScopeTests(unittest.TestCase):
    def setUp(self):
        fixture=test_goal_contract.GoalContractTests();fixture.setUp();self.goal=fixture.goal
        self.old={"name":"project","version":"1.0.0","lockfileVersion":2,"requires":True,"packages":{
            "":{"name":"project","version":"1.0.0","dependencies":{"express":"^4.17.1","body-parser":"^1.19.0","other":"1.0.0"}},
            "node_modules/express":{"version":"4.18.2","integrity":"old-express","dependencies":{"body-parser":"1.20.1"}},
            "node_modules/express/node_modules/body-parser":{"version":"1.20.1","integrity":"nested-old"},
            "node_modules/body-parser":{"version":"1.20.2","integrity":"held-root"},
            "node_modules/other":{"version":"1.0.0","dependencies":{"body-parser":"1.20.0"}},
            "node_modules/other/node_modules/body-parser":{"version":"1.20.0","integrity":"unrelated-nested"}},
            "dependencies":{"express":{"version":"4.18.2","integrity":"old-express","requires":{"body-parser":"1.20.1"},"dependencies":{"body-parser":{"version":"1.20.1","integrity":"nested-old"}}},"body-parser":{"version":"1.20.2","integrity":"held-root"},"other":{"version":"1.0.0","requires":{"body-parser":"1.20.0"},"dependencies":{"body-parser":{"version":"1.20.0","integrity":"unrelated-nested"}}}}}
        self.new=copy.deepcopy(self.old)
        self.new["packages"][""]["dependencies"]["express"]="4.18.3"
        self.new["packages"]["node_modules/express"]={"version":"4.18.3","integrity":"new-express","dependencies":{"body-parser":"1.20.2"}}
        del self.new["packages"]["node_modules/express/node_modules/body-parser"]
        self.new["dependencies"]["express"]={"version":"4.18.3","integrity":"new-express","requires":{"body-parser":"1.20.2"}}
        self.removal_candidate=copy.deepcopy(self.new)
        # Default negatives preserve the protected nested instance, so each
        # additional violation is tested rather than masked by its removal.
        self.new["packages"]["node_modules/express"]["dependencies"]=copy.deepcopy(self.old["packages"]["node_modules/express"]["dependencies"])
        self.new["packages"]["node_modules/express/node_modules/body-parser"]=copy.deepcopy(self.old["packages"]["node_modules/express/node_modules/body-parser"])
        self.new["dependencies"]["express"]["requires"]=copy.deepcopy(self.old["dependencies"]["express"]["requires"])
        self.new["dependencies"]["express"]["dependencies"]=copy.deepcopy(self.old["dependencies"]["express"]["dependencies"])
    def root_held_goal(self):return {**self.goal,"immutablePackages":[]}
    def reject(self,code):
        with self.assertRaises(GoalContractError) as caught:validate_npm_lock_scope(self.old,self.new,self.goal)
        self.assertEqual(caught.exception.code,code)
    def test_original_global_immutable_contract_rejects_nested_removal(self):
        self.new=self.removal_candidate
        self.reject("IMMUTABLE_OR_EXCLUDED_INSTANCE_SCOPE_VIOLATION")
    def test_target_owned_nested_removal_can_reuse_unprotected_unchanged_held_root(self):
        root_held_goal={**self.goal,"immutablePackages":[]}
        result=validate_npm_lock_scope(self.old,self.removal_candidate,root_held_goal)
        self.assertEqual(result["status"],"DECLARED_LOCK_CLOSURE_ONLY")
        self.assertFalse(result["necessityVerified"])
        self.assertEqual(result["compatibility"],"NOT_VERIFIED")
        self.assertEqual(self.old["packages"]["node_modules/express"]["version"],"4.18.2")
    def test_fabricated_target_edge_never_proves_published_necessity(self):
        name="not-an-express-dependency"
        self.new["packages"]["node_modules/express"]["dependencies"][name]="1.0.0"
        self.new["packages"]["node_modules/"+name]={"version":"1.0.0","integrity":"fabricated"}
        self.new["dependencies"]["express"]["requires"][name]="1.0.0"
        self.new["dependencies"][name]={"version":"1.0.0","integrity":"fabricated"}
        result=validate_npm_lock_scope(self.old,self.new,self.goal)
        self.assertFalse(result["necessityVerified"])
        self.assertIn("PUBLISHED_METADATA_FOR_CHANGED_INSTANCES",result["missingEvidence"])
    def test_transitive_immutable_and_excluded_version_changes_reject(self):
        for field in ("immutablePackages","excludedPackages"):
            candidate=copy.deepcopy(self.old)
            candidate["packages"][""]["dependencies"]["express"]="4.18.3"
            candidate["packages"]["node_modules/express"]["version"]="4.18.3"
            candidate["packages"]["node_modules/express/node_modules/body-parser"]["version"]="9.0.0"
            goal={**self.goal,"immutablePackages":[],"excludedPackages":[],field:["body-parser"]}
            with self.subTest(field=field),self.assertRaises(GoalContractError) as caught:validate_npm_lock_scope(self.old,candidate,goal)
            self.assertEqual(caught.exception.code,"IMMUTABLE_OR_EXCLUDED_INSTANCE_SCOPE_VIOLATION")
    def test_scoped_nested_protected_version_rejected_in_both_lock_views(self):
        name="@scope/locked";location="node_modules/express/node_modules/"+name
        for view in ("packages","dependencies"):
            old,new=copy.deepcopy(self.old),copy.deepcopy(self.new)
            for lock in (old,new):
                lock["packages"]["node_modules/express"]["dependencies"][name]="^1.0.0"
                lock["packages"][location]={"version":"1.0.0"}
                lock["dependencies"]["express"]["requires"][name]="^1.0.0"
                lock["dependencies"]["express"].setdefault("dependencies",{})[name]={"version":"1.0.0"}
            if view=="packages":new["packages"][location]["version"]="1.1.0"
            else:new["dependencies"]["express"]["dependencies"][name]["version"]="1.1.0"
            goal={**self.goal,"immutablePackages":[name]}
            with self.subTest(view=view),self.assertRaises(GoalContractError) as caught:validate_npm_lock_scope(old,new,goal)
            self.assertEqual(caught.exception.code,"IMMUTABLE_OR_EXCLUDED_INSTANCE_SCOPE_VIOLATION")
    def test_scoped_nested_protected_identity_and_alias_fail_closed(self):
        from deploom_mcp.goal_lock_scope import _instance_identity
        location="node_modules/root/node_modules/@scope/protected"
        self.assertEqual(_instance_identity(location,{"version":"1.0.0"}),"@scope/protected")
        for path,record in ((location,{"name":"other","version":"1.0.0"}),
                            ("node_modules/root/extra/protected",{"version":"1.0.0"}),
                            (location,{"version":"npm:other@1.0.0"}),
                            ("node_modules/root/./node_modules/protected",{"version":"1.0.0"}),
                            ("node_modules//protected",{"version":"1.0.0"})):
            with self.assertRaises(GoalContractError):_instance_identity(path,record)
    def test_same_name_outside_target_closure_cannot_change(self):
        self.new["packages"]["node_modules/other/node_modules/body-parser"]["version"]="9.9.9"
        self.reject("IMMUTABLE_OR_EXCLUDED_INSTANCE_SCOPE_VIOLATION")
    def test_held_shared_integrity_cannot_change_even_at_same_version(self):
        self.new["packages"]["node_modules/body-parser"]["integrity"]="replaced"
        self.reject("IMMUTABLE_OR_EXCLUDED_INSTANCE_SCOPE_VIOLATION")
    def test_legacy_duplicate_tree_cannot_hide_unrelated_change(self):
        self.new["dependencies"]["other"]["dependencies"]["body-parser"]["integrity"]="changed"
        self.reject("IMMUTABLE_OR_EXCLUDED_INSTANCE_SCOPE_VIOLATION")
    def test_unrelated_instance_insertion_rejected(self):
        self.new["packages"]["node_modules/unrelated"]={"version":"1.0.0"}
        self.reject("TRANSITIVE_SCOPE_VIOLATION")
    def test_metadata_change_is_not_dependency_closure(self):
        self.new["version"]="2.0.0";self.reject("LOCK_METADATA_SCOPE_VIOLATION")
    def test_lock_root_manifest_cannot_erase_held_declarations(self):
        del self.new["packages"][""]["dependencies"]["other"]
        self.reject("MANIFEST_SCOPE_VIOLATION")
    def test_linked_or_other_manager_graph_is_unsupported(self):
        self.new["packages"]["node_modules/express"]["link"]=True
        self.reject("UNSUPPORTED_LOCK_GRAPH")
    def test_target_version_is_still_mandatory(self):
        self.new["packages"]["node_modules/express"]["version"]="4.18.2"
        self.reject("ROOT_LOCK_SCOPE_VIOLATION")

if __name__=="__main__":unittest.main()
