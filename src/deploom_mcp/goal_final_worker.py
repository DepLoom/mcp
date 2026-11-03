"""Fixed host-registered final worker; stdout never grants final authority."""
import json,re,sys
from deploom_mcp.goal_contract import canonical_digest
from deploom_mcp.goal_final_checkpoint import execute_final_candidate,read_final_checkpoint

def main():
    try:
        if len(sys.argv)==6 and sys.argv[1]=='apply':result=execute_final_candidate(*sys.argv[2:])
        elif len(sys.argv)==5 and sys.argv[1]=='read':result=read_final_checkpoint(*sys.argv[2:])
        elif len(sys.argv) in (6,7) and sys.argv[1]=='read-budgeted':
            expected=json.loads(sys.argv[6]) if len(sys.argv)==7 else None
            result=read_final_checkpoint(*sys.argv[2:5],_proof_deadline_utc=float(sys.argv[5]),_expected_terminal=expected)
        else:raise ValueError('INVALID_FINAL_WORKER_ARGUMENTS')
        print(json.dumps({'ok':True,'resultDigest':canonical_digest(result),'result':result}));return 0
    except Exception as exc:
        code=getattr(exc,'code',str(exc).split(':')[0])
        if not re.fullmatch('[A-Z][A-Z0-9_]{1,100}',str(code)):code='FINAL_OPERATION_UNCONFIRMED'
        print(json.dumps({'ok':False,'error':{'code':code},'fullAcceptance':False}));return 1
if __name__=='__main__':raise SystemExit(main())
