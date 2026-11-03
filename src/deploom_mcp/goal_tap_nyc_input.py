"""One typed held TAP config input; observations alone grant no execution.

Integration must join this record with actual Core command/environment receipts.
No general external-file closure or command execution is implemented here.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib,json,re,time
import yaml
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_windows_file_swap import _API,protected_parents
from deploom_mcp.goal_tap_nyc_outputs import held_output_receipt

KIND='PINNED_TAP_RCFILE_YAML_RESETTABLE_CHILD_V2'
ROLES=('private','final');OUTPUTS=('temp','report','cache')

def fail(reason):raise GoalContractError('TAP_NYC_'+reason)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def hash_ok(v):return isinstance(v,str) and re.fullmatch('[0-9a-f]{64}',v) is not None

def source_config(raw):
 if not isinstance(raw,bytes) or not 1<=len(raw)<=8192:fail('SOURCE_SIZE')
 class Loader(yaml.SafeLoader):pass
 def pairs(loader,node,deep=False):
  obj={}
  for key,value in node.value:
   k=loader.construct_object(key,deep=deep)
   if not isinstance(k,str) or k in obj:fail('DUPLICATE_OR_INVALID_KEY')
   obj[k]=loader.construct_object(value,deep=deep)
  return obj
 Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,pairs)
 try:
  if any(isinstance(t,(yaml.tokens.AliasToken,yaml.tokens.AnchorToken)) for t in yaml.scan(raw)):fail('YAML_ALIAS_UNSUPPORTED')
  value=yaml.load(raw,Loader=Loader)
 except yaml.YAMLError as e:raise GoalContractError('TAP_NYC_SOURCE_YAML_INVALID') from e
 if not isinstance(value,dict) or 'nyc-arg' in value:fail('SOURCE_CONFIG_UNSUPPORTED')
 if value.get('coverage') is not True or not isinstance(value.get('files'),list) or not value['files']:fail('COVERAGE_PROFILE_UNPROVEN')
 return value

def recipe_hash(source_sha):
 if not hash_ok(source_sha):fail('SOURCE_HASH')
 return canonical_digest({'kind':KIND,'sourceSha256':source_sha,'method':'APPEND_THREE_OUTPUT_ONLY_ARGS_RESETTABLE_DATA_V2','command':'npm run ci','cwd':'ACTUAL_VERIFIED_WORKSPACE'})

def render_config(source_bytes,outputs):
 source_config(source_bytes)
 if set(outputs)!=set(OUTPUTS):fail('OUTPUT_SET')
 args=[]
 for key,flag in zip(OUTPUTS,('temp-dir','report-dir','cache-dir')):
  path=Path(outputs[key]) / 'data' if key=='temp' else Path(outputs[key])
  if not path.is_absolute():fail('OUTPUT_PATH')
  args.append('  - '+json.dumps('--'+flag+'='+str(path.absolute()),ensure_ascii=False))
 return source_bytes+(b'' if source_bytes.endswith(bytes([10])) else bytes([10]))+bytes([10])+b'nyc-arg:'+bytes([10])+bytes([10]).join(a.encode('utf-8') for a in args)+bytes([10])

def _path(value):
 if not isinstance(value,str) or not Path(value).is_absolute():fail('ABSOLUTE_PATH_REQUIRED')
 path=Path(value).absolute()
 if str(path)!=value:fail('NONCANONICAL_PATH')
 return path

def _identity(value):
 return isinstance(value,dict) and set(value)=={'volumeSerial','fileId'} and isinstance(value['volumeSerial'],str) and value['volumeSerial'].isdigit() and isinstance(value['fileId'],str) and re.fullmatch('[0-9a-f]{32}',value['fileId']) is not None

def validate_policy(policy):
 if (not isinstance(policy,dict) or set(policy)!={'schemaVersion','kind','sourcePath','sourceSha256','recipeHash','lanes'}
  or type(policy['schemaVersion']) is not int or policy['schemaVersion']!=2 or policy['kind']!=KIND
  or policy['sourcePath']!='.taprc.yaml' or recipe_hash(policy['sourceSha256'])!=policy['recipeHash']
  or not isinstance(policy['lanes'],dict) or set(policy['lanes'])!=set(ROLES)):fail('POLICY_SHAPE')
 roots=[];files=[]
 for lane in policy['lanes'].values():
  if not isinstance(lane,dict) or set(lane)!={'yaml','outputs','ancestryDigest'} or not hash_ok(lane['ancestryDigest']):fail('LANE_SHAPE')
  y=lane['yaml']
  if not isinstance(y,dict) or set(y)!={'path','sha256','size','identity'} or not hash_ok(y['sha256']) or type(y['size']) is not int or not 1<=y['size']<=16384 or not _identity(y['identity']):fail('YAML_BINDING_SHAPE')
  files.append(_path(y['path']))
  if not isinstance(lane['outputs'],dict) or set(lane['outputs'])!=set(OUTPUTS):fail('OUTPUT_SET')
  for row in lane['outputs'].values():
   if not isinstance(row,dict) or set(row)!={'path','identity'} or not _identity(row['identity']):fail('OUTPUT_BINDING_SHAPE')
   roots.append(_path(row['path']))
 allpaths=roots+files
 for i,a in enumerate(allpaths):
  for b in allpaths[i+1:]:
   if a.is_relative_to(b) or b.is_relative_to(a):fail('OVERLAPPING_PATHS')
 return policy

def describe_lane(config,outputs,*,deadline):
 api=_API(deadline);config=Path(config).absolute();paths=[config,*[Path(outputs[k]).absolute() for k in OUTPUTS]];handles=[]
 with protected_parents(api,paths) as parents:
  try:
   api.ntfs(config);h=api.open(config,readonly_file=True);handles.append(h);f=api.file(h)
   result={'yaml':{'path':str(config),'sha256':f['sha256'],'size':f['size'],'identity':api.identity(h)},'outputs':{},'ancestryDigest':canonical_digest(parents)}
   for key,path in zip(OUTPUTS,paths[1:]):
    h=api.open(path,True);handles.append(h)
    if any(path.iterdir()):fail('OUTPUT_NOT_EMPTY')
    result['outputs'][key]={'path':str(path),'identity':api.identity(h)}
   return result
  finally:
   for h in reversed(handles):api.close(h)

def _lcov(path,project):
 if not path.is_file() or not 1<=path.stat().st_size<=4*1024*1024:fail('LCOV_MISSING_OR_OVERSIZED')
 lines=path.read_text(encoding='utf-8').splitlines();records=[];current=None
 for line in lines:
  if line.startswith('SF:'):
   if current is not None:fail('LCOV_STRUCTURE')
   f=Path(line[3:]);f=f if f.is_absolute() else project/f;f=f.resolve()
   if not f.is_relative_to(project) or not f.is_file():fail('LCOV_SOURCE_ESCAPE')
   current={'source':str(f),'DA':{}}
  elif line.startswith('DA:'):
   if current is None:fail('LCOV_STRUCTURE')
   try:
    parts=line[3:].split(',');number=int(parts[0]);hits=int(parts[1])
   except (ValueError,IndexError):fail('LCOV_STRUCTURE')
   if number<=0 or hits<0 or number in current['DA']:fail('LCOV_STRUCTURE')
   current['DA'][number]=hits
  elif line.startswith(('LF:','LH:')):
   if current is None:fail('LCOV_STRUCTURE')
   try:v=int(line[3:])
   except ValueError:fail('LCOV_STRUCTURE')
   if v<0 or line[:2] in current:fail('LCOV_STRUCTURE')
   current[line[:2]]=v
  elif line=='end_of_record':
   if current is None or not {'LF','LH'}.issubset(current) or current['LH']>current['LF']:fail('LCOV_STRUCTURE')
   if len(current['DA'])!=current['LF'] or sum(h>0 for h in current['DA'].values())!=current['LH']:fail('LCOV_STRUCTURE')
   records.append(current);current=None
 if current is not None or not records or not any(r['LF'] for r in records):fail('LCOV_STRUCTURE')
 return {'sha256':sha(path.read_bytes()),'records':len(records),'linesFound':sum(r['LF'] for r in records),'linesHit':sum(r['LH'] for r in records)}

@contextmanager
def held_input(policy,project,role,profile_digest,*,deadline,require_empty=True,seal_environment_digest=None):
 policy=validate_policy(policy)
 if role not in ROLES or not hash_ok(profile_digest) or type(require_empty) is not bool:fail('CONTEXT_SHAPE')
 project=Path(project).absolute().resolve();lane=policy['lanes'][role];config=_path(lane['yaml']['path']);source=project/policy['sourcePath'];outputs={k:_path(v['path']) for k,v in lane['outputs'].items()}
 if config.is_relative_to(project) or any(p.is_relative_to(project) or project.is_relative_to(p) for p in outputs.values()):fail('OUTPUT_INSIDE_SOURCE')
 api=_API(deadline);handles=[];paths=[config,*outputs.values()]
 with protected_parents(api,paths) as parents,protected_parents(api,[source]) as source_parents:
  try:
   if canonical_digest(parents)!=lane['ancestryDigest']:fail('PARENT_IDENTITY_CHANGED')
   for path in [source,config]:
    h=api.open(path,readonly_file=True);handles.append(h)
   source_info=api.file(handles[0]);config_info=api.file(handles[1])
   if source_info['sha256']!=policy['sourceSha256']:fail('SOURCE_CONFIG_CHANGED')
   expected=lane['yaml']
   if api.identity(handles[1])!=expected['identity'] or config_info['sha256']!=expected['sha256'] or config_info['size']!=expected['size']:fail('YAML_CHANGED')
   wanted=render_config(source.read_bytes(),{k:str(p) for k,p in outputs.items()})
   if config.read_bytes()!=wanted:fail('RECIPE_MISMATCH')
   for key,path in outputs.items():
    h=api.open(path,True);handles.append(h)
    if api.identity(h)!=lane['outputs'][key]['identity']:fail('OUTPUT_IDENTITY_CHANGED')
    if require_empty and any(path.iterdir()):fail('OUTPUT_NOT_EMPTY')
   observation={'schemaVersion':2,'kind':KIND,'scope':'HOST_CONFIG_AND_OUTPUT_OBSERVATION_ONLY','role':role,'recipeHash':policy['recipeHash'],'profileDigest':profile_digest,'cwdDigest':canonical_digest(str(project)),'sourceConfig':source_info,'sourceParentsDigest':canonical_digest(source_parents),'yaml':config_info,'yamlPath':str(config),'outputs':lane['outputs'],'ancestryDigest':lane['ancestryDigest'],'lease':'WINDOWS_READ_SHARE_ONLY','executionAuthority':'NONE','fullAcceptance':False}
   yield observation
   if api.file(handles[0])!=source_info or api.file(handles[1])!=config_info:fail('LEASE_INPUT_CHANGED')
   if require_empty:
    with held_output_receipt(outputs,deadline=deadline) as receipt:
     observation['outputReceipt']={**receipt,'lcov':_lcov(outputs['report']/'lcov.info',project)}
     if seal_environment_digest is not None:observation.update(seal_observation(observation,seal_environment_digest))
  finally:
   for h in reversed(handles):api.close(h)

def seal_observation(observation,actual_environment_digest):
 if not isinstance(actual_environment_digest,str) or re.fullmatch('[0-9a-f]{32}',actual_environment_digest) is None or 'outputReceipt' not in observation:fail('EXECUTION_JOIN_REQUIRED')
 observation={**observation,'actualEnvironmentDigest':actual_environment_digest}
 return {**observation,'inputBindingHash':canonical_digest({k:v for k,v in observation.items() if k not in {'outputReceipt','inputBindingHash'}})}

def current_matches(policy,project,record,*,deadline,extra_binding=None):
 try:
  if record.get('role')!='final' or record.get('executionAuthority')!='NONE' or record.get('fullAcceptance') is not False:return False
  with held_input(policy,project,'final',record['profileDigest'],deadline=deadline,require_empty=False) as now:
   if extra_binding is not None:now['toolCacheBinding']=extra_binding
   if any(now[k]!=record[k] for k in now):return False
   lane=policy['lanes']['final'];outputs={k:Path(row['path']) for k,row in lane['outputs'].items()}
   with held_output_receipt(outputs,deadline=deadline) as captured:
    receipt={**captured,'lcov':_lcov(outputs['report']/'lcov.info',Path(project).resolve())}
    return receipt==record.get('outputReceipt') and seal_observation({**now,'outputReceipt':receipt},record['actualEnvironmentDigest'])==record
 except (OSError,RuntimeError,ValueError,KeyError,TypeError):return False
