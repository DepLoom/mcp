"""Historical API authority boundaries; mocked records never prove a real project."""
import asyncio,json,unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_historical_checkpoint import HistoricalReadContext
from deploom_mcp.goal_final_authorization import consume_final_grant
from deploom_mcp.goal_final_transaction import FinalApplyTransaction
import deploom_mcp.goal_final_candidate as candidate


class HistoricalReadAuthorityTests(unittest.TestCase):
    def context(self):
        return HistoricalReadContext(b'{}','a'*64,b'{}',b'{}','b'*64,'historical-attempt',60)

    def test_context_is_immutable_and_is_not_an_execution_grant(self):
        context=self.context()
        with self.assertRaises(FrozenInstanceError):context.attempt_id='different'
        for operation in (consume_final_grant,FinalApplyTransaction):
            with self.subTest(operation=operation),self.assertRaisesRegex(GoalContractError,'OPAQUE_FINAL_GRANT_REQUIRED'):
                operation(context)

    def test_decoded_receipt_cannot_mutate_context(self):
        context=self.context();receipt=context.accepted_receipt;receipt['forged']=True
        self.assertEqual(context.accepted_receipt,{})
        self.assertEqual(context.final_profile,{})


class HistoricalAPISelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_and_explicit_current_preserve_exact_canonical_response(self):
        value={'ok':True,'checkpointVerifiedHistorical':True,'currentWorkspaceVerified':False,'fullAcceptance':False}
        with patch('deploom_mcp.goal_execution_registry.load_host_registration',return_value=SimpleNamespace(policy={})), \
             patch.object(candidate,'read_final_checkpoint',return_value=value) as current, \
             patch('deploom_mcp.goal_historical_checkpoint.read_historical_checkpoint') as history:
            self.assertEqual(await candidate.get_goal_checkpoint('r','c','a'*64),value)
            self.assertEqual(await candidate.get_goal_checkpoint('r','c','a'*64,mode='current'),value)
        self.assertEqual(current.call_count,2);history.assert_not_called()

    async def test_historical_uses_separate_reader_without_current_or_process(self):
        value={'ok':True,'status':'HISTORICAL_RECORD_VERIFIED','currentWorkspaceVerified':None,'executionReady':False,'fullAcceptance':False}
        with patch.object(candidate,'read_final_checkpoint') as current, \
             patch.object(candidate,'run_process_async',new=AsyncMock()) as process, \
             patch('deploom_mcp.goal_historical_checkpoint.read_historical_checkpoint',return_value=value) as history:
            self.assertEqual(await candidate.get_goal_checkpoint('r','c','a'*64,mode='historical'),value)
        current.assert_not_called();process.assert_not_called();history.assert_called_once_with('r','c','a'*64)

    async def test_stale_current_never_falls_back_to_history(self):
        with patch('deploom_mcp.goal_execution_registry.load_host_registration',return_value=SimpleNamespace(policy={})), \
             patch.object(candidate,'read_final_checkpoint',side_effect=GoalContractError('CHOICE_METADATA_STALE')), \
             patch('deploom_mcp.goal_historical_checkpoint.read_historical_checkpoint') as history:
            result=await candidate.get_goal_checkpoint('r','c','a'*64)
        self.assertEqual(result,{'ok':False,'error':{'code':'CHOICE_METADATA_STALE'},'fullAcceptance':False})
        history.assert_not_called()

    async def test_invalid_modes_never_select_either_reader(self):
        with patch.object(candidate,'read_final_checkpoint') as current, \
             patch('deploom_mcp.goal_historical_checkpoint.read_historical_checkpoint') as history:
            for mode in ('auto','',True,None,{},[]):
                with self.subTest(mode=mode):
                    result=await candidate.get_goal_checkpoint('r','c','a'*64,mode=mode)
                    self.assertEqual(result['error']['code'],'INVALID_CHECKPOINT_READ_MODE')
        current.assert_not_called();history.assert_not_called()

    async def test_historical_failure_never_leaks_a_success_or_launches_worker(self):
        with patch.object(candidate,'read_final_checkpoint') as current, \
             patch.object(candidate,'run_process_async',new=AsyncMock()) as process, \
             patch('deploom_mcp.goal_historical_checkpoint.read_historical_checkpoint',side_effect=GoalContractError('HISTORICAL_EVIDENCE_INSUFFICIENT')):
            result=await candidate.get_goal_checkpoint('r','c','a'*64,mode='historical')
        self.assertFalse(result['ok']);self.assertFalse(result['fullAcceptance'])
        current.assert_not_called();process.assert_not_called()
class HistoricalTerminalIdentityTests(unittest.TestCase):
    """Owner record joins only; this fixture has no project verification authority."""
    def setUp(self):
        from datetime import datetime,timezone,timedelta
        from deploom_mcp.goal_contract import canonical_digest
        self.digest=canonical_digest
        now=datetime.now(timezone.utc)
        self.entry={'runId':'run','candidateId':'candidate','projectPath':'C:/fixture-only',
            'goal':{},'binding':{'bindingDigest':'a'*64},'profile':{},'policy':{},
            'deltaAuthorization':{},'candidateFiles':{},'finalAuthorization':{},
            'deadlineUTC':(now+timedelta(seconds=120)).isoformat()}
        self.context=HistoricalReadContext(json.dumps(self.entry).encode(),'b'*64,b'{}',b'{}','c'*64,'attempt',60)
        key=canonical_digest({'runId':'run','candidateId':'candidate','attemptId':'attempt'})
        started=now.timestamp()-10
        self.tx={'phase':'TERMINAL_PASS','registryDigest':'b'*64,'authorizationDigest':'c'*64,
            'bindingDigest':'a'*64,'profileDigest':canonical_digest({}),'attemptKey':key,'attemptId':'attempt',
            'runId':'run','candidateId':'candidate','project':'C:/fixture-only','budgetSeconds':60,
            'startedAt':started,'deadline':started+60}

    def check(self):
        from deploom_mcp.goal_historical_checkpoint import validate_terminal_record
        return validate_terminal_record({'schemaVersion':1,'payload':self.tx,'sha256':self.digest(self.tx)},self.context)

    def test_terminal_record_can_be_read_before_its_original_deadline(self):
        self.assertEqual(self.check(),self.tx)

    def test_nonterminal_or_other_registered_attempt_never_becomes_history(self):
        changes={'phase':'LIVE_VERIFIED','registryDigest':'d'*64,'authorizationDigest':'d'*64,
            'bindingDigest':'d'*64,'attemptKey':'d'*64,'attemptId':'other','runId':'other',
            'candidateId':'other','profileDigest':'d'*64,'project':'C:/other','budgetSeconds':59}
        for key,value in changes.items():
            before=self.tx[key];self.tx[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(GoalContractError,'FINAL_COMPLETED_CHECKPOINT_REQUIRED'):
                self.check()
            self.tx[key]=before

    def test_changed_journal_hash_and_bool_schema_reject(self):
        from deploom_mcp.goal_historical_checkpoint import validate_terminal_record
        for schema,digest in [(True,self.digest(self.tx)),(1,'d'*64)]:
            with self.subTest(schema=schema),self.assertRaises(GoalContractError):
                validate_terminal_record({'schemaVersion':schema,'payload':self.tx,'sha256':digest},self.context)

    def test_future_nan_bool_and_modified_deadline_reject(self):
        changes=[('startedAt',True),('startedAt',float('nan')),('startedAt',float('inf')),
            ('startedAt',self.tx['startedAt']+1000),('deadline',self.tx['deadline']+1),('deadline',True)]
        for key,value in changes:
            before=self.tx[key];self.tx[key]=value
            with self.subTest(key=key,value=value),self.assertRaises((GoalContractError,ValueError)):
                self.check()
            self.tx[key]=before
class HistoricalPointerTests(unittest.TestCase):
    """Latest-pointer syntax is not authority for any current generation."""
    def pointer(self):
        return {'schemaVersion':1,'checkpointId':'a'*64,'checkpointDigest':'b'*64,
                'generation':1,'attemptKey':'c'*64}

    def test_latest_same_target_is_syntactically_valid(self):
        from deploom_mcp.goal_historical_checkpoint import validate_historical_pointer
        self.assertIsNone(validate_historical_pointer(self.pointer()))

    def test_typed_successor_does_not_need_a_foreign_owner_or_current_proof(self):
        from pathlib import Path
        from types import SimpleNamespace
        from deploom_mcp.goal_historical_checkpoint import read_historical_checkpoint
        successor={**self.pointer(),'checkpointId':'d'*64,'checkpointDigest':'e'*64,'generation':2,'attemptKey':'f'*64}
        context=SimpleNamespace(attempt_id='attempt',registration=SimpleNamespace(binding={
            'identity':{'implementationDigest':'1'*64,'toolchainDigest':'2'*64}}))
        cp={'checkpointId':'a'*64,'sourceSnapshotKey':'sealed-source'}
        with patch('deploom_mcp.goal_historical_checkpoint.load_historical_context',return_value=(context,None)), \
             patch('deploom_mcp.goal_final_checkpoint.inspect_checkpoint_evidence',return_value=(None,{'phase':'TERMINAL_PASS'},cp,False,None)) as proof, \
             patch('deploom_mcp.goal_final_checkpoint._paths',return_value=(None,None,Path('fixture-state'))), \
             patch('deploom_mcp.goal_historical_checkpoint._read',return_value=successor), \
             patch('deploom_mcp.goal_final_checkpoint.validate_existing_incumbent',side_effect=AssertionError('foreign owner inspected')):
            result=read_historical_checkpoint('r','c','a'*64)
        proof.assert_called_once_with(context)
        self.assertEqual(result['status'],'HISTORICAL_RECORD_VERIFIED')
        self.assertNotIn('isRecordedIncumbent',result)
        self.assertIsNone(result['currentWorkspaceVerified'])
        self.assertFalse(result['executionReady'])

    def test_malformed_pointer_is_not_a_false_incumbent(self):
        from deploom_mcp.goal_historical_checkpoint import validate_historical_pointer
        for value in (None,{}, {**self.pointer(),'generation':True}, {**self.pointer(),'schemaVersion':True},
                      {**self.pointer(),'attemptKey':'invalid'}, {**self.pointer(),'extra':True}):
            with self.subTest(value=value),self.assertRaisesRegex(GoalContractError,'HISTORICAL_LATEST_POINTER_INVALID'):
                validate_historical_pointer(value)

    def test_damaged_publication_proof_is_not_overridden_by_valid_latest(self):
        from deploom_mcp.goal_historical_checkpoint import read_historical_checkpoint
        context=HistoricalReadContext(b'{}','a'*64,b'{}',b'{}','b'*64,'attempt',60)
        with patch('deploom_mcp.goal_historical_checkpoint.load_historical_context',return_value=(context,None)), \
             patch('deploom_mcp.goal_final_checkpoint.inspect_checkpoint_evidence',side_effect=GoalContractError('FINAL_PUBLICATION_IDENTITY_UNPROVEN')), \
             patch('deploom_mcp.goal_historical_checkpoint.validate_historical_pointer') as pointer:
            with self.assertRaisesRegex(GoalContractError,'FINAL_PUBLICATION_IDENTITY_UNPROVEN'):
                read_historical_checkpoint('r','c','a'*64)
        pointer.assert_not_called()