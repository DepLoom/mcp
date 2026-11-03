"""Independent metadata controls, not a project migration or resolver oracle."""
import base64
import copy
import time
import unittest
from deploom_mcp.goal_published_edges import (PUBLIC_REGISTRY,PublishedMetadataSupplier,
    witness_published_ordinary_edge,witness_npm_target_paths)

def sri(label):
    return "sha512-"+base64.b64encode((label.encode()*64)[:64]).decode()

def manifest(name,version,deps=None):
    return {"name":name,"version":version,"dependencies":deps or {},"dist":{
        "integrity":sri(name+version),"tarball":PUBLIC_REGISTRY+"/"+name+"/-/"+name+"-"+version+".tgz"}}

def record(value):
    return {"version":value["version"],"integrity":value["dist"]["integrity"],"resolved":value["dist"]["tarball"],
            "dependencies":copy.deepcopy(value.get("dependencies",{}))}

class FakeClient:
    registry=PUBLIC_REGISTRY
    def __init__(self,data):self.data=data;self.calls=[];self.unavailable=False
    def registry_exact_version_manifest(self,name,version):
        self.calls.append((name,version))
        if self.unavailable:return None,"EXACT_VERSION_METADATA_UNVERIFIED"
        return copy.deepcopy(self.data[(name,version)]),"EXACT_VERSION_METADATA_ADMITTED"

class PublishedEdgeTests(unittest.TestCase):
    def setUp(self):
        self.now=[1000.0]
        self.old_parent=manifest("is-odd","3.0.0",{"is-number":"^6.0.0"})
        self.parent=manifest("is-odd","3.0.1",{"is-number":"^6.0.0"})
        self.child=manifest("is-number","6.0.0")
        self.client=FakeClient({("is-odd","3.0.0"):self.old_parent,("is-odd","3.0.1"):self.parent,("is-number","6.0.0"):self.child})
        self.supplier=PublishedMetadataSupplier(deadline_utc=1100,clock=lambda:self.now[0],ttl_seconds=10,client=self.client)
        self.context={"lockfileVersion":2,"parent":record(self.parent),"child":record(self.child)}
        self.goal={"schemaVersion":1,"goalKind":"functional-maintenance","healthPolicy":"REPORT_ONLY","goalId":"edge-control",
            "exactTargets":{"is-odd":"3.0.1"},"mutablePackages":["is-odd"],"immutablePackages":["is-number"],"excludedPackages":[],
            "requiredChecks":["install","tests","runtime"],"acceptPartial":False,"allowSourceRepair":False,"policyDigest":"a"*64}
    def edge(self):
        return witness_published_ordinary_edge("is-odd","3.0.1","is-number","6.0.0",self.context,self.supplier)
    def locks(self):
        def lock(parent):
            root={"name":"engineering-control","dependencies":{"is-odd":parent["version"],"is-number":"6.0.0"}}
            return {"lockfileVersion":2,"packages":{"":root,"node_modules/is-odd":record(parent),"node_modules/is-number":record(self.child)}}
        return lock(self.old_parent),lock(self.parent)
    def test_published_edge_is_witnessed_but_never_instance_or_execution_proof(self):
        result=self.edge()
        self.assertEqual(result["status"],"PASS");self.assertEqual(result["instanceNecessity"],"UNKNOWN")
        self.assertFalse(result["fullAcceptance"]);self.assertEqual(result["executionAuthority"],"NONE")
        self.assertEqual(result["parent"]["origin"],"TEST_DOUBLE_CONTROL_FLOW_ONLY")
        self.assertEqual(result["parent"]["digestEncoding"],"canonical-json-not-wire-bytes")
    def test_candidate_only_invented_edge_rejected(self):
        self.context["parent"]["dependencies"]["invented"]="1.0.0"
        result=witness_published_ordinary_edge("is-odd","3.0.1","invented","1.0.0",self.context,self.supplier)
        self.assertEqual(result["reason"],"PUBLISHED_ORDINARY_EDGE_ABSENT");self.assertEqual(result["status"],"FAIL")
    def test_candidate_range_rewrite_rejected(self):
        self.context["parent"]["dependencies"]["is-number"]="*"
        self.assertEqual(self.edge()["status"],"FAIL")
    def test_child_outside_published_range_fails(self):
        result=witness_published_ordinary_edge("is-odd","3.0.1","is-number","7.0.0",self.context,self.supplier)
        self.assertEqual(result["reason"],"CHILD_VERSION_OUTSIDE_PUBLISHED_RANGE")
    def test_wrong_name_or_version_is_not_reused(self):
        self.assertEqual(self.edge()["status"],"PASS")
        for key,value in (("name","other"),("version","3.0.0")):
            parent=copy.deepcopy(self.context["parent"]);parent[key]=value
            self.assertEqual(self.supplier.fetch("is-odd","3.0.1",parent)["status"],"UNKNOWN")
    def test_wrong_integrity_and_registry_cannot_reuse_cached_evidence(self):
        self.assertEqual(self.edge()["status"],"PASS")
        self.context["parent"]["integrity"]=sri("wrong")
        self.assertEqual(self.edge()["reason"],"LOCK_REGISTRY_ARTIFACT_IDENTITY_MISMATCH")
        self.client.registry="https://other.example.invalid"
        self.assertEqual(self.edge()["reason"],"UNSUPPORTED_REGISTRY_IDENTITY")
    def test_foreign_resolved_url_is_not_silently_normalized(self):
        self.context["parent"]["resolved"]="https://other.example.invalid/package.tgz"
        self.assertEqual(self.edge()["status"],"UNKNOWN")
    def test_stale_memo_never_masks_unavailable_source(self):
        self.assertEqual(self.edge()["status"],"PASS")
        self.now[0]+=11;self.client.unavailable=True
        self.assertEqual(self.edge()["status"],"UNKNOWN")
        self.assertEqual(len(self.client.calls),3)
    def test_conflicting_published_payload_within_attempt_is_unknown(self):
        self.assertEqual(self.edge()["status"],"PASS")
        self.now[0]+=11;self.parent["dependencies"]["different"]="1.0.0"
        self.assertEqual(self.edge()["reason"],"CONFLICTING_METADATA_WITHIN_ATTEMPT")
    def test_future_or_expired_evidence_does_not_pass(self):
        self.assertEqual(self.edge()["status"],"PASS")
        self.now[0]-=1;self.assertEqual(self.edge()["reason"],"METADATA_CLOCK_WENT_BACKWARD")
        self.now[0]=1100;self.assertEqual(self.edge()["reason"],"METADATA_ATTEMPT_DEADLINE")
    def test_optional_peer_alias_and_missing_integrity_are_unknown(self):
        for field,value in (("optionalDependencies",{"x":"1"}),("peerDependencies",{"x":"1"}),("dependencies",{"x":"npm:other@1"})):
            with self.subTest(field=field):
                parent=copy.deepcopy(self.parent);parent[field]=value
                client=FakeClient({("is-odd","3.0.1"):parent})
                supplier=PublishedMetadataSupplier(deadline_utc=1100,clock=lambda:1000,client=client)
                self.assertEqual(supplier.fetch("is-odd","3.0.1",record(parent))["status"],"UNKNOWN")
        parent=record(self.parent);parent.pop("integrity")
        self.assertEqual(self.supplier.fetch("is-odd","3.0.1",parent)["status"],"UNKNOWN")
    def test_optional_declaration_overriding_same_ordinary_name_stays_unknown(self):
        self.parent["optionalDependencies"]={"is-number":"^6.0.0"}
        self.assertEqual(self.edge()["status"],"UNKNOWN")
        self.assertEqual(self.edge()["reason"],"CONDITIONAL_OR_BUNDLED_DEPENDENCIES_UNSUPPORTED")
    def test_target_path_and_shared_unchanged_instance_do_not_expand_mutability(self):
        before,after=self.locks();result=witness_npm_target_paths(before,after,self.goal,self.supplier)
        self.assertEqual(result["publishedEdges"],"ENGINEERING_CONTROL_PASS");self.assertFalse(result["liveSourceConfirmed"])
        self.assertEqual(result["evidenceOrigin"],"ENGINEERING_CONTROL");self.assertFalse(result["mutableInstancesAuthorized"])
        self.assertEqual(result["instanceNecessity"],"UNKNOWN");self.assertFalse(result["executionReady"])
        self.assertEqual({c["instance"] for c in result["changedInstances"]},{"node_modules/is-odd"})
    def test_invented_target_path_cannot_expand_declared_scope_authority(self):
        before,after=self.locks();after["packages"]["node_modules/is-odd"]["dependencies"]["invented"]="1.0.0"
        after["packages"]["node_modules/invented"]={"version":"1.0.0"}
        result=witness_npm_target_paths(before,after,self.goal,self.supplier)
        self.assertEqual(result["status"],"FAIL")
        self.assertEqual(result["reason"],"CANDIDATE_DEPENDENCIES_DIFFER_FROM_PUBLISHED_MANIFEST")
    def test_changed_held_shared_instance_rejected_before_metadata_requests(self):
        before,after=self.locks();after["packages"]["node_modules/is-number"]["integrity"]=sri("tampered")
        result=witness_npm_target_paths(before,after,self.goal,self.supplier)
        self.assertEqual(result["declaredLockClosure"],"FAIL");self.assertEqual(self.client.calls,[])
    def test_nonpass_declared_scope_mapping_is_never_elevated(self):
        from unittest.mock import patch
        before,after=self.locks()
        with patch("deploom_mcp.goal_published_edges.validate_npm_lock_scope",return_value={"status":"FAIL"}):
            result=witness_npm_target_paths(before,after,self.goal,self.supplier)
        self.assertEqual(result["status"],"UNKNOWN");self.assertEqual(self.client.calls,[])
    def test_trusted_factory_transport_option_does_not_inject_a_test_client(self):
        import sys
        from unittest.mock import patch
        from deploom_mcp.distribution import resolve_core_dir
        sys.path.insert(0,str(resolve_core_dir()))
        import dependency_live_roadmap_generator as core
        with patch.object(core,"LiveDataClient") as factory:
            supplier=PublishedMetadataSupplier(deadline_utc=time.time()+60,use_system_proxy=True)
            self.assertTrue(factory.call_args.kwargs["use_system_proxy"])
            self.assertFalse(supplier.engineering)
        with self.assertRaises(ValueError):PublishedMetadataSupplier(deadline_utc=time.time()+60,use_system_proxy="true")
    def test_v3_published_edge_uses_same_exact_metadata_checks(self):
        self.context['lockfileVersion']=3
        self.assertEqual(self.edge()['status'],'PASS')
        self.context['parent']['dependencies']['is-number']='^99.0.0'
        self.assertNotEqual(self.edge()['status'],'PASS')
    def test_published_edge_format_is_strict_integer(self):
        for version in (True,3.0,'3'):
            self.context['lockfileVersion']=version
            with self.subTest(version=version):self.assertEqual(self.edge()['status'],'UNKNOWN')
    def test_unsupported_lock_and_request_budget_are_unknown(self):
        self.context["lockfileVersion"]=1;self.assertEqual(self.edge()["status"],"UNKNOWN")
        self.context["lockfileVersion"]=2;self.supplier.max_requests=1
        self.assertEqual(self.edge()["reason"],"METADATA_REQUEST_LIMIT")

if __name__=="__main__":unittest.main()
