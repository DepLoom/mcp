"""No-agent engineering fixtures: real Core/Z3, no install/project command."""
import copy
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from deploom_mcp.goal_contract import GoalContractError,validate_goal,canonical_digest,bind_goal
from deploom_mcp.goal_choice_worker import prepare_choice_plan
from deploom_mcp.goal_choice_lineage import validate_lineage
from deploom_mcp.goal_execution_registry import load_host_registration,OPERATION

class CoupledGoalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.project=self.root/'fixture';self.project.mkdir()
        self.current={'engine':'6.4.5','plugin':'4.3.4','held':'1.0.0'}
        manifest={'name':'engineering-fixture','version':'1.0.0','dependencies':self.current}
        lock={'name':manifest['name'],'version':manifest['version'],'lockfileVersion':3,
            'packages':{'':manifest,**{'node_modules/'+n:{'version':v} for n,v in self.current.items()}}}
        (self.project/'package.json').write_text(json.dumps(manifest),encoding='utf-8')
        (self.project/'package-lock.json').write_text(json.dumps(lock),encoding='utf-8')
        self.policy={'healthPolicy':'REPORT_ONLY','hardPolicyRequired':False}
        self.domains={'engine':['6.4.5','7.0.0'],'plugin':['4.3.4','4.5.2','5.0.0']}
        domains={**self.domains,'held':['1.0.0']}
        records={n:{v:{'name':n,'version':v} for v in vs} for n,vs in domains.items()}
        for v,spec in [('4.3.4','^6'),('4.5.2','^6 || ^7'),('5.0.0','^6 || ^7')]:records['plugin'][v]['peerDependencies']={'engine':spec}
        now=datetime.now(timezone.utc)
        self.inputs={'schemaVersion':1,'kind':'HOST_PINNED_ROOT_METADATA_V1',
            'createdUTC':(now-timedelta(seconds=10)).isoformat(),'expiresUTC':(now+timedelta(hours=1)).isoformat(),
            'basePolicy':self.policy,'records':records}
        self.goal={'schemaVersion':3,'goalKind':'coupled-maintenance','healthPolicy':'REPORT_ONLY',
            'goalId':'coupled-fixture','candidateDomains':self.domains,'mutablePackages':['engine','plugin'],
            'immutablePackages':['held'],'excludedPackages':[],'requiredChecks':['install','tree','runtime'],
            'acceptPartial':False,'allowSourceRepair':False,'policyDigest':canonical_digest(self.policy),
            'nodeVersion':'24.18.0','metadataDigest':self.save('choice-inputs',self.inputs),
            'objective':{'kind':'PRIMARY_VERSION_THEN_MIN_CHANGES_V1','primaryPackage':'engine','maxMajorChanges':1}}
        self.env=patch.dict(os.environ,{'DEPLOOM_MCP_STATE_DIR':str(self.root)})
        self.env.start();self.addCleanup(self.env.stop)
    def save(self,folder,value):
        raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
        digest=hashlib.sha256(raw).hexdigest();path=self.root/folder/(digest+'.json')
        path.parent.mkdir(exist_ok=True);path.write_bytes(raw);return digest
    def plan(self):return prepare_choice_plan(self.project,self.goal)
    def validate(self,intent):return validate_lineage(self.root,intent['goal'],intent['binding'],intent['policy'],self.project,profile=intent.get('executionProfile'))
    def test_real_core_optimum_and_parent_exact_admission(self):
        result=self.plan();intent=result['derivedExactIntent']
        self.assertEqual(result['assignment'],{'engine':'7.0.0','plugin':'4.5.2','held':'1.0.0'})
        self.assertFalse(result['executionReady']);self.assertEqual(result['verification'],'NOT_VERIFIED')
        self.assertEqual(result['fullTreeStatus'],'NOT_EVALUATED')
        self.assertEqual(self.validate(intent)['parentGoalDigest'],result['binding']['goalDigest'])
        self.assertEqual(intent['goal']['immutablePackages'],['held'])
        self.assertEqual(intent['goal']['requiredChecks'],sorted(self.goal['requiredChecks']))
        self.assertNotEqual(intent['goal']['policyDigest'],self.goal['policyDigest'])
    def test_parent_and_scope_input_reject_unknown_or_partial(self):
        for change in [{'acceptPartial':True},{'mutablePackages':['engine']},{'objective':{**self.goal['objective'],'kind':'LATEST'}},
                       {'immutablePackages':['engine']},{'exactTargets':{'engine':'7.0.0'}}]:
            with self.subTest(change=change),self.assertRaises(GoalContractError):validate_goal({**self.goal,**change})
    def test_stale_or_mutated_metadata_rejected(self):
        path=self.root/'choice-inputs'/(self.goal['metadataDigest']+'.json');path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(GoalContractError,'CHOICE_ARTIFACT_CHANGED'):self.plan()
        self.inputs['expiresUTC']=self.inputs['createdUTC'];self.goal['metadataDigest']=self.save('choice-inputs',self.inputs)
        with self.assertRaisesRegex(GoalContractError,'CHOICE_METADATA_STALE'):self.plan()
    def test_missing_metadata_is_not_empty_peer_evidence(self):
        self.inputs['records']['held'].pop('1.0.0');self.goal['metadataDigest']=self.save('choice-inputs',self.inputs)
        with self.assertRaisesRegex(GoalContractError,'COUPLED_MODEL_UNSUPPORTED'):self.plan()
    def test_child_assignment_checks_and_stripped_policy_rejected(self):
        intent=self.plan()['derivedExactIntent']
        for field,value in [('exactTargets',{'engine':'7.0.0','plugin':'5.0.0'}),('requiredChecks',['install']),('immutablePackages',[])]:
            changed=copy.deepcopy(intent);changed['goal'][field]=value
            changed['binding']=bind_goal(changed['goal'],changed['binding']['identity'])
            with self.assertRaisesRegex(GoalContractError,'PARENT_TO_EXACT_LINEAGE_MISMATCH'):self.validate(changed)
        # Dropping the wrapper cannot preserve the original child policy binding.
        from deploom_mcp.goal_contract import assert_current_binding
        with self.assertRaises(GoalContractError):
            assert_current_binding(intent['binding'],{**intent['goal'],'policyDigest':canonical_digest(self.policy)},intent['binding']['identity'])
    def test_mutated_receipt_status_assignment_and_parent_rejected(self):
        intent=self.plan()['derivedExactIntent'];digest=intent['selectionDigest']
        original=json.loads((self.root/'choice-results'/(digest+'.json')).read_text(encoding='utf-8'))
        for mutation in [lambda r:r.update(status='unknown'),lambda r:r['assignment'].update(held='2.0.0'),
                         lambda r:r['parentBinding']['goal']['objective'].update(maxMajorChanges=2)]:
            receipt=copy.deepcopy(original);mutation(receipt);changed=copy.deepcopy(intent)
            changed['policy']['coupledChoice']['selectionDigest']=self.save('choice-results',receipt)
            with self.assertRaises(GoalContractError):self.validate(changed)
    def test_unknown_solver_produces_no_intent_or_receipt(self):
        # Imports actual module only after pinned Core loading.
        from deploom_mcp.goal_choice_lineage import build_model
        build_model(self.goal,self.current,self.inputs,self.project)
        from peer_solver_model import ExactSolveResult
        with patch('peer_solver_goal.solve_goal_choice',return_value=ExactSolveResult('z3','unknown')):
            with self.assertRaisesRegex(GoalContractError,'COUPLED_SELECTION_NOT_OPTIMAL'):self.plan()
        self.assertFalse((self.root/'choice-results').exists())
    def test_central_registry_calls_lineage_before_authorizing(self):
        intent=self.plan()['derivedExactIntent'];goal=intent['goal'];binding=intent['binding']
        profile={'commands':['npm test'],'requiredChecks':goal['requiredChecks'],'projectChecks':'strict','verificationPurpose':'intermediate-candidate'}
        entry={'runId':'fixture','candidateId':'candidate','projectPath':str(self.project),'goal':goal,'binding':binding,
            'profile':profile,'policy':intent['policy'],'status':'OPEN',
            'deadlineUTC':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),
            'authorization':{'operation':OPERATION,'bindingDigest':binding['bindingDigest'],
                'profileDigest':canonical_digest(profile),'targetMapDigest':canonical_digest(goal['exactTargets'])}}
        path=self.root/'registry.json';raw=json.dumps({'schemaVersion':1,'entries':[entry]}).encode();path.write_bytes(raw)
        env={'DEPLOOM_MCP_EXECUTION_MANIFEST':str(path),'DEPLOOM_MCP_EXECUTION_MANIFEST_SHA256':hashlib.sha256(raw).hexdigest()}
        with patch.dict(os.environ,env):
            reg=load_host_registration('fixture','candidate',binding['bindingDigest'],OPERATION)
            self.assertEqual(reg.goal,goal)
            receipt=self.root/'choice-results'/(intent['selectionDigest']+'.json');receipt.write_bytes(receipt.read_bytes()+b' ')
            with self.assertRaisesRegex(GoalContractError,'CHOICE_ARTIFACT_CHANGED'):
                load_host_registration('fixture','candidate',binding['bindingDigest'],OPERATION)
    def test_null_lineage_cannot_claim_coupled_admission(self):
        intent=self.plan()['derivedExactIntent']
        intent['policy']['coupledChoice']=None
        with self.assertRaisesRegex(GoalContractError,'INVALID_COUPLED_LINEAGE'):self.validate(intent)
    def test_exact_route_does_not_import_or_call_choice_solver(self):
        from deploom_mcp.goal_worker import prepare_goal_plan
        intent=self.plan()['derivedExactIntent']
        with patch('peer_solver_goal.solve_goal_choice',side_effect=AssertionError('exact invoked solver')):
            self.assertEqual(prepare_goal_plan(self.project,intent['goal'])['modelStatus'],'NOT_EVALUATED')

    def test_source_repair_scope_is_preserved_without_authorizing_checks(self):
        self.goal.update(allowSourceRepair=True,sourceRepairAllowlist=[{'path':'src/adapter.js','beforeSha256':'a'*64}])
        intent=self.plan()['derivedExactIntent']
        self.assertEqual(intent['goal']['schemaVersion'],2)
        self.assertEqual(intent['goal']['sourceRepairAllowlist'],self.goal['sourceRepairAllowlist'])
        self.validate(intent)
    def test_worker_protocol_rejects_promoted_or_retargeted_child(self):
        from deploom_mcp.goal_choice_contract import validate_choice_response
        result=self.plan();validate_choice_response(result,validate_goal(self.goal))
        for mutate in [lambda r:r['derivedExactIntent'].update(executionReady=True),
                       lambda r:r['derivedExactIntent']['policy']['coupledChoice'].update(parentGoalDigest='0'*64),
                       lambda r:r.update(runtimeStatus='PASS')]:
            changed=copy.deepcopy(result);mutate(changed)
            with self.assertRaises(GoalContractError):validate_choice_response(changed,validate_goal(self.goal))

    def test_missing_issuance_rejects_matching_selection_json(self):
        intent=self.plan()['derivedExactIntent']
        (self.root/'choice-issuance'/(intent['selectionDigest']+'.json')).unlink()
        with self.assertRaisesRegex(GoalContractError,'CORE_SELECTION_ISSUANCE_MISSING'):self.validate(intent)
    def test_mismatched_actual_core_issuance_is_not_optimal_provenance(self):
        intent=self.plan()['derivedExactIntent'];path=self.root/'choice-issuance'/(intent['selectionDigest']+'.json')
        value=json.loads(path.read_bytes());value['solverResult']['status']='unknown'
        path.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaisesRegex(GoalContractError,'CORE_SELECTION_ISSUANCE_INVALID'):self.validate(intent)
    def test_repeated_selection_keeps_original_host_invocation_issuance(self):
        first=self.plan()['derivedExactIntent'];second=self.plan()['derivedExactIntent']
        self.assertEqual(first,second)
        self.validate(second)

    def test_named_profile_rebinds_policy_identity_without_changing_checks(self):
        from deploom_mcp.goal_check_bindings import initial_verification_context,validate_verification_context,admission_scope_digest
        from deploom_mcp.goal_choice_contract import validate_choice_response
        identity=self.plan()['binding']['identity']
        commands=['node check.js']
        checks=sorted(self.goal['requiredChecks'])
        profile={'schemaVersion':2,'commands':commands,'requiredChecks':checks,'projectChecks':'strict','verificationPurpose':'intermediate-candidate',
            'checkBindings':[{'checkId':checks[0],'kind':'command','commandIndex':1,'commandSha256':hashlib.sha256(commands[0].encode()).hexdigest()},
                *[{'checkId':name,'kind':'core','evidence':'SOURCE_LOCK_CONTINUITY'} for name in checks[1:]]]}
        profile['admission']={'schemaVersion':1,'scope':'ISOLATED_NPM_V3_CANDIDATE',
            'allowedChangeScopeDigest':admission_scope_digest(self.goal),'optionalAbsenceRules':[]}
        for row in profile['checkBindings']:
            command=row['kind']=='command'
            row['normative']={'schemaVersion':1,'purpose':{'goalRequirementId':row['checkId'],'subjectPackages':self.goal['mutablePackages']},
                'expectedOutcome':{'kind':'PASS','exitCode':0} if command else {'kind':'OWNER_OBSERVED','evidence':row['evidence']},
                'allowedInputs':{'source':'SEALED_PREPARED_SOURCE','dependencies':'APPROVED_PHYSICAL_TREE',
                    'oracle':'SEALED_PROJECT_COMMAND' if command else 'CORE_OWNER','externalInputs':'NONE_DECLARED_BY_HOST'}}
        self.goal['verificationContext']=initial_verification_context(profile,identity)
        digest=self.save('choice-profiles',profile)
        self.assertEqual(digest,self.goal['verificationContext']['profileDigest'])
        result=self.plan();intent=result['derivedExactIntent']
        expected=copy.deepcopy(profile)
        expected['admission']['allowedChangeScopeDigest']=admission_scope_digest(intent['goal'])
        self.assertEqual(expected,intent['executionProfile'])
        self.assertNotEqual(profile['admission']['allowedChangeScopeDigest'],expected['admission']['allowedChangeScopeDigest'])
        self.assertNotEqual(self.goal['verificationContext']['baselineLineageDigest'],intent['goal']['verificationContext']['baselineLineageDigest'])
        validate_verification_context(intent['goal'],intent['executionProfile'],intent['binding']['identity'])
        validate_choice_response(result,validate_goal(self.goal));self.validate(intent)
        changed=copy.deepcopy(intent);changed['executionProfile']['commands']=['node skip.js']
        with self.assertRaises(GoalContractError):self.validate(changed)
        changed=copy.deepcopy(result);changed['derivedExactIntent']['executionProfile']['commands']=['node skip.js']
        with self.assertRaises(GoalContractError):validate_choice_response(changed,validate_goal(self.goal))

    def test_all_source_roots_can_be_prerelease_but_targets_remain_stable(self):
        values={'engine':'6.4.5-alpha.0','plugin':'4.3.4-alpha.0','held':'1.0.0-alpha.23'}
        self.current.update(values)
        manifest=json.loads((self.project/'package.json').read_bytes());manifest['dependencies']=dict(values)
        lock=json.loads((self.project/'package-lock.json').read_bytes());lock['packages']['']=manifest
        for name,version in values.items():lock['packages']['node_modules/'+name]['version']=version
        (self.project/'package.json').write_text(json.dumps(manifest),encoding='utf-8')
        (self.project/'package-lock.json').write_text(json.dumps(lock),encoding='utf-8')
        self.inputs['records']['held']={'1.0.0-alpha.23':{'name':'held','version':'1.0.0-alpha.23'}}
        self.goal['metadataDigest']=self.save('choice-inputs',self.inputs)
        result=self.plan();self.assertEqual(result['assignment'],{'engine':'7.0.0','plugin':'4.5.2','held':'1.0.0-alpha.23'})
        self.validate(result['derivedExactIntent'])
        changed=copy.deepcopy(self.goal);changed['candidateDomains']['engine']=['7.0.0-beta.1']
        with self.assertRaises(GoalContractError):validate_goal(changed)
    def test_source_observer_covers_held_lock_identity_without_goal_authority(self):
        from deploom_mcp.goal_worker import observe_goal_source
        before=observe_goal_source(self.project,self.goal['policyDigest'])
        self.assertNotIn('binding',before);self.assertNotIn('authority',before)
        lock=json.loads((self.project/'package-lock.json').read_bytes());lock['packages']['node_modules/held']['version']='1.0.0-alpha.23'
        (self.project/'package-lock.json').write_text(json.dumps(lock),encoding='utf-8')
        after=observe_goal_source(self.project,self.goal['policyDigest'])
        self.assertNotEqual(before['identity']['lockDigest'],after['identity']['lockDigest'])
        self.assertNotEqual(before['identity']['sourceDigest'],after['identity']['sourceDigest'])

    def test_source_change_after_actual_solver_cannot_issue_selection(self):
        from deploom_mcp.goal_choice_lineage import build_model
        build_model(self.goal,self.current,self.inputs,self.project)
        import peer_solver_goal
        actual=peer_solver_goal.solve_goal_choice
        def change_after_solve(*args,**kwargs):
            result=actual(*args,**kwargs)
            lock=json.loads((self.project/'package-lock.json').read_bytes())
            lock['packages']['node_modules/held']['version']='1.0.0-alpha.23'
            (self.project/'package-lock.json').write_text(json.dumps(lock),encoding='utf-8')
            return result
        with patch('peer_solver_goal.solve_goal_choice',side_effect=change_after_solve):
            with self.assertRaisesRegex(GoalContractError,'CHOICE_CONTEXT_CHANGED'):self.plan()
        self.assertFalse((self.root/'choice-results').exists())
        self.assertFalse((self.root/'choice-issuance').exists())
    def test_different_held_prerelease_singleton_is_rejected_by_core(self):
        from deploom_mcp.goal_choice_lineage import build_model
        build_model(self.goal,self.current,self.inputs,self.project)
        from peer_solver_goal import build_goal_choice_model,GoalChoiceUnsupported
        current={**self.current,'held':'1.0.0-alpha.23'}
        domains={**self.goal['candidateDomains'],'held':['1.0.0-alpha.24']}
        with self.assertRaisesRegex(GoalChoiceUnsupported,'INVALID_CHOICE_DOMAIN'):
            build_goal_choice_model(current,domains,self.inputs['records'],node_version='24.18.0',
                project_dir=str(self.project),primary_package='engine',max_major_changes=1)

if __name__=='__main__':unittest.main()
