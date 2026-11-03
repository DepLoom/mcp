"""Real Core snapshot/private apply source-byte guards; no mock verification PASS."""
import copy,dataclasses,hashlib,os,unittest
from unittest.mock import patch
import test_goal_delta_trial as fixtures
from deploom_mcp.goal_contract import validate_goal,bind_goal,GoalContractError
from deploom_mcp.goal_execution_registry import load_host_registration,OPERATION
from deploom_mcp.goal_worker import prepare_goal_plan
import deploom_mcp.goal_delta_trial as trial

class V2SourceRepairTests(unittest.TestCase):
    setUp=fixtures.HostDeltaTrialTests.setUp
    publish=fixtures.HostDeltaTrialTests.publish
    grant=fixtures.HostDeltaTrialTests.grant
    def v2(self):
        self.before_source=(self.project/'index.js').read_bytes()
        self.after_source=b"module.exports = 'authorized v2 source repair'\n"
        self.goal={**self.goal,'schemaVersion':2,'allowSourceRepair':True,
            'sourceRepairAllowlist':[{'path':'index.js','beforeSha256':hashlib.sha256(self.before_source).hexdigest()}]}
        self.binding=prepare_goal_plan(self.project,self.goal)['binding']
        self.entry.update(goal=self.goal,binding=self.binding)
        self.entry['authorization']['bindingDigest']=self.binding['bindingDigest']
        self.publish()
        with patch.dict(os.environ,self.env):
            self.registration=load_host_registration('delta-control','candidate-1',self.binding['bindingDigest'],OPERATION,state_root=self.state)
    def propose(self,sources=None):
        return trial.propose_exact_delta(self.registration,self.snapshot,self.after_manifest,self.after_lock,
            {'index.js':self.after_source} if sources is None else sources)
    def test_v2_applies_exact_approved_source_bytes_in_private_copy_only(self):
        self.v2();proposal=self.propose();grant=self.grant(proposal)
        seen=[]
        def inspect(project,*_):
            seen.append((project/'index.js').read_bytes())
            return {'fullAcceptance':False}
        result=trial._apply_authorized_private_trial(grant,proposal,self.snapshot,_verification_action=inspect)
        self.assertEqual(seen,[self.after_source]);self.assertFalse(result['fullAcceptance'])
        self.assertEqual(result['exactFileDelta'],['index.js','package-lock.json','package.json'])
        self.assertEqual((self.project/'index.js').read_bytes(),self.before_source)
        self.assertEqual(list((self.state/'delta-trial-workspaces').iterdir()),[])
    def test_v1_cannot_smuggle_source_bytes(self):
        with self.assertRaisesRegex(GoalContractError,'SOURCE_REPAIR_SCOPE_MISMATCH'):
            trial.propose_exact_delta(self.registration,self.snapshot,self.after_manifest,self.after_lock,{'index.js':b'bad'})
    def test_v2_requires_exact_allowlist_and_changed_bytes(self):
        self.v2()
        for sources in ({},{'other.js':b'bad'},{'index.js':self.after_source,'other.js':b'bad'}):
            with self.assertRaisesRegex(GoalContractError,'SOURCE_REPAIR_SCOPE_MISMATCH'):self.propose(sources)
        with self.assertRaisesRegex(GoalContractError,'SOURCE_REPAIR_NO_CHANGE'):self.propose({'index.js':self.before_source})
    def test_wrong_baseline_hash_rejected(self):
        self.v2();wrong={**self.goal,'sourceRepairAllowlist':[{'path':'index.js','beforeSha256':'0'*64}]}
        reg=dataclasses.replace(self.registration,goal=wrong,binding=bind_goal(wrong,self.binding['identity']))
        with self.assertRaisesRegex(GoalContractError,'SOURCE_REPAIR_BASELINE_MISMATCH'):
            trial.propose_exact_delta(reg,self.snapshot,self.after_manifest,self.after_lock,{'index.js':self.after_source})
    def test_after_bytes_are_bound_to_candidate_and_grant(self):
        self.v2();proposal=self.propose();grant=self.grant(proposal)
        other=self.propose({'index.js':self.after_source+b' '})
        self.assertNotEqual(proposal.candidate_digest,other.candidate_digest)
        self.assertNotEqual(proposal.delta_digest,other.delta_digest)
        with self.assertRaisesRegex(GoalContractError,'GRANT_CANDIDATE_MISMATCH'):
            trial.apply_authorized_private_trial(grant,other,self.snapshot)
    def test_v2_before_hash_rebinds_goal_and_identity(self):
        self.v2();changed=copy.deepcopy(self.goal);changed['sourceRepairAllowlist'][0]['beforeSha256']='0'*64
        self.assertNotEqual(self.binding['bindingDigest'],bind_goal(changed,self.binding['identity'])['bindingDigest'])
    def test_unsafe_and_windows_ambiguous_paths_fail_before_execution(self):
        self.v2()
        for path in ['../bin.js','/bin.js','a//b.js','a\\b.js','C:/bin.js','.GIT/config','NODE_MODULES/a.js','package.json','Package-lock.json','bin.js.','con.js','a/NUL']:
            goal={**self.goal,'sourceRepairAllowlist':[{'path':path,'beforeSha256':'0'*64}]}
            with self.subTest(path=path),self.assertRaises(GoalContractError):validate_goal(goal)
        goal={**self.goal,'sourceRepairAllowlist':[{'path':'bin.js','beforeSha256':'0'*64},{'path':'BIN.js','beforeSha256':'0'*64}]}
        with self.assertRaisesRegex(GoalContractError,'DUPLICATE_SOURCE_REPAIR_PATH'):validate_goal(goal)
    def test_v1_still_rejects_repair_contract(self):
        self.v2();goal={**self.goal,'schemaVersion':1}
        with self.assertRaisesRegex(GoalContractError,'INVALID_GOAL'):validate_goal(goal)

if __name__=='__main__':unittest.main()
