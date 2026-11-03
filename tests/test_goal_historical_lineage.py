"""Record provenance regressions, not project or mathematical verification."""
import copy
import json
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch
import test_goal_choice as choice_fixture
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_historical_lineage import validate_recorded_lineage


class HistoricalLineageTests(TestCase):
    def setUp(self):
        self.fixture = choice_fixture.CoupledGoalTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.intent = self.fixture.plan()['derivedExactIntent']
        self.started = datetime.now(timezone.utc).timestamp()

    def validate(self, intent=None, started=None):
        i = intent or self.intent
        return validate_recorded_lineage(self.fixture.root, i['goal'], i['binding'], i['policy'],
                                         i.get('executionProfile'), self.started if started is None else started)

    def test_expired_records_have_provenance_without_solving_or_rebuilding_model(self):
        with patch('deploom_mcp.goal_historical_lineage._now', return_value=datetime.now(timezone.utc)+timedelta(hours=2)), \
             patch('deploom_mcp.goal_choice_lineage.build_model', side_effect=AssertionError('history rebuilt model')):
            result = self.validate()
        self.assertEqual(result['metadataFreshness'], 'EXPIRED')
        self.assertEqual(result['mathematicalModelReevaluation'], 'NOT_PERFORMED')
        self.assertEqual(result['originalIssuanceTime'], 'NOT_RECORDED')
        self.assertEqual(result['issuanceTimeMetadataValidity'], 'NOT_ESTABLISHED')

    def test_current_lineage_still_rejects_same_expired_metadata(self):
        future = datetime.now(timezone.utc)+timedelta(hours=2)
        class FutureDatetime(datetime):
            @classmethod
            def now(cls, tz=None):return future
        with patch('deploom_mcp.goal_choice_lineage.datetime', FutureDatetime):
            with self.assertRaisesRegex(GoalContractError, 'CHOICE_METADATA_STALE'):
                self.fixture.validate(self.intent)
        with patch('deploom_mcp.goal_historical_lineage._now', return_value=future):
            self.assertEqual(self.validate()['metadataFreshness'], 'EXPIRED')

    def test_transaction_time_is_not_a_replacement_issuance_time(self):
        for value in [True, float('nan'), float('inf'), self.started-100, self.started+7200]:
            with self.subTest(value=value), self.assertRaisesRegex(GoalContractError, 'HISTORICAL_METADATA_TIME'):
                self.validate(started=value)

    def test_changed_selection_or_metadata_rejects(self):
        wrapper = self.intent['policy']['coupledChoice']
        for folder, digest in [('choice-results', wrapper['selectionDigest']), ('choice-inputs', wrapper['metadataDigest'])]:
            path = self.fixture.root/folder/(digest+'.json'); raw = path.read_bytes()
            try:
                path.write_bytes(raw+b' ')
                with self.assertRaisesRegex(GoalContractError, 'CHOICE_ARTIFACT_CHANGED'):self.validate()
            finally:path.write_bytes(raw)

    def test_forged_issuance_and_duplicate_json_reject(self):
        path = self.fixture.root/'choice-issuance'/(self.intent['selectionDigest']+'.json')
        raw = path.read_bytes(); value = json.loads(raw)
        value['solverResult']['assignment']['held'] = '2.0.0'
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(GoalContractError, 'CORE_SELECTION_ISSUANCE_CHANGED'):self.validate()
        path.write_bytes(b'{"kind":"FORGED",'+raw[1:])
        with self.assertRaisesRegex(GoalContractError, 'INVALID_CANDIDATE_JSON') as rejected:
            self.validate()
        self.assertEqual(rejected.exception.__cause__.code, 'DUPLICATE_JSON_KEY')

    def test_changed_goal_scope_and_foreign_parent_are_not_recorded_binding(self):
        for change in ['scope', 'parent']:
            i = copy.deepcopy(self.intent)
            if change == 'scope':i['goal']['immutablePackages'] = []
            else:i['policy']['coupledChoice']['parentBindingDigest'] = 'f'*64
            with self.subTest(change=change), self.assertRaises(GoalContractError):self.validate(i)

    def test_missing_issuance_is_not_recreated(self):
        path = self.fixture.root/'choice-issuance'/(self.intent['selectionDigest']+'.json')
        path.unlink()
        with self.assertRaises(FileNotFoundError):self.validate()
        self.assertFalse(path.exists())