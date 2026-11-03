"""Pure authority-boundary regressions; synthetic rows are not native evidence."""
import copy
import hashlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from deploom_mcp.goal_contract import canonical_digest, GoalContractError
from deploom_mcp.goal_workspace_observation import workspace_profile_complete
from deploom_mcp.goal_final_checkpoint import live_complete
from deploom_mcp.goal_frozen_candidate import _verify_raw

class SupervisionAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.project=Path('synthetic-authority-fixture').absolute()
        self.profile={'commands':['npm test'],'checkBindings':[]}
        self.key='a'*32; self.plan={'approvedLockSha256':'b'*64,'obligationPlanDigest':'c'*64,'instanceCount':0}
        origin={'inputSourceSnapshotKey':self.key,'scope':'CURRENT_HOST_WORKSPACE',
            'workspacePathDigest':canonical_digest(str(self.project)),'resolvedLockfileHash':'b'*64}
        tree={'sourceOriginObservationDigest':canonical_digest(origin),'approvedLockSha256':'b'*64,
            'obligationPlanDigest':'c'*64,'resolvedStateKey':'d'*64,'unknownCount':0,'violations':[],
            'materializedTreeObservationDigest':'e'*64,'status':'MATERIALIZED_NPM_INSTANCE_OBLIGATIONS_OBSERVED',
            'authority':'NON_REUSABLE_CORE_OBSERVATION','fullAcceptance':False,'instanceCount':0}
        row={'commandIndex':1,'commandDigest':hashlib.sha256(b'npm test').hexdigest(),
            'origin':'EXECUTED_FRESH','status':'PASS','returnCode':0,'supervision':'guaranteed-tree',
            'commandBoundaryGuard':'PRE_POST_OBSERVED','freshCommandInvocationId':'unit-only',
            'finalVerificationProfileDigest':canonical_digest(self.profile),'sourceSnapshotKey':self.key,
            'resolvedStateKey':'d'*64,'obligationPlanDigest':'c'*64,'materializedTreeObservationDigest':'e'*64}
        operations=[{'operation':kind,'commandIndex':index,'returnCode':0,'supervision':'guaranteed-tree',
            'descendantsRemaining':0,'timedOut':False,'cancelled':False}
            for kind,index in [('strict-frozen-install',None),('project-command',1)]]
        self.raw={'schemaVersion':1,'freshCommandInvocationId':'unit-only',
            'status':'CURRENT_WORKSPACE_PROFILE_OBSERVED','executionScope':'REGISTERED_CURRENT_WORKSPACE',
            'authority':'NON_REUSABLE_CORE_CURRENT_WORKSPACE_OBSERVATION','sourceSnapshotKey':self.key,
            'workspacePathDigest':canonical_digest(str(self.project)),
            'finalVerificationProfileDigest':canonical_digest(self.profile),'ownedCleanupConfirmed':True,
            'sourceSnapshotCleanupClosed':True,'finalBoundaryObserved':True,'fullAcceptance':False,
            'checkpointPublished':False,'currentWorkspaceVerified':False,'materializedSourceOrigin':origin,
            'deliveredSourceObservationDigest':canonical_digest(origin),'materializedDependencyObligations':tree,
            'resolvedLockfileHash':'b'*64,'resolvedStateKey':'d'*64,'liveMaterializedTreeObservationDigest':'e'*64,
            'commandObservations':[row],'ownerOperationObservations':operations,
            'installation':{'status':'FRESH_COLD_FROZEN_INSTALL_OBSERVED','scriptsEnabled':False}}
        self.registration=SimpleNamespace(policy={},profile=self.profile)
        self.context=SimpleNamespace(registration=self.registration,final_profile=self.profile,
            accepted_receipt={'verificationObservation':{'candidateVerifiedSourceSnapshotKey':self.key}})
    def test_best_effort_command_never_satisfies_private_or_final_profile(self):
        self.assertTrue(workspace_profile_complete(self.profile,self.key,self.raw,self.plan,self.project))
        self.assertTrue(live_complete(self.context,self.raw,self.plan,self.project))
        _verify_raw(self.raw,self.registration,self.key,self.plan,self.project)
        for quality in ('best-effort','unknown',''):
            raw=copy.deepcopy(self.raw);raw['commandObservations'][0]['supervision']=quality
            with self.subTest(quality=quality):
                self.assertFalse(workspace_profile_complete(self.profile,self.key,raw,self.plan,self.project))
                self.assertFalse(live_complete(self.context,raw,self.plan,self.project))
                with self.assertRaisesRegex(GoalContractError,'FROZEN_CORE_OBSERVATION_INCOMPLETE'):
                    _verify_raw(raw,self.registration,self.key,self.plan,self.project)
    def test_best_effort_install_operation_cannot_be_private_accepted(self):
        raw=copy.deepcopy(self.raw);raw['ownerOperationObservations'][0]['supervision']='best-effort'
        with self.assertRaisesRegex(GoalContractError,'FROZEN_OPERATION_CLOSURE_UNCONFIRMED'):
            _verify_raw(raw,self.registration,self.key,self.plan,self.project)

if __name__=='__main__':unittest.main()
