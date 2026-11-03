"""Versioned proof joins; no synthetic observation is project acceptance."""
import copy,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
from deploom_mcp.distribution import resolve_core_dir
sys.path.insert(0,str(resolve_core_dir()))
from project_execution_environment import project_environment_observation,workspace_execution_environment
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_execution_environment import (LEGACY,V2,environment_contract,core_environment_arguments,environment_observation_matches_contract,environment_matches)

def raw_observation(row):
 launch={'schemaVersion':2,'contract':V2,'origin':'NATIVE_POPEN_CHILD_MAPPING','environmentDigest':row['environmentDigest'],'cwdDigest':'c'*64,'transportValueForwarded':False}
 return {'environmentDigest':row['environmentDigest'],'projectExecutionEnvironment':row,'workspacePathDigest':'c'*64,'commandObservations':[],'installation':{'status':'FRESH_COLD_FROZEN_INSTALL_OBSERVED'},'ownerOperationObservations':[{'projectExecutionEnvironment':launch}]}

class GoalEnvironmentContractTests(unittest.TestCase):
 def setUp(self):self.policy={'projectExecutionEnvironment':V2,'privateVerificationRoute':'FROZEN_EXACT_SINGLE_MATERIALIZATION_V1'}
 def test_only_exact_host_mode_supported(self):
  self.assertEqual(environment_contract({}),LEGACY);self.assertEqual(core_environment_arguments({}),{});self.assertEqual(core_environment_arguments(self.policy),{'environment_contract':V2})
  for value in [True,{},'wildcard-CODEX']:
   with self.assertRaises(GoalContractError):environment_contract({'projectExecutionEnvironment':value})
  with self.assertRaises(GoalContractError):environment_contract({'projectExecutionEnvironment':V2})
 def test_legacy_receipt_never_promoted_by_v2_policy(self):
  old={'environmentDigest':'old'};self.assertTrue(environment_observation_matches_contract({},old));self.assertFalse(environment_observation_matches_contract(self.policy,old))
 def test_version_boolean_forwarded_transport_or_unattested_origin_rejected(self):
  env=workspace_execution_environment({'PATH':'x','CODEX_TOOL_CALL_ID':'removed'},'cache',contract=V2);row=project_environment_observation(env,contract=V2);raw=raw_observation(row)
  self.assertTrue(environment_observation_matches_contract(self.policy,raw));self.assertFalse(environment_observation_matches_contract({},raw))
  for key,value in [('schemaVersion',True),('transportValueForwarded',True),('origin','CALLER_CLAIM'),('removedTransportNames',['CODEX_*'])]:
   r=copy.deepcopy(raw);r['projectExecutionEnvironment'][key]=value
   with self.subTest(key=key):self.assertFalse(environment_observation_matches_contract(self.policy,r))
 def test_missing_actual_launch_or_wrong_env_cwd_cannot_borrow_planned_fingerprint(self):
  env=workspace_execution_environment({'PATH':'x'},'cache',contract=V2);row=project_environment_observation(env,contract=V2)
  for mode in ['missing','wrong-env','wrong-cwd','unattested']:
   raw=raw_observation(row)
   if mode=='missing':raw['ownerOperationObservations']=[]
   else:raw['ownerOperationObservations'][0]['projectExecutionEnvironment'][{'wrong-env':'environmentDigest','wrong-cwd':'cwdDigest','unattested':'origin'}[mode]]='wrong'
   with self.subTest(mode=mode):self.assertFalse(environment_observation_matches_contract(self.policy,raw))
 def test_current_reader_uses_new_actual_mapping_and_retains_other_environment_guards(self):
  cache=Path.cwd()/'not-created-cache';host={**os.environ,'DEPLOOM_MCP_OFFLINE_CACHE':str(cache),'CODEX_TOOL_CALL_ID':'call-A'}
  with patch.dict(os.environ,host,clear=True):
   env=workspace_execution_environment(os.environ,cache.resolve(),contract=V2);row=project_environment_observation(env,contract=V2);raw=raw_observation(row)
   self.assertTrue(environment_matches(self.policy,raw))
   os.environ['CODEX_TOOL_CALL_ID']='call-B';self.assertTrue(environment_matches(self.policy,raw))
   os.environ['NODE_PATH']='different';self.assertFalse(environment_matches(self.policy,raw))

if __name__=='__main__':unittest.main()
