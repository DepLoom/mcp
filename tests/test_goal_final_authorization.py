"""Final permission/scope controls; no mock is a project verification result."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploom_mcp.goal_contract import GoalContractError
import deploom_mcp.goal_final_authorization as final


class FinalAuthorizationBoundaryTests(unittest.TestCase):
    def test_private_operation_never_selects_final_authority(self):
        with patch.object(final,'load_host_registration') as load:
            with self.assertRaisesRegex(GoalContractError,'EXPLICIT_FINAL_APPLY_AUTHORIZATION_REQUIRED'):
                final.load_final_grant('r','c','a'*64,'apply-private-candidate')
        load.assert_not_called()

    def test_engineering_permission_never_selects_final_authority(self):
        reg=SimpleNamespace(delta_authorization={'origin':'ENGINEERING_CONTROL'},profile={},project_path='unused-engineering-fixture')
        with patch.object(final,'load_host_registration',return_value=reg):
            with self.assertRaisesRegex(GoalContractError,'FINAL_APPLY_REQUIRES_NORMAL_HOST_AUTHORIZATION'):
                final.load_final_grant('r','c','a'*64,final.FINAL_OPERATION)

    def test_normal_private_permission_without_explicit_final_cap_is_denied(self):
        reg=SimpleNamespace(final_authorization=None)
        with patch.object(final,'load_host_registration',return_value=reg),patch.object(final,'require_registered_execution_class',return_value='PREAPPROVED_HOST_POLICY'):
            with self.assertRaisesRegex(GoalContractError,'FINAL_APPLY_AUTHORIZATION_UNAVAILABLE'):
                final.load_final_grant('r','c','a'*64,final.FINAL_OPERATION)

    def test_final_schema_bool_cannot_equal_integer_version(self):
        reg=SimpleNamespace(final_authorization={'schemaVersion':True,'attemptId':'a','budgetSeconds':60,'acceptedReceiptDigest':'a'*64})
        with patch.object(final,'load_host_registration',return_value=reg),patch.object(final,'require_registered_execution_class',return_value='PREAPPROVED_HOST_POLICY'):
            with self.assertRaisesRegex(GoalContractError,'INVALID_FINAL_AUTHORIZATION'):
                final.load_final_grant('r','c','a'*64,final.FINAL_OPERATION)

    def test_live_contract_changes_only_execution_scope_not_checks(self):
        profile={'commands':['pinned command'],'requiredChecks':['runtime'],
            'admission':{'scope':'ISOLATED_NPM_V2_CANDIDATE','optionalAbsenceRules':[]},
            'checkBindings':[{'checkId':'runtime','oracle':{'sha256':'b'*64},
                'normative':{'purpose':{'goalRequirementId':'runtime'},'expectedOutcome':{'kind':'PASS','exitCode':0},
                    'allowedInputs':{'source':'SEALED_PREPARED_SOURCE','dependencies':'APPROVED_PHYSICAL_TREE'}}}]}
        original=copy.deepcopy(profile);value=final.final_workspace_profile(profile)
        self.assertEqual(profile,original)
        self.assertEqual(value['admission']['scope'],'REGISTERED_CURRENT_NPM_V2_WORKSPACE')
        self.assertEqual(value['checkBindings'][0]['normative']['allowedInputs']['source'],'SEALED_REGISTERED_WORKSPACE')
        value['admission']['scope']=original['admission']['scope']
        value['checkBindings'][0]['normative']['allowedInputs']['source']='SEALED_PREPARED_SOURCE'
        self.assertEqual(value,original)

    def test_json_or_unissued_objects_cannot_be_consumed_as_grants(self):
        for value in [{},object(),final._FinalGrant()]:
            with self.subTest(value=type(value).__name__),self.assertRaisesRegex(GoalContractError,'OPAQUE_FINAL_GRANT_REQUIRED'):
                final.consume_final_grant(value)
