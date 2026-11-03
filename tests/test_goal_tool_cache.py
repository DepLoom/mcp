"""Real Core/Node cache scope with sealed fixture tool sources, not S2 acceptance.

Actual pinned package importer is exercised separately by the engineering
positive; these tiny sources deliberately test owner joins, not transpilation.
"""
import copy,hashlib,json,os,unittest
from pathlib import Path
from unittest.mock import patch
import test_goal_tap_nyc_core as fixture
from deploom_mcp import goal_tool_cache as tc
from deploom_mcp.goal_tap_nyc_execution import receipt_matches,physically_current
from deploom_mcp.goal_contract import GoalContractError


def add_fixture_policy(case):
 project=case.f.project;components={}
 for key,names in tc.FILES.items():
  root=project/('pinned-tools/importer' if key=='importer' else 'pinned-tools/node_modules/find-cache-dir');root.mkdir(parents=True)
  for name in names:
   if name=='package.json':
    pkg={'name':tc.PACKAGES[key][0],'version':tc.PACKAGES[key][1]}
    if key=='importer':pkg['dependencies']={'find-cache-dir':'^3.3.1'}
    raw=json.dumps(pkg).encode()
   else:raw=b'module.exports = {}; // Boundary fixture, not published tool bytes\n'
   (root/name).write_bytes(raw)
  components[key]={'path':root.relative_to(project).as_posix(),'files':{n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names}}
 value={'schemaVersion':3,'kind':tc.KIND,'commandScope':tc.SCOPE,'contentClass':'MUTABLE_EXECUTABLE_TOOL_CACHE','allowedNamespaces':['import-jsx'],'components':components};value['recipeHash']=tc.recipe(value)
 case.policy['toolCache']=value
 for row in case.profile['checkBindings']:
  if row['commandIndex']==case.profile['commands'].index('npm run ci')+1:row['normative']['allowedInputs']['externalInputs']['toolCacheRecipeHash']=value['recipeHash']
 return value


class ToolCacheCoreTests(unittest.TestCase):
 def setUp(self):
  self.case=fixture.TapRealCoreTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
  self.value=add_fixture_policy(self.case)
  extra="\nconst d=process.env.CACHE_DIR;a(d);fs.mkdirSync(p.join(d,'import-jsx'));fs.writeFileSync(p.join(d,'import-jsx','a'.repeat(32)+'.js'),'module.exports=1');"
  (self.case.f.project/'check.cjs').write_text(self.case.body+extra,encoding='utf8')
 def test_actual_selected_ci_env_and_physical_current(self):
  c=self.case
  normal='node -e "if(process.env.CACHE_DIR!==undefined)process.exit(9)"'
  c.profile['commands']=[normal,'npm run ci',normal]
  ci=c.profile['checkBindings'][0];ci['commandIndex']=2
  c.profile['checkBindings']=[{'kind':'command','commandIndex':1,'normative':{'allowedInputs':{'externalInputs':'NONE_DECLARED_BY_HOST'}}},ci,{'kind':'command','commandIndex':3,'normative':{'allowedInputs':{'externalInputs':'NONE_DECLARED_BY_HOST'}}}]
  host=dict(os.environ);raw,r=c.invoke('final',True);self.assertEqual(dict(os.environ),host)
  self.assertEqual(r['schemaVersion'],3);self.assertTrue(receipt_matches(c.policy,c.profile,'final',c.f.project,raw,r))
  base=raw['environmentDigest'];ci_digest=r['inputObservation']['actualEnvironmentDigest'];self.assertNotEqual(base,ci_digest)
  self.assertEqual(raw['commandEnvironmentDigests'],{'2':ci_digest})
  self.assertEqual([o['projectExecutionEnvironment']['environmentDigest'] for o in raw['ownerOperationObservations']],[base,base,ci_digest,base])
  self.assertTrue(physically_current(c.policy,c.profile,c.f.project,raw,r,deadline=c.f.deadline))
  self.assertFalse((c.f.project/'node_modules/.cache').exists())
  bad=copy.deepcopy(r);bad['schemaVersion']=2;self.assertFalse(receipt_matches(c.policy,c.profile,'final',c.f.project,raw,bad))
  path=c.f.outputs('final')['cache']/'import-jsx'/('a'*32+'.js');path.write_text('changed')
  self.assertFalse(physically_current(c.policy,c.profile,c.f.project,raw,r,deadline=c.f.deadline))
 def test_stale_consumer_prevents_ci_spawn(self):
  c=self.case;p=c.f.project/self.value['components']['helper']['path']/'index.js';p.write_text('changed')
  import verification_process_supervisor as owner
  original=owner.subprocess.Popen;ci=[]
  def launch(argv,*a,**kw):
   if isinstance(argv,str) and 'npm run ci' in argv:ci.append(argv)
   return original(argv,*a,**kw)
  with patch.object(owner.subprocess,'Popen',side_effect=launch):
   with self.assertRaisesRegex(GoalContractError,'TOOL_CACHE'):c.invoke('private',True)
  self.assertEqual(ci,[])
 def test_ambient_cache_or_wrong_native_mapping_is_rejected(self):
  c=self.case
  with patch.dict(os.environ,{'CACHE_DIR':'true'}):
   with self.assertRaisesRegex(GoalContractError,'TOOL_CACHE'):c.invoke('private',True)
  self.assertFalse(Path(c.f.policy['lanes']['private']['yaml']['path']+'.consumed.json').exists())
  import verification_process_supervisor as owner
  original=owner.prepare_unc_shell;started=[];popen=owner.subprocess.Popen
  def adapted(argv,cwd,env):
   a,d,e=original(argv,cwd,env)
   return (a,d,{**e,'CACHE_DIR':'true'}) if argv[-1]=='npm run ci' else (a,d,e)
  def launch(argv,*a,**kw):
   if isinstance(argv,str) and 'npm run ci' in argv:started.append(argv)
   return popen(argv,*a,**kw)
  with patch.object(owner,'prepare_unc_shell',side_effect=adapted),patch.object(owner.subprocess,'Popen',side_effect=launch):
   with self.assertRaisesRegex(owner.ProcessSupervisionError,'ENVIRONMENT_MISMATCH'):c.invoke('private',True)
  self.assertEqual(started,[])
 def test_namespace_and_recipe_do_not_grant_blanket_cache(self):
  c=self.case
  (c.f.project/'check.cjs').write_text(c.body+"\nfs.mkdirSync(p.join(process.env.CACHE_DIR,'unknown-tool'));",encoding='utf8')
  with self.assertRaisesRegex(GoalContractError,'NAMESPACE'):c.invoke('private',True)
 def test_missing_normative_cache_hash_rejects_before_slot(self):
  c=self.case;c.profile['checkBindings'][0]['normative']['allowedInputs']['externalInputs'].pop('toolCacheRecipeHash')
  with self.assertRaisesRegex(GoalContractError,'NORMATIVE_INPUT_MISMATCH'):c.invoke('private',True)
  self.assertFalse(Path(c.f.policy['lanes']['private']['yaml']['path']+'.consumed.json').exists())

if __name__=='__main__':unittest.main()
