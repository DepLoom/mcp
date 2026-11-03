"""Bounded passive current-reader diagnostics, never acceptance authority.

Only fixed gates, booleans, safe exception identifiers and monotonic durations
are recorded. No project content, paths, exception prose or credentials.
Persistence happens after the reader block and cannot change its verdict.
"""
from __future__ import annotations
import json,os,re,tempfile,time
from pathlib import Path
from deploom_mcp.goal_contract import canonical_digest

_GATES=frozenset({'incumbent','source','swaps','tree','toolchain','environment','tap','environment_and_tap','generated_outputs','verdict'})
_SAFE=re.compile(r'[A-Za-z][A-Za-z0-9_]{0,95}\Z')

class CurrentReadTrace:
    def __init__(self,*,enabled=True):
        self.enabled=enabled;self.started=time.monotonic();self.events=[];self.active=None

    def mark(self,gate,result):
        if not self.enabled or len(self.events)>=32:return
        if gate not in _GATES or not (type(result) is bool or result in ('BEGIN','SKIPPED')):return
        self.active=gate
        self.events.append({'gate':gate,'result':result,'elapsedSeconds':max(0.,time.monotonic()-self.started)})

    def exception(self,exc):
        if not self.enabled or len(self.events)>=32:return
        kind=type(exc).__name__;code=getattr(exc,'code',None)
        self.events.append({'gate':self.active,'result':'EXCEPTION',
            'exceptionType':kind if _SAFE.fullmatch(kind) else 'Exception',
            'code':code if isinstance(code,str) and _SAFE.fullmatch(code) else None,
            'elapsedSeconds':max(0.,time.monotonic()-self.started)})

    def persist(self,context,checkpoint):
        if not self.enabled:return
        try:
            reg=context.registration
            identity={'runId':reg.run_id,'candidateId':reg.candidate_id,'attemptId':context.attempt_id,
                'bindingDigest':reg.binding['bindingDigest'],'authorizationDigest':context.authorization_digest,
                'implementationDigest':reg.binding['identity']['implementationDigest'],
                'checkpointId':checkpoint['checkpointId']}
            value={'schemaVersion':1,'kind':'GOAL_CURRENT_READ_DIAGNOSTIC_V1',
                'authority':'DIAGNOSTIC_ONLY_NOT_ACCEPTANCE','identity':identity,
                'events':list(self.events),'eventLimit':32}
            _publish(identity,value)
        except Exception:
            # A diagnostic is expendable; no fallback can manufacture current=true.
            return


def _publish(identity,value):
    from deploom_mcp.goal_execution_registry import configured_state_root
    from deploom_mcp.goal_report import _plain_under
    root=configured_state_root().resolve()
    directory=_plain_under(root/'current-read-diagnostics'/canonical_digest(identity),root)
    directory.mkdir(parents=True,exist_ok=True)
    destination=_plain_under(directory/'latest.json',root)
    data=(json.dumps(value,ensure_ascii=True,allow_nan=False,separators=(',',':'))+'\n').encode('utf-8')
    if len(data)>32768:raise ValueError('diagnostic exceeds bound')
    fd,name=tempfile.mkstemp(prefix='.current-',suffix='.tmp',dir=directory)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        _plain_under(destination,root)
        os.replace(name,destination)
    finally:
        Path(name).unlink(missing_ok=True)
