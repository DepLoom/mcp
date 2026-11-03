"""MCP joins, not project acceptance. Fixture receipts do not grant authority."""
import copy,hashlib,json,os,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from deploom_mcp.goal_contract import canonical_digest,GoalContractError
from deploom_mcp.goal_delta_trial import _core
_core()
from standalone_build_outputs import KIND,capture_receipt
from deploom_mcp.goal_generated_outputs import (PROFILE,POLICY_KEY,validate_host_policy,
    verifier_arguments,expected_binding,observation_matches,physically_current,checkpoint_identity)

H=lambda raw:hashlib.sha256(raw).hexdigest()
class GeneratedOutputAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name)/'project';self.project.mkdir()
        self.env=patch.dict(os.environ,{'DEPLOOM_SOURCE_SCOPE_MODE':PROFILE});self.env.start();self.addCleanup(self.env.stop)
        for d in ['scripts','tests','dist','.tap/processinfo','.tap/test-results/tests']:(self.project/d).mkdir(parents=True,exist_ok=True)
        scripts={'test':'node scripts/build.js','test:perf':'node scripts/perf.js'}
        (self.project/'package.json').write_text(json.dumps({'scripts':scripts}))
        (self.project/'scripts/build.js').write_text('// fixture recipe\n')
        (self.project/'scripts/perf.js').write_text('// fixture perf\n')
        (self.project/'tests/a.js').write_text('// fixture test\n')
        spec={'schemaVersion':1,'kind':KIND,'buildFiles':['dist/index.cjs'],'testFiles':['tests/a.js'],'emptyDirectories':[],
            'buildScriptSha256':H((self.project/'scripts/build.js').read_bytes()),'testScriptSha256':H(scripts['test'].encode()),'perfScriptSha256':H(scripts['test:perf'].encode())}
        self.profile={'commands':['npm test','npm run test:perf']}
        self.policy={POLICY_KEY:spec,'projectExecutionEnvironment':'HOST_OWNED_PROJECT_EXECUTION_ENV_V1','privateVerificationRoute':'FROZEN_EXACT_SINGLE_MATERIALIZATION_V1'}
        for name in ['dist/index.cjs','.tap/processinfo/00000000-0000-4000-8000-000000000001.json','.tap/test-results/tests/a.js.tap']:(self.project/name).write_text('fixture')
        self.raw={'sourceSnapshotKey':'s'*32,'finalVerificationProfileDigest':canonical_digest(self.profile),'freshCommandInvocationId':'fixture-only',
            'materializedDependencyObligations':{'obligationPlanDigest':'d'*64},'resolvedLockfileHash':'l'*64,'environmentDigest':'e'*32,
            'materializedSourceOrigin':{'toolchain':{'nodeSha256':'n'*64,'npmCliSha256':'p'*64}},
            'commandObservations':[{'commandDigest':H(b'npm test')}]}
        self.raw['generatedOutputObservation']=capture_receipt(self.project,spec,deadline=time.time()+10,binding=expected_binding(self.raw))
    def test_explicit_host_recipe_and_core_arguments(self):
        self.assertEqual(validate_host_policy(self.policy,self.profile,self.project),self.policy[POLICY_KEY])
        args=verifier_arguments(self.policy,self.profile,self.project)
        self.assertEqual(args['generated_output_spec'],self.policy[POLICY_KEY])
        self.assertIn('environment',args)
    def test_input_transport_arguments_are_explicit_and_not_a_receipt(self):
        from deploom_mcp.goal_generated_outputs import source_materialization_arguments
        result=source_materialization_arguments(self.policy,self.profile,self.project)
        self.assertEqual(result,{'generated_output_materialization':{'spec':self.policy[POLICY_KEY],'commands':self.profile['commands']}})
        self.assertNotIn('generatedOutputObservation',result)
    def test_legacy_source_transport_remains_unchanged(self):
        from deploom_mcp.goal_generated_outputs import source_materialization_arguments
        with patch.dict(os.environ,{'DEPLOOM_SOURCE_SCOPE_MODE':''}):
            self.assertEqual(source_materialization_arguments({},self.profile,self.project),{})
    def test_actual_project_host_mapping_filters_unknown_and_loader_names(self):
        from deploom_mcp.goal_generated_outputs import project_host_environment
        host={'PATH':'node-path','SystemRoot':'windows','TEMP':'owned-temp','NODE_OPTIONS':'untrusted','DOTENV_CONFIG_PATH':'untrusted','TAP_CHANGED':'1','NYC_CONFIG':'untrusted','GITHUB_STEP_SUMMARY':'untrusted','MCP_FAKE_SECRET':'fixture'}
        self.assertEqual(project_host_environment(host),{'PATH':'node-path','SystemRoot':'windows','TEMP':'owned-temp'})
        self.assertEqual(host['NODE_OPTIONS'],'untrusted')
    def test_profile_cannot_be_enabled_without_bound_host_policy(self):
        with self.assertRaisesRegex(GoalContractError,'HOST_POLICY_REQUIRED'):validate_host_policy({},self.profile,self.project)
        with patch.dict(os.environ,{'DEPLOOM_SOURCE_SCOPE_MODE':''}):
            with self.assertRaisesRegex(GoalContractError,'SOURCE_PROFILE_REQUIRED'):validate_host_policy(self.policy,self.profile,self.project)
    def test_explicit_null_spec_is_invalid_not_legacy(self):
        policy={**self.policy,POLICY_KEY:None}
        with self.assertRaisesRegex(GoalContractError,'INVALID_SPEC'):validate_host_policy(policy,self.profile,self.project)
        raw=copy.deepcopy(self.raw);raw.pop('generatedOutputObservation')
        self.assertFalse(observation_matches(policy,self.profile,raw))
    def test_recipe_change_or_mixed_profile_rejected(self):
        changed=copy.deepcopy(self.policy);changed[POLICY_KEY]['testScriptSha256']='0'*64
        with self.assertRaisesRegex(GoalContractError,'RECIPE_UNPROVEN'):validate_host_policy(changed,self.profile,self.project)
        changed=copy.deepcopy(self.policy);changed['tapNycInput']={}
        with self.assertRaisesRegex(GoalContractError,'MIXED_PROFILE'):validate_host_policy(changed,self.profile,self.project)
    def test_receipt_binding_and_inventory_digest_cannot_be_substituted(self):
        self.assertTrue(observation_matches(self.policy,self.profile,self.raw))
        for field,value in [('sourceSnapshotKey','other'),('environmentDigest','other'),('resolvedLockfileHash','other'),('freshCommandInvocationId','other')]:
            raw=copy.deepcopy(self.raw);raw[field]=value
            with self.subTest(field=field):self.assertFalse(observation_matches(self.policy,self.profile,raw))
        raw=copy.deepcopy(self.raw);raw['generatedOutputObservation']['inventory'][0]['inode']='other'
        self.assertFalse(observation_matches(self.policy,self.profile,raw))
    def test_missing_or_unrequested_receipt_not_accepted(self):
        raw=copy.deepcopy(self.raw);raw.pop('generatedOutputObservation')
        self.assertFalse(observation_matches(self.policy,self.profile,raw))
        self.assertFalse(observation_matches({},self.profile,self.raw))
        self.assertTrue(observation_matches({},self.profile,raw))
    def test_actual_unchanged_outputs_current_and_deleted_output_not_current(self):
        self.assertTrue(physically_current(self.policy,self.profile,self.project,self.raw,deadline=time.time()+5))
        (self.project/'dist/index.cjs').unlink()
        self.assertFalse(physically_current(self.policy,self.profile,self.project,self.raw,deadline=time.time()+5))
    def test_tap_tamper_and_unknown_new_output_not_current(self):
        file=self.project/'.tap/test-results/tests/a.js.tap';file.write_text('tampered')
        self.assertFalse(physically_current(self.policy,self.profile,self.project,self.raw,deadline=time.time()+5))
        file.write_text('fixture');(self.project/'dist/extra.js').write_text('extra')
        self.assertFalse(physically_current(self.policy,self.profile,self.project,self.raw,deadline=time.time()+5))
    def test_private_owner_rejects_missing_receipt_before_complete_observation(self):
        from deploom_mcp.goal_frozen_candidate import _verify_raw
        reg=SimpleNamespace(policy=self.policy,profile=self.profile)
        raw=copy.deepcopy(self.raw);raw.pop('generatedOutputObservation')
        with patch('deploom_mcp.goal_execution_environment.environment_observation_matches_contract',return_value=True):
            with self.assertRaisesRegex(GoalContractError,'FROZEN_GENERATED_OUTPUT_UNPROVEN'):_verify_raw(raw,reg,'s'*32,{},self.project)
    def test_final_owner_rejects_missing_output_observation(self):
        from deploom_mcp.goal_final_checkpoint import live_complete
        context=SimpleNamespace(registration=SimpleNamespace(policy=self.policy),final_profile=self.profile,
            accepted_receipt={'verificationObservation':{'candidateVerifiedSourceSnapshotKey':'s'*32}})
        raw=copy.deepcopy(self.raw);raw.pop('generatedOutputObservation')
        self.assertFalse(live_complete(context,raw,{},self.project))
    def test_current_owner_checks_outputs_after_old_guards(self):
        from deploom_mcp.goal_final_checkpoint import current_state_matches
        context=SimpleNamespace(registration=SimpleNamespace(policy=self.policy),final_profile=self.profile)
        record={'liveObservation':self.raw}
        with patch('deploom_mcp.goal_final_checkpoint.current_environment_matches',return_value=True),patch('deploom_mcp.goal_tap_nyc_execution.physically_current',return_value=True):
            from deploom_mcp.goal_current_read_trace import CurrentReadTrace
            trace=CurrentReadTrace()
            self.assertTrue(current_state_matches(context,record,self.project,_trace=trace))
            self.assertEqual([r['result'] for r in trace.events if r['gate']=='generated_outputs'],['BEGIN',True])
            (self.project/'dist/index.cjs').write_text('changed')
            self.assertFalse(current_state_matches(context,record,self.project))
    def test_generated_current_cannot_accept_a_late_true(self):
        from deploom_mcp.goal_final_checkpoint import current_state_matches
        from test_goal_current_proof_budget import Clock
        clock=Clock();budget=clock.budget()
        context=SimpleNamespace(registration=SimpleNamespace(policy=self.policy),final_profile=self.profile)
        def late(*args,**kwargs):clock.advance(21);return True
        with patch('deploom_mcp.goal_final_checkpoint.current_environment_matches',return_value=True),patch('deploom_mcp.goal_tap_nyc_execution.physically_current',return_value=True),patch('deploom_mcp.goal_generated_outputs.physically_current',side_effect=late):
            with self.assertRaisesRegex(GoalContractError,'CURRENT_PROOF_DEADLINE_EXCEEDED'):current_state_matches(context,{'liveObservation':self.raw},self.project,_budget=budget)
    @unittest.skipUnless(os.name=='nt','native V2 supervision requires Windows')
    def test_native_core_adapter_receipt_and_process_closure(self):
        import shutil
        from standalone_build_outputs import GeneratedOutputError
        from materialized_workspace_verifier import verify_current_workspace
        from resolved_dependency_state import compile_npm_dependency_obligations
        from deploom_mcp.goal_tap_nyc_execution import verify_with_tap_input
        from source_snapshot import source_snapshot_fingerprint
        q=self.project.parent/'native';q.mkdir();cache=self.project.parent/'cache';cache.mkdir()
        for name in ['scripts','tests']:shutil.copytree(self.project/name,q/name)
        package=json.loads((self.project/'package.json').read_bytes());package.update(name='mcp-output-fixture',version='1.0.0',private=True)
        (q/'package.json').write_text(json.dumps(package))
        body="if(process.env.DOTENV_CONFIG_PATH||process.env.TAP_CHANGED||process.env.NYC_CONFIG||process.env.MCP_FAKE_SECRET)throw Error('ENVIRONMENT_LEAK');const f=require('fs');f.mkdirSync('dist');f.writeFileSync('dist/index.cjs','built');f.mkdirSync('.tap/processinfo',{recursive:true});f.mkdirSync('.tap/test-results/tests',{recursive:true});f.writeFileSync('.tap/processinfo/00000000-0000-4000-8000-000000000001.json','{}');f.writeFileSync('.tap/test-results/tests/a.js.tap','ok 1');"
        (q/'scripts/build.js').write_text(body)
        policy=copy.deepcopy(self.policy);policy[POLICY_KEY]['buildScriptSha256']=H(body.encode())
        lock={'name':package['name'],'version':'1.0.0','lockfileVersion':2,'requires':True,'packages':{'':package}}
        rawlock=json.dumps(lock).encode();(q/'package-lock.json').write_bytes(rawlock)
        plan=compile_npm_dependency_obligations(rawlock,rawlock,exact_targets={},changed_instances=[],immutable_packages=[],excluded_packages=[],delta_digest='a'*64,scope_digest='b'*64)
        marker=q.parent/'preload-executed.txt';preload=q.parent/'synthetic-preload.cjs'
        preload.write_text("require('fs').writeFileSync("+json.dumps(str(marker))+",'UNEXPECTED_PRELOAD');")
        with patch.dict(os.environ,{'GIT_CEILING_DIRECTORIES':str(q.parent),'NODE_OPTIONS':'--require='+json.dumps(str(preload)),'NODE_PATH':'','DOTENV_CONFIG_PATH':'SYNTHETIC_OUT_OF_SCOPE','TAP_CHANGED':'1','NYC_CONFIG':'SYNTHETIC_OUT_OF_SCOPE','MCP_FAKE_SECRET':'fixture-only','DEPLOOM_MCP_OFFLINE_CACHE':str(cache)}):
            raw,tap=verify_with_tap_input(verify_current_workspace,policy,self.profile,'private',q,
                expected_source_key=source_snapshot_fingerprint(q),dependency_plan=plan,commands=self.profile['commands'],
                cache_dir=cache,deadline=time.time()+45,final_profile_digest=canonical_digest(self.profile))
            self.assertIsNone(tap);self.assertTrue(observation_matches(policy,self.profile,raw))
            self.assertFalse(marker.exists())
            from deploom_mcp.goal_execution_environment import environment_matches
            self.assertTrue(environment_matches(policy,raw))
            self.assertTrue(physically_current(policy,self.profile,q,raw,deadline=time.time()+5))
            self.assertTrue(all(r['descendantsRemaining']==0 and r['supervision']=='guaranteed-tree' for r in raw['ownerOperationObservations']))
            self.assertFalse(raw['fullAcceptance']);self.assertFalse(raw['checkpointPublished'])
            (q/'dist/index.cjs').write_text('changed')
            self.assertFalse(physically_current(policy,self.profile,q,raw,deadline=time.time()+5))
    def test_checkpoint_includes_generated_identity_and_keeps_old_empty_case(self):
        from deploom_mcp.goal_final_checkpoint import tap_checkpoint_identity
        record={'liveObservation':self.raw};want={'generatedOutputObservationDigest':canonical_digest(self.raw['generatedOutputObservation'])}
        self.assertEqual(checkpoint_identity(record),want);self.assertEqual(tap_checkpoint_identity(record),want)
        self.assertEqual(tap_checkpoint_identity({}),{})
    def test_existing_execution_adapter_passes_spec_without_tap_recipe(self):
        from deploom_mcp.goal_tap_nyc_execution import verify_with_tap_input
        seen={}
        def fixture_verifier(project,**args):seen.update(args);return {'fixtureOnly':True}
        raw,tap=verify_with_tap_input(fixture_verifier,self.policy,self.profile,'private',self.project)
        self.assertEqual(raw,{'fixtureOnly':True});self.assertIsNone(tap)
        self.assertEqual(seen['generated_output_spec'],self.policy[POLICY_KEY]);self.assertEqual(seen['environment_contract'],self.policy['projectExecutionEnvironment'])

if __name__=='__main__':unittest.main()
