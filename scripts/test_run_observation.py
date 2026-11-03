"""Persist observed unittest outcomes before enforcing complete invocation inventory.

A setup-class error leaves methods NOT_EXECUTED. A summary never changes that
failure into PASS; the native caller records the independently observed exit.
"""
from __future__ import annotations
import json,os,tempfile
from pathlib import Path


def observe_test_result(result,collected_ids,started_ids,finished_ids):
    collected=list(collected_ids);started=list(started_ids);finished=list(finished_ids)
    missing=[value for value in collected if value not in set(started)]
    extra=[value for value in started if value not in set(collected)]
    inventory_ok=started==collected and finished==collected
    successful=result.wasSuccessful() and inventory_ok
    errors=[{'test':test.id(),'traceback':trace} for test,trace in result.errors]
    return {'collected':len(collected),'tests':result.testsRun,
        'executed':result.testsRun-len(result.skipped),'collectedIDs':collected,
        'startedIDs':started,'finishedIDs':finished,'missingIDs':missing,'extraIDs':extra,
        'orderedInventoryMatched':inventory_ok,'errors':len(result.errors),
        'errorObservations':errors,'setUpClassErrors':[row for row in errors if row['test'].startswith('setUpClass (')],
        'failures':len(result.failures),'skips':[{'test':t.id(),'reason':v} for t,v in result.skipped],
        'success':successful,'intendedExitStatus':0 if successful else 1,
        'nativeExitStatus':'OBSERVED_SEPARATELY_BY_PARENT'}


def write_observed_result(path,value):
    """Atomically publish a new summary; never replace an existing observation."""
    path=Path(path)
    raw=(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2)+'\n').encode('utf-8')
    fd,temp=tempfile.mkstemp(prefix='.'+path.name+'-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.link(temp,path)
    finally:
        Path(temp).unlink()