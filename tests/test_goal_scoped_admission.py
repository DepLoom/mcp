"""Normative binding and physical event controls; doubles never prove project PASS."""
import copy,dataclasses,hashlib,unittest
from types import SimpleNamespace
import test_goal_check_bindings as mappings
import test_goal_invocation_adapter as invocation
import test_goal_source_origin_observation as origins
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_check_bindings import (admission_scope_digest,validate_check_bindings,
    initial_verification_context,validate_verification_context)
from deploom_mcp.goal_invocation_adapter import InvocationObservationCollector
from verification_invocation import run_observed,observe_command,defer_workspace_cleanup


def normative_profile(profile,goal):
    result=copy.deepcopy(profile)
    result['admission']={'schemaVersion':1,'scope':'ISOLATED_NPM_V2_CANDIDATE',
        'allowedChangeScopeDigest':admission_scope_digest(goal),'optionalAbsenceRules':[]}
    for row in result['checkBindings']:
        command=row['kind']=='command';oracle=row.get('oracle',{})
        row['normative']={'schemaVersion':1,'purpose':{'goalRequirementId':row['checkId'],'subjectPackages':list(goal['exactTargets'])},
            'expectedOutcome':{'kind':'PASS','exitCode':0} if command else {'kind':'OWNER_OBSERVED','evidence':row['evidence']},
            'allowedInputs':{'source':'SEALED_PREPARED_SOURCE','dependencies':'APPROVED_PHYSICAL_TREE',
                'oracle':'PINNED_INLINE_BODY' if oracle.get('mode')=='INLINE_NODE_V1' else 'SEALED_PROJECT_COMMAND' if command else 'CORE_OWNER',
                'externalInputs':'NONE_DECLARED_BY_HOST'}}
    return result

class NormativeProfileTests(unittest.TestCase):
    def setUp(self):
        invocation.InvocationCollectorTests.setUp(self)
        self.profile=normative_profile(mappings.mapped(self.registration.profile,self.registration.goal['requiredChecks']),self.registration.goal)
    def test_full_profile_context_has_no_scope_digest_cycle(self):
        goal=copy.deepcopy(self.registration.goal);goal['verificationContext']=initial_verification_context(self.profile,self.registration.binding['identity'])
        self.assertEqual(admission_scope_digest(goal),self.profile['admission']['allowedChangeScopeDigest'])
        validate_check_bindings(self.profile,goal['requiredChecks']);validate_verification_context(goal,self.profile,self.registration.binding['identity'])
    def test_required_normative_contract_cannot_be_missing(self):
        self.profile['checkBindings'][0].pop('normative')
        with self.assertRaisesRegex(GoalContractError,'INVALID_NORMATIVE_CHECK_CONTRACT'):validate_check_bindings(self.profile,self.registration.goal['requiredChecks'])
    def test_normative_false_zero_and_wrong_requirement_are_rejected(self):
        for change in ['boolean','wrong-requirement']:
            profile=copy.deepcopy(self.profile);row=profile['checkBindings'][0]
            if change=='boolean':row['normative']['expectedOutcome']['exitCode']=False
            else:row['normative']['purpose']['goalRequirementId']='unrelated'
            with self.subTest(change=change),self.assertRaises(GoalContractError):validate_check_bindings(profile,self.registration.goal['requiredChecks'])
    def test_changed_always_zero_command_cannot_inherit_old_context(self):
        goal=copy.deepcopy(self.registration.goal);goal['verificationContext']=initial_verification_context(self.profile,self.registration.binding['identity'])
        self.profile['commands'][0]='node -e "process.exit(0)"';self.profile['checkBindings'][0]['commandSha256']=hashlib.sha256(self.profile['commands'][0].encode()).hexdigest()
        with self.assertRaisesRegex(GoalContractError,'GOAL_VERIFICATION_CONTEXT_MISMATCH'):validate_verification_context(goal,self.profile,self.registration.binding['identity'])
    def test_host_file_identity_alone_cannot_be_a_normative_executed_oracle(self):
        self.profile['checkBindings'][0]['oracle']={'hostPath':'runtime.cjs','sha256':'a'*64}
        with self.assertRaisesRegex(GoalContractError,'NORMATIVE_CHECK_INPUT_SCOPE_UNSUPPORTED'):validate_check_bindings(self.profile,self.registration.goal['requiredChecks'])
    def test_changed_goal_cannot_retain_an_old_scope_digest(self):
        goal=copy.deepcopy(self.registration.goal);goal['goalId']='different-goal';goal['verificationContext']=initial_verification_context(self.profile,self.registration.binding['identity'])
        with self.assertRaisesRegex(GoalContractError,'SCOPED_ADMISSION_GOAL_BINDING_MISMATCH'):validate_verification_context(goal,self.profile,self.registration.binding['identity'])
    def test_legacy_profile_does_not_silently_gain_admission(self):
        profile=mappings.mapped(self.registration.profile,self.registration.goal['requiredChecks']);validate_check_bindings(profile,self.registration.goal['requiredChecks']);self.assertNotIn('admission',profile)

class PhysicalObservationLinkTests(unittest.TestCase):
    setUp=invocation.InvocationCollectorTests.setUp
    def collect(self,*,missing_boundary=False,wrong_event=False):
        profile=normative_profile(mappings.mapped(self.registration.profile,self.registration.goal['requiredChecks']),self.registration.goal)
        self.registration.profile.update(profile);origin=origins.SourceOriginObservationTests.origin(self)
        tree={'schemaVersion':1,'authority':'NON_REUSABLE_CORE_OBSERVATION','status':'MATERIALIZED_NPM_INSTANCE_OBLIGATIONS_OBSERVED','fullAcceptance':False,
            'finalBoundaryObserved':True,'sourceOriginObservationDigest':canonical_digest(origin),'obligationPlanDigest':'e'*64,
            'resolvedStateKey':'b'*64,'approvedLockSha256':'c'*64,'unknownCount':0,'violations':[],
            'materializedTreeObservationDigest':'f'*64,'commandBoundaryGuards':[{'commandIndex':i,'status':'PRE_POST_OBSERVED','obligationPlanDigest':'e'*64,'materializedTreeObservationDigest':'f'*64} for i in range(1,len(profile['commands'])+1)]}
        if missing_boundary:tree['commandBoundaryGuards'].pop()
        result=dataclasses.replace(self.result,materialized_source_origin=origin,materialized_dependency_obligations=tree)
        collector=InvocationObservationCollector(self.registration,'a'*32,self.fp,engineering_control=True,expected_obligation_plan_digest='e'*64)
        def action():
            defer_workspace_cleanup(lambda:None)
            for i,command in enumerate(profile['commands'],1):
                observe_command(i,command,SimpleNamespace(returncode=0,supervision=SimpleNamespace(quality='guaranteed-tree',descendants_remaining=0)),guarded=True,
                    identities={'sourceSnapshotKey':'a'*32,'targetOnlyAssignmentFingerprint':self.fp,'resolvedStateKey':'b'*64,'resolvedLockfileHash':'c'*64,
                        'sourceOriginObservationDigest':canonical_digest(origin),'preparedSourceContentKey':origin['preparedSourceContentKey'],
                        'dependencyObligationPlanDigest':'e'*64,'materializedTreeObservationDigest':('0'*64 if wrong_event else 'f'*64),'dependencyPrePostGuardStatus':'PRE_POST_OBSERVED'})
            return result
        collector.invoke(lambda observer:run_observed(observer,self.fp,profile['commands'],action));return collector,result
    def test_bound_double_control_stays_non_authoritative(self):
        collector,result=self.collect();record=collector.finalize(result);self.assertTrue(record['observationComplete']);self.assertFalse(record['fullAcceptance']);self.assertFalse(record['namedCheckObservations']['goalChecksComplete'])
        self.assertEqual(record['verificationAuthority'],'TEST_DOUBLE_CONTROL_FLOW_ONLY')
    def test_missing_command_boundary_is_not_a_complete_observation(self):
        collector,result=self.collect(missing_boundary=True)
        with self.assertRaisesRegex(GoalContractError,'DEPENDENCY_OBLIGATION_OBSERVATION_MISMATCH'):collector.finalize(result)
    def test_wrong_command_tree_digest_is_not_fresh_confirmed_evidence(self):
        collector,result=self.collect(wrong_event=True);self.assertFalse(collector.finalize(result)['observationComplete'])
    def test_result_mutation_after_final_owner_event_is_rejected(self):
        collector,result=self.collect();result.materialized_dependency_obligations['unknownCount']=1
        with self.assertRaisesRegex(GoalContractError,'DEPENDENCY_OBLIGATION_OBSERVATION_MISMATCH'):collector.finalize(result)

if __name__=='__main__':unittest.main()
