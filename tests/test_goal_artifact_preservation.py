"""Pure layout-scope negatives plus independent Node lookup; no project install."""
import base64,copy,json,shutil,subprocess,tempfile,unittest
from pathlib import Path
from deploom_mcp.goal_contract import canonical_digest,validate_goal,GoalContractError
from deploom_mcp.goal_lock_scope import evaluate_npm_artifact_preservation,validate_npm_lock_scope,_resolve

class ArtifactPreservationTests(unittest.TestCase):
    def setUp(self):
        def record(name,version,**extra):
            return {'version':version,'integrity':'sha512-'+base64.b64encode((name+version).encode().ljust(64,b'0')[:64]).decode(),
                    'resolved':f'https://registry.npmjs.org/{name}/-/{name}-{version}.tgz',**extra}
        self.oldleaf=record('leaf','3.1.1');self.newleaf=record('leaf','5.0.0');self.consumer=record('consumer','1.0.0',dependencies={'leaf':'^3.1.1'},dev=True)
        self.old={'name':'fixture','version':'1.0.0','lockfileVersion':2,'requires':True,'packages':{
            '':{'name':'fixture','version':'1.0.0','dependencies':{'leaf':'^3.1.1','consumer':'1.0.0'}},
            'node_modules/leaf':self.oldleaf,'node_modules/consumer':self.consumer}}
        self.new=copy.deepcopy(self.old);self.new['packages']['']['dependencies']['leaf']='5.0.0';self.new['packages']['node_modules/leaf']=self.newleaf
        self.src='node_modules/leaf';self.dst='node_modules/consumer/node_modules/leaf'
        self.new['packages'][self.dst]={**self.oldleaf,'dev':True}
        self.goal={'schemaVersion':1,'goalKind':'functional-maintenance','healthPolicy':'REPORT_ONLY','goalId':'layout',
            'exactTargets':{'leaf':'5.0.0'},'mutablePackages':['leaf'],'immutablePackages':['consumer'],'excludedPackages':[],
            'requiredChecks':['full-tree','runtime'],'acceptPartial':False,'allowSourceRepair':False,'policyDigest':'a'*64}
        self.before_manifest=copy.deepcopy(self.old['packages']['']);self.before_manifest['scripts']={'test':'unchanged'}
        self.after_manifest=copy.deepcopy(self.new['packages']['']);self.after_manifest['scripts']={'test':'unchanged'}
        self.sync_views()
    def sync_views(self):
        for lock in (self.old,self.new):
            tree={}
            for location,record in sorted(lock['packages'].items(),key=lambda x:x[0].count('/')):
                if not location:continue
                parts=location.split('/node_modules/');current=tree
                for parent in parts[:-1]:
                    name=parent.removeprefix('node_modules/');current=current[name].setdefault('dependencies',{})
                name=parts[-1].removeprefix('node_modules/')
                out={k:copy.deepcopy(v) for k,v in record.items() if k not in {'dependencies','optionalDependencies','engines','license','funding','peerDependencies','peerDependenciesMeta','os','cpu','libc','inBundle','bundleDependencies','bundledDependencies'}}
                req={**record.get('dependencies',{}),**record.get('optionalDependencies',{})}
                if record.get('inBundle'):out['bundled']=True
                if req:out['requires']=req
                current[name]=out
            lock['dependencies']=tree
    def contract(self):
        return {'schemaVersion':1,'mode':'PRESERVE_HELD_ARTIFACT_V1','beforeLockDigest':canonical_digest(self.old),
            'afterLockDigest':canonical_digest(self.new),'beforeManifestDigest':canonical_digest(self.before_manifest),'afterManifestDigest':canonical_digest(self.after_manifest),'goalDigest':canonical_digest(validate_goal(self.goal)),
            'preservations':[{'package':'leaf','version':'3.1.1','beforePath':self.src,'afterPath':self.dst,'contextFlags':{'dev':{'before':self.old['packages'][self.src].get('dev'),'after':self.new['packages'][self.dst].get('dev')}}}]}
    def evaluate(self,contract=None):return evaluate_npm_artifact_preservation(self.old,self.new,self.goal,contract or self.contract(),before_manifest=self.before_manifest,after_manifest=self.after_manifest)
    def test_declared_match_grants_no_execution_or_project_proof(self):
        result=self.evaluate();self.assertEqual(result['status'],'DECLARED_HELD_ARTIFACTS_PRESERVED_ONLY')
        self.assertFalse(result['fullAcceptance']);self.assertEqual(result['executionAuthority'],'NONE')
        self.assertEqual(result['physicalResolution'],'NOT_VERIFIED');self.assertEqual(result['metadata'],'NOT_VERIFIED')
        self.assertEqual(result['comparedHeldEdges'],1);self.assertEqual(result['heldInstanceCount'],2)
        self.assertIn('STRICT_INSTALL_AND_FULL_TREE',result['missingEvidence'])
    def test_default_guard_unchanged_and_still_rejects(self):
        with self.assertRaisesRegex(GoalContractError,'TRANSITIVE_SCOPE_VIOLATION'):validate_npm_lock_scope(self.old,self.new,self.goal)
    def test_semantic_lock_or_goal_drift_rejects(self):
        contract=self.contract();self.new['packages'][self.dst]['version']='3.1.2'
        self.assertEqual(self.evaluate(contract)['reason'],'STALE_PRESERVATION_CONTRACT')
        self.new['packages'][self.dst]['version']='3.1.1';self.goal['requiredChecks'].append('new-check')
        self.assertEqual(self.evaluate(contract)['reason'],'STALE_PRESERVATION_CONTRACT')
    def test_caller_passed_flag_not_authority(self):
        contract={**self.contract(),'passed':True};self.assertEqual(self.evaluate(contract)['reason'],'INVALID_PRESERVATION_CONTRACT')
    def test_nested_version_integrity_or_origin_change_rejects(self):
        for key,value in [('version','3.1.2'),('integrity','sha512-'+base64.b64encode(b'Z'*64).decode()),('resolved','https://example.invalid/other.tgz')]:
            saved=copy.deepcopy(self.new)
            self.new['packages'][self.dst][key]=value;self.sync_views()
            with self.subTest(key=key):self.assertEqual(self.evaluate()['status'],'FAIL')
            self.new=saved
    def test_copy_artifact_metadata_must_be_identical(self):
        self.new['packages'][self.dst]['engines']={'node':'>=99'}
        self.assertEqual(self.evaluate()['reason'],'PRESERVATION_ARTIFACT_RECORD_CHANGED')
    def test_additional_nested_instance_cannot_be_hidden(self):
        self.new['packages']['node_modules/consumer/node_modules/extra']=copy.deepcopy(self.oldleaf);self.sync_views()
        self.assertEqual(self.evaluate()['reason'],'PRESERVATION_UNENUMERATED_DELTA')
    def test_wrong_placement_cannot_expose_new_root_to_held_consumer(self):
        wrong='node_modules/unrelated/node_modules/leaf';self.new['packages'][wrong]=self.new['packages'].pop(self.dst)
        # A new unrelated consumer is itself outside the enumerated delta.
        self.new['packages']['node_modules/unrelated']=copy.deepcopy(self.consumer);self.dst=wrong;self.sync_views()
        self.assertEqual(self.evaluate()['status'],'FAIL')
    def test_legacy_identity_or_requirements_mismatch_rejects(self):
        for change in ['version','requires']:
            saved=copy.deepcopy(self.new)
            if change=='version':self.new['dependencies']['consumer']['dependencies']['leaf']['version']='3.1.2'
            else:self.new['dependencies']['consumer']['requires']['leaf']='*'
            with self.subTest(change=change):self.assertEqual(self.evaluate()['reason'],'PRESERVATION_LOCK_VIEW_MISMATCH')
            self.new=saved
    def test_affected_conditional_context_returns_unknown(self):
        for key,value in [('peerDependencies',{'leaf':'^3.1.1'}),('optionalDependencies',{'leaf':'^3.1.1'}),('os',['win32']),('cpu',['x64']),('libc',['glibc'])]:
            old,new=copy.deepcopy(self.old),copy.deepcopy(self.new)
            self.old['packages']['node_modules/consumer'][key]=value;self.new['packages']['node_modules/consumer'][key]=value;self.sync_views()
            with self.subTest(key=key):self.assertEqual(self.evaluate()['status'],'UNKNOWN')
            self.old,self.new=old,new
    def test_copied_conditional_artifact_returns_unknown(self):
        for key,value in [('optional',True),('peer',True),('os',['win32']),('bundledDependencies',['child'])]:
            old,new=copy.deepcopy(self.old),copy.deepcopy(self.new)
            self.old['packages'][self.src][key]=value;self.new['packages'][self.dst][key]=value;self.sync_views()
            with self.subTest(key=key):self.assertEqual(self.evaluate()['status'],'UNKNOWN')
            self.old,self.new=old,new
    def test_protected_target_never_relaxes(self):
        self.goal['immutablePackages'].append('leaf');self.assertEqual(self.evaluate_nonstrict_contract()['status'],'FAIL')
    def evaluate_nonstrict_contract(self):
        c={'schemaVersion':1,'mode':'PRESERVE_HELD_ARTIFACT_V1','beforeLockDigest':canonical_digest(self.old),'afterLockDigest':canonical_digest(self.new),'goalDigest':'0'*64,'preservations':[]}
        return evaluate_npm_artifact_preservation(self.old,self.new,self.goal,c,before_manifest=self.before_manifest,after_manifest=self.after_manifest)
    def test_source_alias_or_link_is_unknown_not_compatible(self):
        self.new['packages'][self.dst]['version']='npm:other@3.1.1';self.sync_views();self.assertEqual(self.evaluate()['status'],'UNKNOWN')
    def test_untrusted_sources_or_missing_sri_are_unknown(self):
        for key,value in [('resolved','http://example.invalid/pkg.tgz'),('resolved','https://' + 'user:secret@example.invalid/pkg.tgz'),('integrity','sha512-bad')]:
            saved=copy.deepcopy(self.new);self.new['packages'][self.dst][key]=value;self.sync_views()
            with self.subTest(key=key):self.assertEqual(self.evaluate()['status'],'UNKNOWN')
            self.new=saved
    def test_node_real_lookup_agrees_for_hoisted_and_nested_leaf(self):
        node=shutil.which('node');self.assertTrue(node,'Declared Windows/Node profile requires Node')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for label,lock in [('before',self.old),('after',self.new)]:
                tree=root/label;tree.mkdir()
                for loc,rec in lock['packages'].items():
                    if not loc:continue
                    directory=tree/loc;directory.mkdir(parents=True,exist_ok=True)
                    (directory/'package.json').write_text(json.dumps({'name':loc.split('node_modules/')[-1],'version':rec['version'],'main':'index.js'}))
                    (directory/'index.js').write_text('module.exports=1;\n')
                script="const {createRequire}=require('node:module');const p=require('node:path');const r=createRequire(p.resolve('node_modules/consumer/index.js'));console.log(p.relative(process.cwd(),p.dirname(r.resolve('leaf/package.json'))).split(p.sep).join('/'));"
                x=subprocess.run([node,'-e',script],cwd=tree,capture_output=True,text=True,timeout=10);self.assertEqual(x.returncode,0,x.stderr)
                self.assertEqual(x.stdout.strip(),_resolve(lock['packages'],'node_modules/consumer','leaf'))
    def test_invalid_input_does_not_claim_pass(self):
        for value in [None,[],True]:self.assertEqual(evaluate_npm_artifact_preservation(value,self.new,self.goal,self.contract())['status'],'UNKNOWN')

    def add_unchanged_bundle(self):
        owner={**copy.deepcopy(self.consumer),'dependencies':{'member':'1.0.0'},'bundleDependencies':['member']}
        owner['resolved']='https://registry.npmjs.org/bundle/-/bundle-1.0.0.tgz'
        for lock in (self.old,self.new):
            lock['packages']['']['dependencies']['bundle']='1.0.0'
            lock['packages']['node_modules/bundle']=copy.deepcopy(owner)
            lock['packages']['node_modules/bundle/node_modules/member']={'version':'1.0.0','inBundle':True,'dev':True}
        for manifest in (self.before_manifest,self.after_manifest):manifest['dependencies']['bundle']='1.0.0'
        self.sync_views()
    def test_unchanged_bundle_context_is_declared_only_not_member_sri_proof(self):
        self.add_unchanged_bundle();result=self.evaluate()
        self.assertEqual(result['status'],'DECLARED_HELD_ARTIFACTS_PRESERVED_ONLY')
        self.assertEqual(result['unchangedBundledContexts'],['node_modules/bundle/node_modules/member'])
        self.assertEqual(result['physicalResolution'],'NOT_VERIFIED');self.assertEqual(result['metadata'],'NOT_VERIFIED')
    def test_bundle_owner_member_or_flag_drift_is_not_ignored(self):
        for kind in ['owner','member','legacyflag']:
            self.setUp();self.add_unchanged_bundle()
            if kind=='owner':self.new['packages']['node_modules/bundle']['version']='1.0.1';self.sync_views()
            elif kind=='member':self.new['packages']['node_modules/bundle/node_modules/member']['version']='1.0.1';self.sync_views()
            else:self.new['dependencies']['bundle']['dependencies']['member']['bundled']=False
            with self.subTest(kind=kind):self.assertNotEqual(self.evaluate()['status'],'DECLARED_HELD_ARTIFACTS_PRESERVED_ONLY')
    def test_missing_ordinary_edge_is_unknown_even_when_both_closures_omit_it(self):
        for lock in (self.old,self.new):lock['packages']['node_modules/consumer']['dependencies']['missing']='1.0.0'
        self.sync_views();self.assertEqual(self.evaluate()['reason'],'PRESERVATION_REQUIRED_EDGE_UNRESOLVED')
    def test_invalid_edge_name_is_never_interpreted_as_instance_path(self):
        for name in ['leaf/arbitrary','leaf/node_modules/other','@scope/leaf/node_modules/other']:
            self.setUp()
            for lock in (self.old,self.new):lock['packages']['node_modules/consumer']['dependencies'][name]='1.0.0'
            self.sync_views()
            with self.subTest(name=name):self.assertEqual(self.evaluate()['status'],'UNKNOWN')
    def test_manifest_script_changes_cannot_hide_in_unchanged_lock(self):
        self.after_manifest['scripts']['test']='echo fake'
        self.assertEqual(self.evaluate()['reason'],'MANIFEST_SCOPE_VIOLATION')
    def test_manifest_root_omissions_are_rejected(self):
        for manifest in (self.before_manifest,self.after_manifest):manifest['dependencies']['hidden']='1.0.0'
        self.assertEqual(self.evaluate()['reason'],'PRESERVATION_MANIFEST_LOCK_ROOT_MISMATCH')
    def test_context_dev_exception_must_be_explicit_and_exact(self):
        contract=self.contract();contract['preservations'][0]['contextFlags']={'dev':{'before':False,'after':False}}
        self.assertEqual(self.evaluate(contract)['reason'],'PRESERVATION_CONTEXT_FLAG_NOT_ENUMERATED')
    def test_boolean_schema_version_is_rejected(self):
        contract=self.contract();contract['schemaVersion']=True
        self.assertEqual(self.evaluate(contract)['reason'],'INVALID_PRESERVATION_CONTRACT')

    def test_declared_bundle_match_cannot_be_promoted_to_runtime_equivalence(self):
        self.add_unchanged_bundle();result=self.evaluate()
        self.assertEqual(result['status'],'DECLARED_HELD_ARTIFACTS_PRESERVED_ONLY')
        versions=[];node=shutil.which('node');self.assertTrue(node)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for label,lock in [('before',self.old),('after',self.new)]:
                tree=root/label;tree.mkdir()
                for location,record in lock['packages'].items():
                    if not location:continue
                    path=tree/location;path.mkdir(parents=True,exist_ok=True)
                    (path/'package.json').write_text(json.dumps({'name':location.split('node_modules/')[-1],'version':record['version'],'main':'index.js'}))
                    code="module.exports=require('leaf/package.json').version;" if record.get('inBundle') else 'module.exports=1;'
                    (path/'index.js').write_text(code)
                x=subprocess.run([node,'-e',"console.log(require('./node_modules/bundle/node_modules/member'))"],cwd=tree,capture_output=True,text=True,timeout=10)
                self.assertEqual(x.returncode,0,x.stderr);versions.append(x.stdout.strip())
        self.assertEqual(versions,['3.1.1','5.0.0'])
        self.assertFalse(result['fullAcceptance']);self.assertEqual(result['compatibility'],'NOT_VERIFIED');self.assertEqual(result['executionAuthority'],'NONE')
    def test_context_bool_alias_does_not_match_literal_true(self):
        c=self.contract();c['preservations'][0]['contextFlags']['dev']['after']=1
        self.assertEqual(self.evaluate(c)['reason'],'PRESERVATION_CONTEXT_FLAG_NOT_ENUMERATED')

    def test_invalid_nested_instance_syntax_is_not_a_root_package_name(self):
        name='consumer/node_modules/hidden'
        for manifest in (self.before_manifest,self.after_manifest):manifest['dependencies'][name]='1.0.0'
        for lock in (self.old,self.new):
            lock['packages']['']['dependencies'][name]='1.0.0'
            lock['packages']['node_modules/consumer/node_modules/hidden']={k:self.consumer[k] for k in ['version','integrity','resolved']}
        self.sync_views()
        self.assertEqual(self.evaluate()['status'],'UNKNOWN')

    def test_nonstring_resolved_returns_unknown_not_unhandled_exception(self):
        for value in [123,{},[],None,True]:
            self.setUp();self.new['packages'][self.dst]['resolved']=value;self.sync_views()
            with self.subTest(value=value):self.assertEqual(self.evaluate()['status'],'UNKNOWN')
    def test_only_integer_lock_v2_is_supported(self):
        for version in [3,2.0,True]:
            self.setUp();self.old['lockfileVersion']=version;self.new['lockfileVersion']=version
            with self.subTest(version=version):self.assertEqual(self.evaluate()['status'],'UNKNOWN')

if __name__=='__main__':unittest.main()
