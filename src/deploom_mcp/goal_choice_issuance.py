"""Host-owned record of actual Core selection issuance, not imported proof.

The configured host filesystem is trusted; this is not hostile-writer attestation.
A selection JSON alone cannot enter execution without its separately issued record.
"""
from __future__ import annotations
import dataclasses
import json
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_report import _plain_under,_SHA

_FIELDS={'schemaVersion','kind','selectionDigest','parentBindingDigest','modelDigest','implementationDigest','solverResult'}

def _path(root,digest):
    if not isinstance(digest,str) or not _SHA.fullmatch(digest):raise GoalContractError('INVALID_CHOICE_DIGEST')
    return _plain_under(root/'choice-issuance'/(digest+'.json'),root)

def validate_issuance(root,receipt,digest):
    path=_path(root,digest)
    if not path.is_file():raise GoalContractError('CORE_SELECTION_ISSUANCE_MISSING')
    if path.stat().st_size>256*1024:raise GoalContractError('CORE_SELECTION_ISSUANCE_INVALID')
    value=json.loads(path.read_bytes())
    if (not isinstance(value,dict) or set(value)!=_FIELDS or type(value['schemaVersion']) is not int
            or value['schemaVersion']!=1 or value['kind']!='CORE_SELECTION_ISSUANCE_V1'
            or value['selectionDigest']!=digest
            or value['parentBindingDigest']!=receipt['parentBinding']['bindingDigest']
            or value['modelDigest']!=receipt['modelDigest']
            or value['implementationDigest']!=receipt['parentBinding']['identity']['implementationDigest']):
        raise GoalContractError('CORE_SELECTION_ISSUANCE_INVALID')
    result=value['solverResult']
    if (not isinstance(result,dict) or set(result)!={'backend','status','assignment','score','states','detail','elapsed_ms','operation_id'}
            or result['backend']!='z3' or result['status']!='optimal'
            or result['assignment']!=receipt['assignment'] or result['score']!=receipt['score']
            or not isinstance(result['operation_id'],str) or not 1<=len(result['operation_id'])<=128):
        raise GoalContractError('CORE_SELECTION_ISSUANCE_INVALID')
    return canonical_digest(value)

def issue_selection(root,receipt,digest,result):
    """Only the local worker invokes this after its actual validated Core result."""
    raw_result=dataclasses.asdict(result)
    # JSON round-trip normalizes the score tuple before the common validation.
    value=json.loads(json.dumps({'schemaVersion':1,'kind':'CORE_SELECTION_ISSUANCE_V1',
        'selectionDigest':digest,'parentBindingDigest':receipt['parentBinding']['bindingDigest'],
        'modelDigest':receipt['modelDigest'],
        'implementationDigest':receipt['parentBinding']['identity']['implementationDigest'],
        'solverResult':raw_result},allow_nan=False))
    if (result.status!='optimal' or result.backend!='z3' or result.assignment!=receipt['assignment']
            or list(result.score or ())!=receipt['score'] or not result.operation_id):
        raise GoalContractError('CORE_SELECTION_ISSUANCE_INVALID')
    path=_path(root,digest);path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False))
    except FileExistsError:
        # An existing same-binding issuance is idempotent. Never replace another
        # invocation record or repair a corrupt ledger by silently recomputing it.
        pass
    return validate_issuance(root,receipt,digest)
