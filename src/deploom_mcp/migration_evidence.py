"""Optional reference section, deliberately separate from goal/execution authority."""
from __future__ import annotations
import copy,dataclasses,hashlib,json,re,sys,time
from deploom_mcp.migration_sources import CATALOG,EvidenceUnavailable,SourceStore,VERSION,digest,encoded
SCHEMA='official_migration_evidence.v1'
EXACT=re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z')

def validate_request(value):
    fields={'schema','mode','transitions','detail','localApplicability'}
    if (not isinstance(value,dict) or set(value)!=fields or value['schema']!=SCHEMA or value['mode']!='ON_DEMAND'
            or value['detail'] not in ('summary','usage','full') or value['localApplicability'] not in ('OFF','ON')
            or not isinstance(value['transitions'],list) or not 1<=len(value['transitions'])<=2):
        raise EvidenceUnavailable('INVALID_MIGRATION_EVIDENCE_REQUEST')
    seen=set()
    for row in value['transitions']:
        if not isinstance(row,dict) or set(row)!={'package','from','to'} or any(not isinstance(v,str) or len(v)>160 for v in row.values()):
            raise EvidenceUnavailable('INVALID_TRANSITION_IDENTITY')
        if any(not EXACT.fullmatch(row[k]) for k in ('from','to')):raise EvidenceUnavailable('EXACT_TRANSITION_REQUIRED')
        from semantic_version import Version
        try:
            if any(str(Version(row[k]))!=row[k] for k in ('from','to')):raise ValueError()
        except ValueError:raise EvidenceUnavailable('EXACT_TRANSITION_REQUIRED') from None
        key=(row['package'],row['from'],row['to'])
        if key in seen:raise EvidenceUnavailable('DUPLICATE_TRANSITION')
        seen.add(key)
    return copy.deepcopy(value)

def unknown(reason,*,binding_digest=None):
    return {'schema':SCHEMA,'authority':'REFERENCE_ONLY','verificationAuthority':'NONE','status':'UNKNOWN',
        'reason':reason,'goalBindingDigest':binding_digest,'transitions':[],'evidenceComplete':False}

def _app(status='UNKNOWN',reason='LOCAL_INSPECTION_OFF'):
    return {'authority':'PLANNING_ONLY','status':status,'reason':reason,'sourceIdentity':None,'localCoverage':None,
        'observations':[],'unusedControls':[],'semanticApplicability':'UNKNOWN','executionAuthority':'NONE'}

def _bool(text,pattern):
    match=re.search(pattern,text,re.I|re.S)
    return None if match is None else match.group(1).lower()=='true'

def source_controls(package,documents):
    by={d['kind']:d for d in documents};controls=[];conflicts=[]
    for role in ['beforePackage','afterPackage']:
        try:data=json.loads(by[role]['text'])
        except (ValueError,KeyError):raise EvidenceUnavailable('OFFICIAL_PACKAGE_JSON_INVALID') from None
        expected=by[role]['tag'].removeprefix('v')
        if not isinstance(data,dict) or data.get('name')!=package or data.get('version')!=expected:raise EvidenceUnavailable('OFFICIAL_PACKAGE_IDENTITY_MISMATCH')
        if role=='afterPackage':
            controls.append({'controlId':'TARGET_PACKAGE_METADATA','kind':'PACKAGE_EXPORTS','sourceIds':[by[role]['sourceId']],
                'claims':{k:data[k] for k in ('type','exports','engines') if k in data},'authority':'SOURCE_METADATA_ONLY'})
    if package=='strip-json-comments':
        defaults={}
        source=_bool(by['afterSource']['text'],r'trailingCommas\s*=\s*(true|false)')
        types=re.search(r'/\*\*((?:(?!\*/).)*)\*/\s*(?:readonly\s+)?trailingCommas\??\s*:\s*boolean',by['afterTypes']['text'],re.S)
        documented=_bool(types.group(1),r'@default\s+(true|false)') if types else None
        readme=re.search(r'#{2,4}\s+trailingCommas\b(.*?)(?=\n#{2,4}\s|\Z)',by['afterReadme']['text'],re.S)
        readme_default=_bool(readme.group(1),r'Default:\s*`?(true|false)') if readme else None
        for kind,value in [('afterSource',source),('afterTypes',documented),('afterReadme',readme_default)]:
            if value is not None:defaults[kind]={'value':value,'sourceId':by[kind]['sourceId'],'claim':'SOURCE_LITERAL' if kind=='afterSource' else 'DOCUMENTED_DEFAULT'}
        if defaults:
            controls.append({'controlId':'TRAILING_COMMAS_DEFAULT','kind':'API','claims':defaults,'runtimeEvaluated':False})
            if len({v['value'] for v in defaults.values()})>1:conflicts.append({'controlId':'TRAILING_COMMAS_DEFAULT','status':'CONFLICT','claims':defaults,'resolution':'NOT_AUTOMATIC'})
        controls.append({'controlId':'MODULE_FORMAT_TRANSITION','kind':'EXPORT','sourceIds':[by['beforeSource']['sourceId'],by['afterSource']['sourceId']],
            'observations':{'beforeModuleExportsSyntax':'module.exports' in by['beforeSource']['text'],
                'afterDefaultExportSyntax':bool(re.search(r'export\s+default\s+function',by['afterSource']['text']))},'localRepairRequired':'UNKNOWN'})
    elif package=='vite':
        try:companion=json.loads(by['companionPackage']['text'])
        except (ValueError,KeyError):raise EvidenceUnavailable('COMPANION_METADATA_INVALID') from None
        if companion.get('name')!='@vitejs/plugin-react' or companion.get('version')!='4.5.2':raise EvidenceUnavailable('COMPANION_IDENTITY_MISMATCH')
        controls.append({'controlId':'COMPANION_PEER_REFERENCE','kind':'PEER','sourceIds':[by['companionPackage']['sourceId']],
            'package':'@vitejs/plugin-react','version':'4.5.2','range':companion.get('peerDependencies',{}).get('vite'),
            'relation':'REFERENCE_NOT_AUTOMATICALLY_SELECTED','resolverVerified':False})
    return controls,conflicts

def _keyword_hints(documents,source_version,target_version):
    from deploom_mcp.distribution import resolve_core_dir
    core=resolve_core_dir();sys.path.insert(0,str(core))
    import dependency_live_roadmap_generator as generator
    from pathlib import Path
    if not Path(generator.__file__).resolve().is_relative_to(core.resolve()):
        return {'kind':'LABELLED_KEYWORD_HINTS','status':'UNAVAILABLE_CORE_ORIGIN'}
    value=generator.analyze_release_text('\n\n'.join(x['text'] for x in documents),source_version,target_version,len(documents),'Pinned reference documents; semantic/project coverage unknown',coverage_complete=False)
    return {'kind':'LABELLED_KEYWORD_HINTS','authority':'UNTRUSTED_DOCUMENT_HINTS','semanticValidation':'NOT_PERFORMED',
        'breaking':value.breaking_changes,'migration':value.migration_notes,'requirements':value.requirements,'deprecations':value.deprecations}

def reference_transition(store,row,detail):
    result={**row,'reference':{'authority':'REFERENCE_ONLY','status':'UNKNOWN','sources':[],'controls':[],
        'conflicts':[],'coverage':{'status':'PARTIAL','missingKinds':['RUNTIME','SEMANTIC_LOCAL_APPLICABILITY'] }},'applicability':_app()}
    ref=result['reference'];key=(row['package'],row['from'],row['to'])
    if row['from']==row['to']:ref.update(status='NO_TRANSITION');return result
    from semantic_version import Version
    if Version(row['to']).prerelease:ref.update(status='UNSUPPORTED',reason='PRERELEASE_TARGET_REQUIRES_SEPARATE_POLICY');return result
    if row['package'].startswith('npm:') or '/' in row['package'] and not row['package'].startswith('@'):
        ref.update(status='IDENTITY_UNVERIFIED',reason='RENAME_OR_ALIAS_UNVERIFIED');return result
    if key not in CATALOG:ref.update(status='UNSUPPORTED',reason='EXACT_TRANSITION_PROVIDER_UNAVAILABLE');return result
    documents=[]
    try:
        for kind,repo,tag,path in CATALOG[key]:
            document=store.document(kind,repo,tag,path)
            document['sourceId']=digest(encoded([document['repo'],document['resolvedCommit'],document['artifact'],document['sha256']]))
            documents.append(document)
        controls,conflicts=source_controls(row['package'],documents)
        ref.update(status='CONFLICT' if conflicts else 'AVAILABLE',retrievalStatus='AVAILABLE',controls=controls,conflicts=conflicts,
            keywordHints=_keyword_hints(documents,row['from'],row['to']))
    except (EvidenceUnavailable,OSError,ValueError) as exc:ref.update(status='UNKNOWN',reason=str(exc) if isinstance(exc,EvidenceUnavailable) else 'REFERENCE_UNAVAILABLE')
    for document in documents:
        source={k:v for k,v in document.items() if k!='text'}
        if detail=='full':
            body=document['text'].encode('utf-8');source['text']=body[:8192].decode('utf-8',errors='ignore');source['textTruncated']=len(body)>8192
        ref['sources'].append(source)
    return result

def build_reference_section(request,store,*,binding_digest=None):
    request=validate_request(request);start=time.monotonic();cpu=time.process_time()
    transitions=[reference_transition(store,row,request['detail']) for row in request['transitions']]
    statuses={r['reference']['status'] for r in transitions}
    return {'schema':SCHEMA,'authority':'REFERENCE_ONLY','verificationAuthority':'NONE','goalBindingDigest':binding_digest,
        'status':('CONFLICT' if 'CONFLICT' in statuses else 'AVAILABLE') if statuses.issubset({'AVAILABLE','CONFLICT','NO_TRANSITION'}) else 'PARTIAL',
        'transitionCoverage':{'requested':len(transitions),'referenceAvailable':sum(r['reference']['status'] in {'AVAILABLE','CONFLICT'} for r in transitions),'unknownOrUnsupported':sum(r['reference']['status'] not in {'AVAILABLE','CONFLICT','NO_TRANSITION'} for r in transitions)},
        'providerVersion':VERSION,'transitions':transitions,'evidenceComplete':False,
        'metrics':{**store.metrics,'wallSeconds':time.monotonic()-start,'processCpuSeconds':time.process_time()-cpu,
            'byteMetricScope':'HTTP_RESPONSE_BODY_BYTES_READ_NOT_HEADERS_OR_NATIVE_TOKENS'},
        'cacheTrust':'LOCAL_OPERATOR_CACHE_NOT_SIGNATURE_ATTESTATION'}


async def attach_to_plan(payload,project_path,request,remaining):
    """Never changes the original proposal fields, authority or intent."""
    from deploom_mcp.process_runner import run_process_async
    from pathlib import Path
    import os
    binding=payload.get('binding',{});binding_digest=binding.get('bindingDigest')
    try:
        request=validate_request(request)
        if remaining<=0:raise EvidenceUnavailable('EVIDENCE_TOOL_DEADLINE_EXHAUSTED')
        context={'projectPath':project_path,'binding':binding,'assignment':payload['assignment'],'request':request}
        args=encoded(context).decode('utf-8')
        if len(args)>65536:raise EvidenceUnavailable('EVIDENCE_CONTEXT_TOO_LARGE')
        env=dict(os.environ);env['PYTHONIOENCODING']='utf-8'
        start=time.monotonic()
        result=await run_process_async([sys.executable,'-B','-m','deploom_mcp.migration_worker',args,str(min(30.0,remaining))],
            cwd=str(Path(__file__).parent),env=env,timeout=min(30.0,remaining),max_output_bytes=100*1024)
        if result.timed_out:raise EvidenceUnavailable('EVIDENCE_TOOL_DEADLINE_EXHAUSTED')
        if result.returncode:raise EvidenceUnavailable('EVIDENCE_WORKER_UNAVAILABLE')
        section=json.loads(result.stdout)
        if (not isinstance(section,dict) or section.get('schema')!=SCHEMA or section.get('authority')!='REFERENCE_ONLY'
                or section.get('verificationAuthority')!='NONE' or section.get('goalBindingDigest')!=binding_digest):
            raise EvidenceUnavailable('EVIDENCE_WORKER_PROTOCOL_INVALID')
        forbidden={'executionReady','accepted','currentWorkspaceVerified','checkpointId','authorization','derivedExactIntent','goal','binding','assignment','grant'}
        def check_authority(value):
            if isinstance(value,dict):
                if forbidden.intersection(value):raise EvidenceUnavailable('EVIDENCE_AUTHORITY_PROMOTION_REJECTED')
                if 'executionAuthority' in value and value['executionAuthority']!='NONE':raise EvidenceUnavailable('EVIDENCE_AUTHORITY_PROMOTION_REJECTED')
                for child in value.values():check_authority(child)
            elif isinstance(value,list):
                for child in value:check_authority(child)
        check_authority(section)
        section.setdefault('metrics',{})['subprocessWallSeconds']=time.monotonic()-start
    except EvidenceUnavailable as exc:section=unknown(str(exc),binding_digest=binding_digest)
    except (OSError,ValueError,KeyError,TypeError):section=unknown('EVIDENCE_WORKER_UNAVAILABLE',binding_digest=binding_digest)
    return {**payload,'officialMigrationEvidence':section}
