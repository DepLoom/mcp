"""Bounded Core inspection or exact host-registered isolated verification."""
import hashlib,json,sys
from deploom_mcp.distribution import release_manifest_path,resolve_core_dir

def main():
    core=resolve_core_dir()
    manifest=json.loads(release_manifest_path().read_text(encoding="utf-8"))
    for name,expected in manifest["core"]["files"].items():
        path=core/name
        if path.is_symlink() or not path.resolve().is_relative_to(core.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            print(json.dumps({"code":"CORE_INTEGRITY_MISMATCH"}));return 1
    sys.path.insert(0,str(core))
    from baseline_constraint_verifier import verify_assignment
    import inspect
    code=("CORE_ATTEMPT_RECEIPT_PROVIDER_UNAVAILABLE" if "invocation_observer" not in inspect.signature(verify_assignment).parameters
          else "CANDIDATE_EXECUTION_ADMISSION_NOT_READY")
    if len(sys.argv)==5:
        from deploom_mcp.goal_contract import GoalContractError,canonical_digest
        from deploom_mcp.goal_verifier_bridge import execute_registered_private_candidate
        try:
            record=execute_registered_private_candidate(*sys.argv[1:])
            code=(record["status"] if record.get("candidateProfileAccepted") is True
                else "HOST_AUTHORIZED_PRIVATE_VERIFICATION_OBSERVED" if record["authorizationOrigin"]=="PREAPPROVED_HOST_POLICY"
                else "ENGINEERING_CONTROLLED_VERIFICATION_OBSERVED")
            print(json.dumps({"code":code,"receiptDigest":canonical_digest(record),"fullAcceptance":False}));return 0
        except GoalContractError as exc:
            print(json.dumps({"code":exc.code,"verifierInvoked":None,"fullAcceptance":False}));return 1
    print(json.dumps({"code":code,"verifierInvoked":False,"fullAcceptance":False}));return 0

if __name__=="__main__":raise SystemExit(main())
