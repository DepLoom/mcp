"""Historical execution evidence vs current host environment, actual Core fixture."""
import os,unittest
from unittest.mock import patch
import test_goal_tool_cache as fixture
from deploom_mcp.goal_tap_nyc_execution import receipt_matches
from deploom_mcp.goal_execution_environment import environment_matches

@unittest.skipUnless(os.name=='nt','Native held-input and output profiles require Windows handles')
class ToolCacheHistoryTests(unittest.TestCase):
 def test_registry_revision_does_not_rewrite_historical_execution_env(self):
  f=fixture.ToolCacheCoreTests();f.setUp();self.addCleanup(f.doCleanups);c=f.case
  raw,r=c.invoke('final',True)
  self.assertTrue(receipt_matches(c.policy,c.profile,'final',c.f.project,raw,r));self.assertTrue(environment_matches(c.policy,raw))
  with patch.dict(os.environ,{'DEPLOOM_MCP_EXECUTION_MANIFEST_SHA256':'revised-final-authorization'}):
   self.assertTrue(receipt_matches(c.policy,c.profile,'final',c.f.project,raw,r),'A new operator registry hash cannot invalidate historical native evidence')
   self.assertFalse(environment_matches(c.policy,raw),'Currentness must still notice the changed actual host mapping')

if __name__=='__main__':unittest.main()
