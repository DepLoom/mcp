"""Local public-fixture MCP provenance; no raw arguments, stderr or credentials."""
import hashlib,json,re
from pathlib import Path

def versions(root):
    try:
        p=json.loads((Path(root)/'package.json').read_text(encoding='utf8'))
        return {**p.get('dependencies',{}),**p.get('devDependencies',{})}
    except (OSError,ValueError):return {}

def fingerprint(root):
    data={}
    for name in ('package.json','package-lock.json','src/App.tsx','src/mount.tsx','vite.config.ts','tsconfig.json'):
        p=Path(root)/name
        try:
            if p.is_file():data[name]=hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:data[name]='UNREADABLE_DURING_EDIT'
    return data

def decode_result(result):
    if not isinstance(result,dict):return {}
    if isinstance(result.get('structuredContent'),dict):return result['structuredContent']
    for c in result.get('content',[]):
        if isinstance(c,dict) and c.get('type')=='text':
            try:
                x=json.loads(c.get('text',''))
                if isinstance(x,dict):return x
            except (ValueError,TypeError):pass
    return {}

class Observation:
    def __init__(self,root):
        self.root=Path(root);self.initial=fingerprint(root);self.sequence=0;self.first_edit=None
        self.calls=[];self.messages=[];self.names=set(versions(root))
    def ingest(self,event):
        self.sequence+=1
        if event.get('method') not in ('item/started','item/completed'):return
        current=fingerprint(self.root)
        if current!=self.initial and self.first_edit is None:self.first_edit=self.sequence
        p=event.get('params') or {};item=p.get('item') or {}
        if event.get('method')!='item/completed':return
        if item.get('type')=='agentMessage':
            text=item.get('text','')
            if isinstance(text,str):
                text=text.replace(str(self.root),'[ASSIGNED_WORKSPACE]')
                text=re.sub(r'https?://[^\s`<>]+','[URL]',text)
                text=re.sub(r'[A-Za-z]:[\\/][^\n`]+','[LOCAL_PATH]',text)
                self.messages.append(text[:6000])
        if item.get('type')!='mcpToolCall':return
        args=item.get('arguments') or {}
        if not isinstance(args,dict):args={}
        data=decode_result(item.get('result'));rows=[]
        for row in data.get('dependencies',[]) if isinstance(data.get('dependencies'),list) else []:
            if isinstance(row,dict) and row.get('package') in self.names:
                rows.append({k:row.get(k) for k in ('package','current','target','status','needsReview')})
        request={k:args[k] for k in ('target_level','target_mode','objective','min_lag_ok_pct','draft_max_candidates','deadline_seconds') if k in args}
        request['mutable_packages']=[n for n in args.get('mutable_packages',[]) or [] if n in self.names]
        request['required_packages']=[n for n in args.get('required_packages',[]) or [] if n in self.names]
        request['exclude']=[n for n in args.get('exclude',[]) or [] if n in self.names]
        request['approved_version_lines']={n:v for n,v in (args.get('approved_version_lines') or {}).items() if n in self.names}
        request['exact_targets']={n:v for n,v in (args.get('exact_targets') or {}).items() if n in self.names}
        request['registryExplicit']=bool(args.get('registry'))
        try:request['projectMatchesAssigned']=Path(args.get('project_path','')).resolve()==self.root.resolve()
        except (OSError,ValueError,TypeError):request['projectMatchesAssigned']=False
        error=data.get('error') or {}
        if not isinstance(error,dict):error={}
        self.calls.append({'tool':item.get('tool'),'server':item.get('server'),'status':item.get('status'),
          'durationMs':item.get('durationMs'),'sequence':self.sequence,'beforeObservedEdit':self.first_edit is None,
          'request':request,'responseOk':data.get('ok'),'responseStatus':data.get('status'),
          'responseErrorCode':error.get('code'),'transportErrorPresent':bool(item.get('error')),
          'transportErrorMessage':re.sub(r'[A-Za-z]:[\\/][^\n`]+','[LOCAL_PATH]',re.sub(r'https?://[^\s`<>]+','[URL]',str((item.get('error') or {}).get('message',''))))[:1800],
          'decisionCompleteness':data.get('decisionCompleteness'),
          'targetAssessmentAggregate':(data.get('targetAssessment') or {}).get('aggregate'),
          'criticalEvidence':[ {k:e.get(k) for k in ('code','severity','package','nextAction')} for e in data.get('decisionEvidence',[]) if isinstance(e,dict)],
          'targets':rows,'responseIdentities':data.get('identities'),
          'responseSha256':hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest(),
          'manifestAtCall':versions(self.root)})
    def result(self):
        return {'firstObservedEditSequence':self.first_edit,'mcpCalls':self.calls,
                'agentMessagesRedacted':self.messages,'finalVersions':versions(self.root),
                'scope':'LOCAL_PUBLIC_SYNTHETIC_FIXTURE_ONLY','causalUse':'TARGET_AGREEMENT_IS_NOT_PROOF_OF_CAUSAL_USE'}
