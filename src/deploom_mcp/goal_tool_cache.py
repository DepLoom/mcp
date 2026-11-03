"""Pinned executable tool cache, separate from the unchanged TAP/NYC V2 input.

Trusted pinned tooling/initially empty cache only. The final manifest does not
prove every intermediate executable read or arbitrary hostile-writer closure.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib,json,re,os
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_windows_file_swap import _API,protected_parents

KIND='PINNED_FIND_CACHE_DIR_TOOL_CACHE_V1'
SCOPE='CI_NATIVE_SUBTREE_ONLY'
FILES={'importer':('package.json','index.js','cache.js','transform.js'),'helper':('package.json','index.js')}
PACKAGES={'importer':('@isaacs/import-jsx','4.0.1'),'helper':('find-cache-dir','3.3.2')}


def expect(value):
 if not value:raise GoalContractError('TOOL_CACHE_CONTRACT_UNPROVEN')


def recipe(value):return canonical_digest({k:v for k,v in value.items() if k!='recipeHash'})


def validate(policy):
 value=policy.get('toolCache')
 if value is None:return None
 expect(isinstance(value,dict) and set(value)=={'schemaVersion','kind','commandScope','contentClass','allowedNamespaces','components','recipeHash'})
 expect(type(value['schemaVersion']) is int and value['schemaVersion']==3 and value['kind']==KIND and value['commandScope']==SCOPE
  and value['contentClass']=='MUTABLE_EXECUTABLE_TOOL_CACHE' and value['allowedNamespaces']==['import-jsx'] and value['recipeHash']==recipe(value))
 components=value['components'];expect(isinstance(components,dict) and set(components)==set(FILES));roots=[]
 for key,names in FILES.items():
  row=components[key];expect(isinstance(row,dict) and set(row)=={'path','files'})
  path=row['path'];expect(isinstance(path,str) and path and '\\' not in path and not Path(path).is_absolute() and all(p not in {'','.','..'} and ':' not in p for p in path.split('/')))
  roots.append(path);expect(isinstance(row['files'],dict) and set(row['files'])==set(names))
  expect(all(isinstance(h,str) and re.fullmatch('[a-f0-9]{64}',h) for h in row['files'].values()))
 expect(not any(a==b or a.startswith(b+'/') or b.startswith(a+'/') for a,b in [(roots[0],roots[1])]))
 return value


def declared(policy, tap):
 value=validate(policy);result={'kind':tap['kind'],'recipeHash':tap['recipeHash']}
 if value:result['toolCacheRecipeHash']=value['recipeHash']
 return result


def ci_environment(policy,base,role):
 value=validate(policy)
 if value is None:return base
 from project_execution_environment import project_execution_environment,V2
 expect(not any(k.upper()=='CACHE_DIR' for k in base))
 path=policy['tapNycInput']['lanes'][role]['outputs']['cache']['path']
 expect(Path(path).is_absolute() and str(Path(path).absolute())==path)
 return project_execution_environment(base,contract=V2,overrides={'CACHE_DIR':path})


def prospective(policy,profile,role,base_digest):
 value=validate(policy)
 return {'kind':KIND,'recipeHash':value['recipeHash'],'baseEnvironmentDigest':base_digest,'commandIndex':profile['commands'].index('npm run ci')+1} if value else None


@contextmanager
def held_consumers(policy,project,*,deadline,expected=None):
 value=validate(policy);expect(value is not None);project=Path(project).resolve();api=_API(deadline);handles=[];rows={}
 paths=[project/row['path']/name for key,row in value['components'].items() for name in FILES[key]]
 with protected_parents(api,paths):
  try:
   for key,row in value['components'].items():
    root=project/row['path'];expect(root.resolve().is_relative_to(project))
    for name,wanted in row['files'].items():
     path=root/name;h=api.open(path,readonly_file=True);handles.append(h);observed=api.file(h)
     expect(observed['sha256']==wanted);rows[row['path']+'/'+name]=observed
    info=json.loads((root/'package.json').read_bytes());expect((info.get('name'),info.get('version'))==PACKAGES[key])
    expect(info.get('main','index.js') in {'index.js','./index.js'} and not info.get('exports'))
   importer=project/value['components']['importer']['path'];helper=(project/value['components']['helper']['path']).resolve()
   info=json.loads((importer/'package.json').read_bytes());expect('find-cache-dir' in info.get('dependencies',{}))
   resolved=None
   for parent in [importer,*importer.parents]:
    if not parent.is_relative_to(project):break
    candidate=parent/'node_modules/find-cache-dir'
    if (candidate/'package.json').is_file():resolved=candidate.resolve();break
   expect(resolved==helper)
   if expected is not None:expect(rows==expected)
   yield rows
   for h,(name,wanted) in zip(handles,rows.items()):expect(api.file(h)==wanted)
  finally:
   for h in reversed(handles):api.close(h)


def binding_matches(policy,profile,role,binding,base_digest):
 try:
  wanted=prospective(policy,profile,role,base_digest)
  if wanted is None:return binding is None
  if not isinstance(binding,dict) or set(binding)!=set(wanted)|{'consumerIdentities'}:return False
  if any(binding[k]!=v for k,v in wanted.items()):return False
  rows=binding['consumerIdentities'];value=validate(policy)
  names={row['path']+'/'+name:sha for row in value['components'].values() for name,sha in row['files'].items()}
  if not isinstance(rows,dict) or set(rows)!=set(names):return False
  for name,wanted in names.items():
   row=rows[name]
   if (not isinstance(row,dict) or set(row)!={'volumeSerial','fileId','size','sha256'} or row['sha256']!=wanted
    or not isinstance(row['volumeSerial'],str) or not row['volumeSerial'].isdigit() or not isinstance(row['fileId'],str)
    or re.fullmatch('[a-f0-9]{32}',row['fileId']) is None or type(row['size']) is not int or row['size']<1):return False
  return True
 except (KeyError,TypeError,ValueError):return False


def namespace_matches(receipt):
 try:
  rows=receipt['manifests']['cache']
  for row in rows:
   p=row['relativePath'];kind=row['type']
   if p=='':
    if kind!='directory':return False
   elif p=='import-jsx':
    if kind!='directory':return False
   elif p.startswith('import-jsx/'):
    if kind!='file' or re.fullmatch('import-jsx/[a-f0-9]{32}\\.js',p) is None:return False
   elif kind!='file' or re.fullmatch('[A-Za-z0-9_.@-]+-[a-f0-9]{64}\\.js',p) is None:return False
  return True
 except (KeyError,TypeError):return False


def expected_environment_digests(policy,observation):
 """Historical native witness joins; current environment is checked separately.

 A later registry revision must not replace the environment of a past command.
 The selected map alone is insufficient: the actual started Core witness and
 the full host input binding/recipe are additionally required by receipt_matches.
 """
 value=validate(policy)
 if value is None:return {}
 commands=observation['commandObservations'];matches=[r['commandIndex'] for r in commands if r.get('commandDigest')==hashlib.sha256(b'npm run ci').hexdigest()]
 expect(len(matches)==1 and type(matches[0]) is int)
 selected=[r for r in observation['ownerOperationObservations'] if r.get('guardedSpawnWitness') is not None]
 expect(len(selected)==1);op=selected[0];w=op['guardedSpawnWitness'];index=matches[0]
 expect(isinstance(w,dict) and w.get('kind')=='TRUSTED_PRE_POPEN_GUARD_V1' and w.get('schemaVersion')==1
  and w.get('operation')=='project-command' and op.get('commandIndex')==index and w.get('commandIndex')==index
  and w.get('invocationId')==observation['freshCommandInvocationId'] and w.get('profileDigest')==observation['finalVerificationProfileDigest']
  and w.get('sealedCommandHash')==hashlib.sha256(b'npm run ci').hexdigest() and w.get('argvDigest')==op.get('argvDigest')
  and w.get('cwdDigest')==observation['workspacePathDigest'] and w.get('spawnStarted') is True
  and type(w.get('nativePid')) is int and w['nativePid']>0 and w.get('role') in {'private','final'}
  and isinstance(w.get('environmentDigest'),str) and re.fullmatch('[a-f0-9]{32}',w['environmentDigest']) is not None)
 native={k:v for k,v in w.items() if k not in {'role','inputBindingHash','prePopenObservationHash','spawnStarted','nativePid'}}
 expect(canonical_digest(native)==w.get('prePopenObservationHash'))
 return {str(index):w['environmentDigest']}
