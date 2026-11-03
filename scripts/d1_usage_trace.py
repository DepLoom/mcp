"""Metadata-only chronology; native usage increments are not causal attribution."""
import hashlib,json,time

class UsageTrace:
    def __init__(self):
        self.started=time.monotonic();self.records=[];self.sequence=0
    def ingest(self,event,usage=None):
        self.sequence+=1;method=event.get('method');p=event.get('params') or {}
        base={'sequence':self.sequence,'elapsedSeconds':round(time.monotonic()-self.started,3)}
        if method=='thread/tokenUsage/updated' and usage is not None:
            self.records.append({**base,'kind':'nativeUsage','total':dict(usage)});return
        if method not in ('item/started','item/completed'):return
        item=p.get('item') or {};typ=item.get('type')
        if typ not in ('commandExecution','mcpToolCall','agentMessage'):return
        r={**base,'kind':method,'type':typ,'id':item.get('id')}
        if typ=='commandExecution':
            command=json.dumps(item.get('command'),sort_keys=True,ensure_ascii=False)
            lower=command.lower()
            r['commandSha256']=hashlib.sha256(command.encode()).hexdigest()
            r['phaseHints']=[label for needle,label in (('get-content','read'),('npm view','registry'),('npm ci','install'),('npm ls','peers'),('typecheck','typecheck'),('build','build'),('test','test'),('write_text','edit'),('writealltext','edit'),('jsdom','runtime'),('blueprint','artifact-read')) if needle in lower]
            r['phaseHintsAreHeuristics']=True
            if method=='item/completed':
                r.update(exitCode=item.get('exitCode'),durationMs=item.get('durationMs'))
                output=item.get('aggregatedOutput') or ''
                r['outputUtf8Bytes']=len(output.encode()) if isinstance(output,str) else None
        elif typ=='mcpToolCall':
            r.update(tool=item.get('tool'),durationMs=item.get('durationMs'))
            result=item.get('result') or {}
            if isinstance(result,dict):
                r['textUtf8Bytes']=sum(len(c.get('text','').encode()) for c in result.get('content',[]) if isinstance(c,dict) and c.get('type')=='text' and isinstance(c.get('text',''),str))
                structured=result.get('structuredContent',result.get('structured_content'))
                r['structuredJsonUtf8Bytes']=len(json.dumps(structured,ensure_ascii=False,separators=(',',':')).encode()) if structured is not None else 0
        elif method=='item/completed':
            text=item.get('text','');r['textUtf8Bytes']=len(text.encode()) if isinstance(text,str) else None
        self.records.append(r)
    def result(self):
        return {'records':self.records,'accounting':'Native cumulative input includes context replay; cached/reasoning are subsets. Chronology and phase hints do not establish marginal causal token costs. No raw commands, output or arguments retained.'}
