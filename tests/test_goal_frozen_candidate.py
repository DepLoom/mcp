"""Real generated offline npm artifact -> private Core/MCP owner; no network/model."""
import base64,copy,dataclasses,hashlib,io,json,os,shutil,subprocess,sys,tarfile,tempfile,time,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from deploom_mcp.distribution import resolve_core_dir
sys.path.insert(0,str(resolve_core_dir()))
import source_snapshot as core
from deploom_mcp.goal_contract import canonical_digest,GoalContractError
from deploom_mcp.goal_worker import prepare_goal_plan
from deploom_mcp.goal_check_bindings import build_inline_node_command,initial_verification_context,admission_scope_digest
from deploom_mcp.goal_execution_registry import OPERATION,FROZEN_ROUTE,load_host_registration
from deploom_mcp.goal_delta_trial import propose_exact_delta,APPLY_OPERATION
from deploom_mcp.goal_verifier_bridge import execute_registered_private_candidate,scoped_profile_requirements_complete

class FrozenRealOwnerTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name).resolve();cls.project=cls.root/'baseline';cls.project.mkdir();cls.state=cls.root/'state';cls.state.mkdir();cache=cls.state/'cache';cache.mkdir();cls.env={'DEPLOOM_MCP_STATE_DIR':str(cls.state),'DEPLOOM_MCP_OFFLINE_CACHE':str(cache),'GIT_CEILING_DIRECTORIES':str(cls.root),'NODE_OPTIONS':'','NODE_PATH':'','DEPLOOM_VEX_AUTO_REFS':'0'}
  def artifact(version):
   data=io.BytesIO()
   with tarfile.open(fileobj=data,mode='w:gz') as archive:
    for name,raw in {'package/package.json':json.dumps({'name':'fixture-runtime','version':version,'main':'index.js'}).encode(),'package/index.js':('module.exports='+json.dumps(version)+';').encode()}.items():
     info=tarfile.TarInfo(name);info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
   payload=data.getvalue();path=cls.root/('fixture-'+version+'.tgz');path.write_bytes(payload)
   return path,{'version':version,'resolved':'https://example.invalid/fixture-runtime-'+version+'.tgz','integrity':'sha512-'+base64.b64encode(hashlib.sha512(payload).digest()).decode()}
  old_tar,old_record=artifact('1.0.0');new_tar,new_record=artifact('2.0.0')
  from draft_npm_preflight import _npm_command
  node,npm=_npm_command()
  helper=Path(__file__).with_name('npm_fixture_cache.cjs')
  prepared=subprocess.run([str(node),str(helper),str(npm),str(new_tar),str(cache),new_record['integrity'],new_record['resolved'],'fixture-runtime','2.0.0'],check=True,capture_output=True,timeout=20)
  cache_proof=json.loads(prepared.stdout)
  if (cache_proof.get('status')!='STOCK_NPM_FIXTURE_CACHE_VERIFIED'
      or cache_proof.get('archiveSHA256')!=hashlib.sha256(new_tar.read_bytes()).hexdigest()
      or cache_proof.get('integrity')!=new_record['integrity']
      or cache_proof.get('networkAttempts')!=0
      or cache_proof.get('indexVerified') is not True or cache_proof.get('lockResolvedBodyVerified') is not True):
   raise AssertionError('exact offline fixture cache preparation unproven')
  before={'name':'frozen-owner-fixture','version':'1.0.0','private':True,'dependencies':{'fixture-runtime':'1.0.0'}};after=copy.deepcopy(before);after['dependencies']['fixture-runtime']='2.0.0'
  old={'name':before['name'],'version':'1.0.0','lockfileVersion':2,'requires':True,'packages':{'':before,'node_modules/fixture-runtime':old_record},'dependencies':{'fixture-runtime':old_record}}
  new={'name':before['name'],'version':'1.0.0','lockfileVersion':2,'requires':True,'packages':{'':after,'node_modules/fixture-runtime':new_record},'dependencies':{'fixture-runtime':new_record}}
  (cls.project/'package.json').write_text(json.dumps(before));(cls.project/'package-lock.json').write_text(json.dumps(old));(cls.project/'marker.txt').write_text('preserve')
  subprocess.run(['git','-C',str(cls.project),'init','-q'],check=True,capture_output=True,timeout=5);subprocess.run(['git','-C',str(cls.project),'add','--','package.json','package-lock.json','marker.txt'],check=True,capture_output=True,timeout=5);subprocess.run(['git','-C',str(cls.project),'-c','user.name=DepLoom Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgSign=false','commit','-q','-m','fixture'],check=True,capture_output=True,timeout=5)
  (cls.state/'candidate.json').write_text(json.dumps(after));(cls.state/'candidate-lock.json').write_text(json.dumps(new))
  body=b"if(require('fixture-runtime')!=='2.0.0')throw Error('WRONG_PHYSICAL_PACKAGE');console.log('actual fixture runtime2');";cmd=build_inline_node_command(body);oracle=cls.state/'runtime.cjs';oracle.write_bytes(body)
  policy={'healthPolicy':'REPORT_ONLY','hardPolicyRequired':False,'transitiveChangePolicy':'EXACT_HOST_ENUMERATED','privateVerificationRoute':FROZEN_ROUTE}
  if getattr(cls,'private_budget_seconds',None) is not None:policy['privateVerificationBudgetSeconds']=cls.private_budget_seconds
  goal={'schemaVersion':1,'goalKind':'functional-maintenance','healthPolicy':'REPORT_ONLY','goalId':'real-frozen-owner','exactTargets':{'fixture-runtime':'2.0.0'},'mutablePackages':['fixture-runtime'],'immutablePackages':[],'excludedPackages':[],'requiredChecks':['runtime','source','cleanup'],'acceptPartial':False,'allowSourceRepair':False,'policyDigest':canonical_digest(policy)}
  bindings=[{'checkId':'runtime','kind':'command','commandIndex':1,'commandSha256':hashlib.sha256(cmd.encode()).hexdigest(),'oracle':{'hostPath':'runtime.cjs','sha256':hashlib.sha256(body).hexdigest(),'mode':'INLINE_NODE_V1','bodyBytes':len(body)}},{'checkId':'source','kind':'core','evidence':'SOURCE_LOCK_CONTINUITY'},{'checkId':'cleanup','kind':'core','evidence':'OWNED_CLEANUP_CLOSED'}]
  for row in bindings:
   command=row['kind']=='command';row['normative']={'schemaVersion':1,'purpose':{'goalRequirementId':row['checkId'],'subjectPackages':['fixture-runtime']},'expectedOutcome':{'kind':'PASS','exitCode':0} if command else {'kind':'OWNER_OBSERVED','evidence':row['evidence']},'allowedInputs':{'source':'SEALED_PREPARED_SOURCE','dependencies':'APPROVED_PHYSICAL_TREE','oracle':'PINNED_INLINE_BODY' if command else 'CORE_OWNER','externalInputs':'NONE_DECLARED_BY_HOST'}}
  profile={'schemaVersion':2,'commands':[cmd],'requiredChecks':goal['requiredChecks'],'projectChecks':'strict','verificationPurpose':'intermediate-candidate','checkBindings':bindings,'admission':{'schemaVersion':1,'scope':'ISOLATED_NPM_V2_CANDIDATE','allowedChangeScopeDigest':admission_scope_digest(goal),'optionalAbsenceRules':[]}}
  cls.manifest=cls.state/'execution.json';cls.env['DEPLOOM_MCP_EXECUTION_MANIFEST']=str(cls.manifest)
  with patch.dict(os.environ,cls.env):
   initial=prepare_goal_plan(cls.project,goal);goal['verificationContext']=initial_verification_context(profile,initial['binding']['identity']);binding=prepare_goal_plan(cls.project,goal)['binding']
   cls.entry={'runId':'real-frozen','candidateId':'candidate-2','projectPath':str(cls.project),'goal':goal,'binding':binding,'profile':profile,'policy':policy,'candidateFiles':{'manifest':'candidate.json','lock':'candidate-lock.json'},'authorization':{'operation':OPERATION,'bindingDigest':binding['bindingDigest'],'profileDigest':canonical_digest(profile),'targetMapDigest':canonical_digest(goal['exactTargets'])},'status':'OPEN','deadlineUTC':(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat()}
   def publish():
    cls.manifest.write_text(json.dumps({'schemaVersion':1,'entries':[cls.entry]}));cls.env['DEPLOOM_MCP_EXECUTION_MANIFEST_SHA256']=hashlib.sha256(cls.manifest.read_bytes()).hexdigest();os.environ.update(cls.env)
   publish();reg=load_host_registration('real-frozen','candidate-2',binding['bindingDigest'],OPERATION)
   with core.verification_source_snapshot_scope(cls.project,timeout_seconds=10,strict_cleanup=True) as snapshot:
    proposal=propose_exact_delta(reg,snapshot,(cls.state/'candidate.json').read_bytes(),(cls.state/'candidate-lock.json').read_bytes());cls.entry['deltaAuthorization']={'schemaVersion':1,'operation':APPLY_OPERATION,'origin':'PREAPPROVED_HOST_POLICY','candidateDigest':proposal.candidate_digest,'deltaDigest':proposal.delta_digest,'bindingDigest':binding['bindingDigest'],'profileDigest':canonical_digest(profile),'sourceSnapshotKey':snapshot.key,'transitiveScope':'EXACT_HOST_ENUMERATED','stateRootDigest':canonical_digest({'hostStateRoot':str(cls.state)})}
   publish();cls.registration=load_host_registration('real-frozen','candidate-2',binding['bindingDigest'],OPERATION)
   # A real new route must never call the old assignment resolver.
   with patch('baseline_constraint_verifier.verify_assignment',side_effect=AssertionError('OLD_RESOLVER_CALLED')):
    cls.record=execute_registered_private_candidate('real-frozen','candidate-2',binding['bindingDigest'],OPERATION)
 @classmethod
 def tearDownClass(cls):cls.temp.cleanup()
 def valid(self,record=None):
  with patch.dict(os.environ,self.env):return scoped_profile_requirements_complete(self.record if record is None else record,self.registration)
 def test_real_offline_install_and_runtime_then_cleanup(self):
  self.assertTrue(self.valid());self.assertTrue(self.record['candidateProfileAccepted']);self.assertFalse(self.record['fullAcceptance']);raw=self.record['verificationObservation']['coreWorkspaceObservation'];self.assertEqual(raw['installation']['status'],'FRESH_COLD_FROZEN_INSTALL_OBSERVED');self.assertEqual([x['operation'] for x in raw['ownerOperationObservations']],['strict-frozen-install','project-command']);self.assertEqual(raw['materializedDependencyObligations']['instanceCount'],1);self.assertFalse((self.project/'node_modules').exists());self.assertEqual((self.project/'marker.txt').read_text(),'preserve');self.assertFalse(list((self.state/'delta-trial-workspaces').iterdir()))
 def test_stale_baseline_or_lock_cannot_be_relabelled(self):
  for mode in ('source','lock','workspace'):
   r=copy.deepcopy(self.record);o=r['verificationObservation'];raw=o['coreWorkspaceObservation']
   if mode=='source':raw['sourceSnapshotKey']=o['baselineSourceSnapshotKey']
   if mode=='lock':raw['resolvedLockfileHash']='f'*64
   if mode=='workspace':raw['workspacePathDigest']=canonical_digest(str(self.project))
   o['coreObservationDigest']=canonical_digest(raw);self.assertFalse(self.valid(r),mode)
 def test_unknown_tree_and_timeout_after_return_are_rejected(self):
  for mode in ('unknown','timeout','cleanup','command','argv'):
   r=copy.deepcopy(self.record);o=r['verificationObservation'];raw=o['coreWorkspaceObservation']
   if mode=='unknown':raw['materializedDependencyObligations']['unknownCount']=1
   if mode=='timeout':raw['ownerOperationObservations'][-1]['timedOut']=True
   if mode=='cleanup':r['allOwnedSnapshotCleanupConfirmed']=False
   if mode=='command':raw['commandObservations'][0]['returnCode']=1
   if mode=='argv':raw['commandObservations'][0]['inlineExecution']['argv'][2]='console.log(1)'
   o['coreObservationDigest']=canonical_digest(raw);self.assertFalse(self.valid(r),mode)
 def test_missing_impact_or_duplicate_install_rejected(self):
  r=copy.deepcopy(self.record);r['verificationObservation']['namedCheckObservations']['checks'].pop();self.assertFalse(self.valid(r))
  r=copy.deepcopy(self.record);o=r['verificationObservation'];raw=o['coreWorkspaceObservation'];raw['ownerOperationObservations'].insert(0,copy.deepcopy(raw['ownerOperationObservations'][0]));o['coreObservationDigest']=canonical_digest(raw);self.assertFalse(self.valid(r))
 def test_raw_origin_keeps_current_workspace_scope_and_no_final_claim(self):
  o=self.record['verificationObservation'];self.assertEqual(o['materializedSourceOrigin']['scope'],'CURRENT_HOST_WORKSPACE');self.assertNotIn('status',o['materializedSourceOrigin']);self.assertFalse(o['coreWorkspaceObservation']['currentWorkspaceVerified']);self.assertFalse(o['coreWorkspaceObservation']['checkpointPublished'])

if __name__=='__main__':unittest.main()
