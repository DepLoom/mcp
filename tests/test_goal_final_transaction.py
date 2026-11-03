"""Partial-source recovery invariants; no test double grants workspace authority."""
import copy
import unittest
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_final_transaction import assert_partial_source


class PartialSourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.before={name:{'path':name,'kind':'file','sha256':hash,'size':1,'executable':False}
            for name,hash in [('package.json','a'*64),('package-lock.json','b'*64),('src.cjs','c'*64)]}
        self.files=[{'path':'package.json','beforeSha256':'a'*64,'afterSha256':'d'*64},
            {'path':'package-lock.json','beforeSha256':'b'*64,'afterSha256':'e'*64}]
    def test_same_attempt_allows_preimage_mixed_and_postimage_only(self):
        actual=copy.deepcopy(self.before);assert_partial_source(self.before,actual,self.files)
        for row in self.files:
            actual[row['path']]['sha256']=row['afterSha256']
            assert_partial_source(self.before,actual,self.files)
    def test_foreign_edit_to_planned_file_is_not_overwritten(self):
        actual=copy.deepcopy(self.before);actual['package.json']['sha256']='f'*64
        with self.assertRaisesRegex(GoalContractError,'FOREIGN_OR_STALE'):assert_partial_source(self.before,actual,self.files)
    def test_unlisted_source_edit_is_not_hidden_by_correct_target_bytes(self):
        actual=copy.deepcopy(self.before);actual['src.cjs']['sha256']='f'*64
        with self.assertRaisesRegex(GoalContractError,'FOREIGN_OR_STALE'):assert_partial_source(self.before,actual,self.files)
    def test_extra_missing_and_executable_changes_are_rejected(self):
        for mode in ['extra','missing','executable']:
            actual=copy.deepcopy(self.before)
            if mode=='extra':actual['extra']=actual['src.cjs']
            elif mode=='missing':actual.pop('src.cjs')
            else:actual['package.json']['executable']=True
            with self.subTest(mode=mode),self.assertRaises(GoalContractError):assert_partial_source(self.before,actual,self.files)
    def test_duplicate_or_foreign_file_plan_is_not_a_recovery_scope(self):
        for files in [self.files+self.files[:1],self.files+[{'path':'foreign','beforeSha256':'a'*64,'afterSha256':'b'*64}]]:
            with self.assertRaises(GoalContractError):assert_partial_source(self.before,self.before,files)
