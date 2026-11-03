"""Typed publication identity regressions; these predicates do not verify projects."""
import copy
from types import SimpleNamespace
import unittest
from deploom_mcp.goal_contract import canonical_digest,GoalContractError
from deploom_mcp.goal_final_checkpoint import _typed_pointer,_publication_target

class PublicationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.context=SimpleNamespace(attempt_id='attempt-second',registration=SimpleNamespace(
            run_id='run-second',candidate_id='candidate-second',binding={'goalDigest':'g'*64}))
        self.key=canonical_digest({'runId':'run-second','candidateId':'candidate-second','attemptId':'attempt-second'})
        self.parent={'schemaVersion':1,'checkpointId':'a'*64,'checkpointDigest':'b'*64,'generation':2,'attemptKey':'c'*64}
        self.cp={'schemaVersion':1,'checkpointId':'d'*64,'attemptId':'attempt-second','attemptKey':self.key,'goalDigest':'g'*64,'parentCheckpointId':'a'*64}
        self.target={'schemaVersion':1,'checkpointId':'d'*64,'checkpointDigest':canonical_digest(self.cp),'generation':3,'attemptKey':self.key}
        self.record={'attemptKey':self.key,'attemptId':'attempt-second','runId':'run-second','candidateId':'candidate-second','incumbent':self.parent,'publicationTarget':self.target,'checkpointPayload':self.cp}
    def check(self):return _publication_target(self.context,self.record,'d'*64,self.cp)
    def test_exact_target_and_parent_generation_are_reconstructed(self):
        self.assertEqual(self.check(),self.target)
        self.record['incumbent']=None;self.cp['parentCheckpointId']=None
        self.record['publicationTarget']={**self.target,'generation':1,'checkpointDigest':canonical_digest(self.cp)}
        self.assertEqual(self.check()['generation'],1)
    def test_same_checkpoint_id_and_digest_do_not_identify_generation_or_attempt(self):
        for field,value in [('generation',100),('attemptKey','e'*64)]:
            changed={**self.target,field:value}
            self.assertTrue(_typed_pointer(changed));self.assertNotEqual(changed,self.check())
            self.record['publicationTarget']=changed
            with self.subTest(field=field),self.assertRaisesRegex(GoalContractError,'PUBLICATION_IDENTITY'):self.check()
            self.record['publicationTarget']=self.target
    def test_boolean_schema_generation_and_untyped_extra_fields_are_not_identity(self):
        for field,value in [('schemaVersion',True),('generation',True),('generation',0),('attemptKey','not-sha'),('extra',1)]:
            with self.subTest(field=field,value=value):self.assertFalse(_typed_pointer({**self.target,field:value}))
    def test_previous_checkpoint_cannot_be_reattributed_to_current_candidate(self):
        for container,field,value in [(self.record,'candidateId','previous-candidate'),(self.record,'attemptId','previous-attempt'),(self.cp,'attemptKey','f'*64),(self.cp,'parentCheckpointId','f'*64),(self.cp,'goalDigest','previous-goal')]:
            before=container[field];container[field]=value
            with self.subTest(field=field),self.assertRaisesRegex(GoalContractError,'PUBLICATION_IDENTITY'):self.check()
            container[field]=before
    def test_frozen_publication_payload_and_typed_parent_required(self):
        self.record['checkpointPayload']={**self.cp,'attemptId':'other'}
        with self.assertRaises(GoalContractError):self.check()
        self.record['checkpointPayload']=self.cp;self.parent['generation']=True
        with self.assertRaises(GoalContractError):self.check()
    def test_another_valid_incumbent_remains_distinct_from_historical_checkpoint(self):
        other={**self.target,'checkpointId':'e'*64,'checkpointDigest':'f'*64,'generation':4,'attemptKey':'1'*64}
        self.assertTrue(_typed_pointer(other));self.assertNotEqual(other,self.check())

class PreviousIncumbentOwnerTests(unittest.TestCase):
    """Metadata joins only; mock snapshot owner is never a verification PASS."""
    def setUp(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.state=self.root/'state';(self.state/'checkpoints').mkdir(parents=True)
        self.previous=self.root/'final-apply-attempts'/('a'*64);self.previous.mkdir(parents=True)
        self.cp={'schemaVersion':1,'kind':'GOAL_FINAL_CHECKPOINT_V1','status':'VERIFIED_SELECTED_PROFILE',
            'checkpointId':'b'*64,'attemptKey':'a'*64,'authorizationDigest':'c'*64,'sourceSnapshotKey':'sealed-source'}
        self.pointer={'schemaVersion':1,'generation':1,'attemptKey':'a'*64,'checkpointId':'b'*64,'checkpointDigest':canonical_digest(self.cp)}
        self.record={'phase':'TERMINAL_PASS','attemptKey':'a'*64,'checkpointId':'b'*64,'checkpointDigest':canonical_digest(self.cp),
            'publicationTarget':self.pointer,'checkpointPayload':self.cp,'authorizationDigest':'c'*64,'liveObservation':{'sourceSnapshotKey':'sealed-source'}}
        self.core=Mock();self.save()
    def save(self):
        import json
        (self.state/'checkpoints'/('b'*64+'.json')).write_text(json.dumps(self.cp),encoding='utf-8')
        (self.previous/'transaction.json').write_text(json.dumps({'payload':self.record,'sha256':canonical_digest(self.record)}),encoding='utf-8')
    def validate(self):
        from deploom_mcp.goal_final_checkpoint import validate_existing_incumbent
        return validate_existing_incumbent(self.pointer,self.state,self.root,self.core)
    def test_separate_terminal_owner_and_durable_snapshot_are_consulted(self):
        self.validate();self.core.open_source_snapshot.assert_called_once_with(self.previous/'verified-source',expected_key='sealed-source',timeout_seconds=20)
        self.core.validate_source_snapshot.assert_called_once()
    def test_new_journal_cannot_invent_old_generation(self):
        self.pointer={**self.pointer,'generation':100}
        with self.assertRaisesRegex(GoalContractError,'PREVIOUS_INCUMBENT'):self.validate()
        self.core.open_source_snapshot.assert_not_called()
    def test_pending_pointer_owner_is_not_a_verified_previous_checkpoint(self):
        self.record['phase']='POINTER_COMMITTED';self.save()
        with self.assertRaisesRegex(GoalContractError,'PREVIOUS_INCUMBENT'):self.validate()
        self.core.open_source_snapshot.assert_not_called()
    def test_missing_durable_source_propagates_failure_not_acceptance(self):
        self.core.open_source_snapshot.side_effect=OSError('missing owned snapshot')
        with self.assertRaises(OSError):self.validate()
    def test_previous_snapshot_source_key_and_capability_must_join(self):
        self.record['authorizationDigest']='f'*64;self.save()
        with self.assertRaisesRegex(GoalContractError,'PREVIOUS_INCUMBENT'):self.validate()

if __name__=='__main__':unittest.main()
