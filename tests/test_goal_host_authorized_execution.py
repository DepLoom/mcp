"""Normal host permission is distinct from candidate/profile acceptance."""
import copy,dataclasses,json
from pathlib import Path
import unittest
from unittest.mock import patch,AsyncMock
import test_goal_delta_trial as fixtures
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_check_bindings import initial_verification_context
from deploom_mcp.goal_worker import prepare_goal_plan
from deploom_mcp.goal_execution_registry import OPERATION,load_host_registration,require_registered_execution_class
from deploom_mcp.goal_candidate import verify_goal_candidate
import deploom_mcp.goal_delta_trial as trial

class NormalHostPermissionTests(unittest.TestCase):
    publish=fixtures.HostDeltaTrialTests.publish
    proposal=fixtures.HostDeltaTrialTests.proposal
    def setUp(self):
        fixtures.HostDeltaTrialTests.setUp(self)
        commands=['node -e "void '+str(i)+'"' for i,_ in enumerate(self.goal['requiredChecks'])]
        self.profile={'schemaVersion':2,'commands':commands,'requiredChecks':self.goal['requiredChecks'],
            'projectChecks':'strict','verificationPurpose':'intermediate-candidate','checkBindings':[
            {'checkId':name,'kind':'command','commandIndex':i+1,'commandSha256':__import__('hashlib').sha256(commands[i].encode()).hexdigest()}
            for i,name in enumerate(self.goal['requiredChecks'])]}
        self.goal['verificationContext']=initial_verification_context(self.profile,self.binding['identity'])
        self.binding=prepare_goal_plan(self.project,self.goal)['binding']
        self.entry.update(goal=self.goal,binding=self.binding,profile=self.profile,
            candidateFiles={'manifest':'candidate.json','lock':'candidate-lock.json'},
            authorization={'operation':OPERATION,'bindingDigest':self.binding['bindingDigest'],
                'profileDigest':canonical_digest(self.profile),'targetMapDigest':canonical_digest(self.goal['exactTargets'])})
        self.publish()
        with patch.dict('os.environ',self.env):
            self.registration=load_host_registration('delta-control','candidate-1',self.binding['bindingDigest'],OPERATION)
    def normal_grant(self):
        proposal=self.proposal()
        fixtures.HostDeltaTrialTests.grant(self,proposal)
        self.entry['deltaAuthorization']['origin']='PREAPPROVED_HOST_POLICY';self.publish()
        with patch.dict('os.environ',self.env):
            registration=load_host_registration('delta-control','candidate-1',self.binding['bindingDigest'],OPERATION)
            grant=trial.load_host_delta_grant('delta-control','candidate-1',self.binding['bindingDigest'],trial.APPLY_OPERATION,proposal)
        return registration,grant,proposal
    def test_normal_registered_permission_is_not_a_success_claim(self):
        registration,_,_=self.normal_grant()
        self.assertEqual(require_registered_execution_class(registration),'PREAPPROVED_HOST_POLICY')
        self.assertFalse(hasattr(registration,'passed'))
    def test_normal_apply_only_is_denied_before_workspace_or_attempt(self):
        registration,grant,proposal=self.normal_grant()
        with self.assertRaisesRegex(GoalContractError,'HOST_AUTHORIZED_VERIFICATION_ACTION_REQUIRED'):
            trial.apply_authorized_private_trial(grant,proposal,self.snapshot)
        self.assertFalse((self.state/'delta-trial-workspaces').exists())
        self.assertFalse((self.state/'delta-trial-attempts').exists())
    def test_normal_missing_files_or_named_context_is_denied(self):
        registration,_,_=self.normal_grant()
        changes=[('HOST_AUTHORIZED_CANDIDATE_FILES_REQUIRED',{'candidate_files':None}),
            ('HOST_AUTHORIZED_NAMED_PROFILE_REQUIRED',{'profile':{k:v for k,v in self.profile.items() if k not in {'schemaVersion','checkBindings'}}}),
            ('HOST_AUTHORIZED_NAMED_PROFILE_REQUIRED',{'goal':{k:v for k,v in self.goal.items() if k!='verificationContext'}})]
        for code,delta in changes:
            with self.subTest(code=code),self.assertRaisesRegex(GoalContractError,code):
                require_registered_execution_class(dataclasses.replace(registration,**delta))
    def test_loader_rejects_normal_legacy_profile(self):
        self.normal_grant();self.entry['profile']={k:v for k,v in self.profile.items() if k not in {'schemaVersion','checkBindings'}}
        self.entry['goal']={k:v for k,v in self.goal.items() if k!='verificationContext'}
        self.entry['binding']=prepare_goal_plan(self.project,self.entry['goal'])['binding']
        self.entry['authorization'].update(bindingDigest=self.entry['binding']['bindingDigest'],profileDigest=canonical_digest(self.entry['profile']))
        self.publish()
        with patch.dict('os.environ',self.env),self.assertRaisesRegex(GoalContractError,'HOST_AUTHORIZED_NAMED_PROFILE_REQUIRED'):
            load_host_registration('delta-control','candidate-1',self.entry['binding']['bindingDigest'],OPERATION)
    def test_parent_rechecks_pinned_registry_after_child(self):
        import asyncio,hashlib
        from deploom_mcp.process_runner import ProcessRun
        from deploom_mcp.goal_verifier_bridge import trial_ledger_path
        registration,_,_=self.normal_grant()
        with patch.dict('os.environ',self.env):
            receipt=trial_ledger_path(registration);receipt.parent.mkdir();receipt.write_text('{}')
            async def changed_registry(*args,**kwargs):
                self.manifest.write_bytes(self.manifest.read_bytes()+b' ')
                return ProcessRun(0,json.dumps({'code':'HOST_AUTHORIZED_PRIVATE_VERIFICATION_OBSERVED','receiptDigest':canonical_digest({})}),'')
            with patch('deploom_mcp.goal_candidate.run_process_async',new_callable=AsyncMock,side_effect=changed_registry) as child:
                result=asyncio.run(verify_goal_candidate(registration.run_id,registration.candidate_id,registration.binding['bindingDigest'],OPERATION))
        self.assertFalse(result['ok']);self.assertFalse(result['fullAcceptance'])
        self.assertEqual(result['error']['code'],'EXECUTION_REGISTRY_CHANGED');self.assertEqual(child.await_count,1)

    def test_exact_integer_lock_format_rejects_float_or_bool(self):
        lock=json.loads(self.before_lock)
        for version in [2.0,True]:
            lock['lockfileVersion']=version;(self.project/'package-lock.json').write_text(json.dumps(lock))
            with self.assertRaisesRegex(GoalContractError,'UNSUPPORTED_LOCK_FORMAT'):
                prepare_goal_plan(self.project,self.goal)

class NormalParentReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_engineering_stdout_cannot_be_upgraded_to_normal_permission(self):
        from test_goal_candidate import fixture_registration
        from deploom_mcp.process_runner import ProcessRun
        reg=dataclasses.replace(fixture_registration(Path.cwd()),candidate_files={'manifest':'x','lock':'y'},
            delta_authorization={'origin':'PREAPPROVED_HOST_POLICY'})
        process=ProcessRun(0,json.dumps({'code':'ENGINEERING_CONTROLLED_VERIFICATION_OBSERVED','receiptDigest':'0'*64}),'')
        with patch('deploom_mcp.goal_candidate.load_host_registration',return_value=reg),patch('deploom_mcp.goal_candidate.run_process_async',new_callable=AsyncMock,return_value=process):
            result=await verify_goal_candidate(reg.run_id,reg.candidate_id,reg.binding['bindingDigest'],OPERATION)
        self.assertFalse(result['ok']);self.assertFalse(result['fullAcceptance'])

if __name__=='__main__':unittest.main()
