"""Read-only imported observation report. Imported artifacts never gain verifier authority."""
from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path
from deploom_mcp.goal_contract import GoalContractError, assert_current_binding, canonical_digest, validate_goal

_RUN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_STATUSES = {"PASS", "FAIL", "NOT_RUN", "UNKNOWN", "UNSUPPORTED"}

def _plain_under(path: Path, root: Path) -> Path:
    # Containment is not execution authority. Inspect BOTH original spellings
    # before resolving them, so canonicalization cannot hide a junction/root
    # redirect. Walk to the anchor: 8.3 and long ancestors need not compare
    # lexically even when Windows resolves them to the same authorized root.
    raw_path = Path(path).absolute()
    raw_root = Path(root).absolute()
    try:
        for raw in (raw_path, raw_root):
            for component in (raw, *raw.parents):
                try:
                    info = component.lstat()
                except FileNotFoundError:
                    continue  # a not-yet-created artifact tail is permitted
                if (component.is_symlink()
                        or (hasattr(component, "is_junction") and component.is_junction())
                        or getattr(info, "st_file_attributes", 0) & 0x400):
                    raise GoalContractError("INVALID_GOAL_ARTIFACT_PATH")
        if not raw_path.resolve().is_relative_to(raw_root.resolve()):
            raise GoalContractError("INVALID_GOAL_ARTIFACT_PATH")
    except OSError as exc:
        raise GoalContractError("INVALID_GOAL_ARTIFACT_PATH") from exc
    return path

def load_run(run_id: str, expected_goal_digest: str, state_root: Path):
    if not isinstance(run_id,str) or not _RUN.fullmatch(run_id) or not isinstance(expected_goal_digest,str) or not _SHA.fullmatch(expected_goal_digest):
        raise GoalContractError("INVALID_GOAL_REPORT_REQUEST")
    root = state_root / "goal-runs"
    path = _plain_under(root / (run_id+".json"), state_root)
    if path.stat().st_size > 1024*1024:raise GoalContractError("GOAL_RECORD_TOO_LARGE")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record,dict) or record.get("schemaVersion") != 1 or record.get("runId") != run_id:
        raise GoalContractError("INVALID_GOAL_RUN_RECORD")
    goal = validate_goal(record.get("goal"))
    if canonical_digest(goal) != expected_goal_digest:
        raise GoalContractError("GOAL_DIGEST_MISMATCH")
    return record, goal

def _reported_attempts(record):
    attempts=record.get("testAttempts",[])
    if not isinstance(attempts,list) or len(attempts)>8:raise GoalContractError("INVALID_TEST_ATTEMPTS")
    result=[]
    for attempt in attempts:
        if not isinstance(attempt,dict) or not _RUN.fullmatch(str(attempt.get("id",""))) or attempt.get("environment") not in {"SANDBOX","APPROVED_LOCAL"}:
            raise GoalContractError("INVALID_TEST_ATTEMPTS")
        counts=attempt.get("counts",{})
        if not isinstance(counts,dict) or set(counts)!={"total","passed","failed","pending"} or any(type(v) is not int or not 0<=v<=1000000 for v in counts.values()):
            raise GoalContractError("INVALID_TEST_ATTEMPTS")
        if counts["total"]!=counts["passed"]+counts["failed"]+counts["pending"]:
            raise GoalContractError("INVALID_TEST_ATTEMPTS")
        result.append({"id":attempt["id"],"environment":attempt["environment"],"reportedCounts":counts})
    selected=record.get("selectedTestAttemptId")
    if selected is not None and selected not in {a["id"] for a in result}:raise GoalContractError("INVALID_TEST_ATTEMPTS")
    return {"attempts":result,"selectedAttemptId":selected,"authority":"CLIENT_REPORTED"}

def summarize_imported(record: dict, goal: dict, current_plan: dict, state_root: Path) -> dict:
    """Even matching files/hashes prove artifact identity, not actual command execution."""
    if not current_plan.get("ok"):
        raise GoalContractError("CURRENT_GOAL_BINDING_UNAVAILABLE")
    saved, current = record.get("binding"), current_plan.get("binding")
    if not isinstance(saved,dict) or not isinstance(current,dict):
        raise GoalContractError("STALE_OR_INVALID_GOAL_BINDING")
    assert_current_binding(saved,goal,saved.get("identity",{}))
    assert_current_binding(current,goal,current.get("identity",{}))
    if any(saved["identity"][key]!=current["identity"][key] for key in current["identity"] if key!="implementationDigest"):
        raise GoalContractError("STALE_OR_INVALID_GOAL_BINDING")
    profile = record.get("profileIdentity",{})
    if not isinstance(profile,dict) or not _RUN.fullmatch(str(profile.get("id",""))) or not _SHA.fullmatch(str(profile.get("sha256",""))):
        raise GoalContractError("INVALID_PROFILE_IDENTITY")
    parent = record.get("parentRunId")
    if parent is not None and (not isinstance(parent,str) or not _RUN.fullmatch(parent)):
        raise GoalContractError("INVALID_PARENT_RUN_ID")
    raw = record.get("observations",{})
    if not isinstance(raw,dict) or set(raw)-set(goal["requiredChecks"]):
        raise GoalContractError("INVALID_GOAL_OBSERVATIONS")
    checks=[]
    for name in goal["requiredChecks"]:
        observation=raw.get(name,{"status":"NOT_RUN"})
        if not isinstance(observation,dict) or observation.get("status") not in _STATUSES:
            raise GoalContractError("INVALID_GOAL_OBSERVATIONS")
        status=observation["status"];reason="IMPORTED_OBSERVATION"
        artifact=observation.get("artifact")
        if status in {"PASS","FAIL"}:
            if not isinstance(artifact,dict) or not isinstance(artifact.get("relativePath"),str) or not _SHA.fullmatch(str(artifact.get("sha256",""))):
                status,reason="UNKNOWN","MISSING_ARTIFACT_IDENTITY"
            else:
                relative=Path(artifact["relativePath"])
                if relative.is_absolute() or ".." in relative.parts:raise GoalContractError("INVALID_GOAL_ARTIFACT_PATH")
                path=_plain_under(state_root/"goal-runs"/relative,state_root)
                try:
                    if path.stat().st_size > 8*1024*1024 or hashlib.sha256(path.read_bytes()).hexdigest()!=artifact["sha256"]:
                        status,reason="UNKNOWN","ARTIFACT_IDENTITY_MISMATCH"
                except OSError:status,reason="UNKNOWN","ARTIFACT_UNAVAILABLE"
        checks.append({"id":name,"reportedStatus":status,"reason":reason,"authority":"CLIENT_REPORTED"})
    locked=current_plan.get("lockedTargets",{})
    target_match=all(locked.get(n)==v for n,v in goal["exactTargets"].items())
    failed=any(c["reportedStatus"]=="FAIL" for c in checks)
    return {"ok":True,"kind":"goal-report","goalKind":goal["goalKind"],"runId":record["runId"],"goalDigest":canonical_digest(goal),
        "attainment":"not_met" if failed or not target_match else "unknown",
        "verification":"NOT_VERIFIED","verificationStatus":"NOT_VERIFIED","verificationAuthority":"CLIENT_REPORTED",
        "bindingStatus":"MATCH", "observationOrigin":"IMPORTED_ARTIFACTS", "fullAcceptance":False,
        "profileIdentity":{"id":profile["id"],"sha256":profile["sha256"]},"parentRunId":parent,
        "observationProducerIdentity":record.get("producerIdentity",{}).get("sha256") if isinstance(record.get("producerIdentity"),dict) and _SHA.fullmatch(str(record.get("producerIdentity",{}).get("sha256",""))) else "UNKNOWN",
        "planningImplementationDigest":saved["identity"]["implementationDigest"],
        "reportingImplementationDigest":current["identity"]["implementationDigest"],
        "observationsProveCurrentWorkspace":False, "testObservations":_reported_attempts(record),
        "reason":"REPORTED_MANDATORY_FAILURE_OR_TARGET_MISMATCH" if failed or not target_match else "CONTROLLED_VERIFICATION_REQUIRED",
        "targetProgress":{"required":len(goal["exactTargets"]),"lockedMatches":sum(locked.get(n)==v for n,v in goal["exactTargets"].items()),"installedObservation":"NOT_EVALUATED"},
        "checks":{"required":len(goal["requiredChecks"]),"reportedPass":sum(c["reportedStatus"]=="PASS" for c in checks),"items":checks},
        "health":{"policy":"REPORT_ONLY","observation":"NOT_EVALUATED"},"hardPolicyStatus":"NOT_EVALUATED",
        "legacyPublication":"NOT_PERFORMED","remainingWork":[c["id"] for c in checks],
        "note":"Imported logs are observations, not controlled verification proof; even all reported PASS cannot achieve the goal."}

async def get_goal_report(run_id: str, expected_goal_digest: str, plan_reader):
    root=Path(os.path.abspath(os.path.expanduser(os.environ.get("DEPLOOM_MCP_STATE_DIR",str(Path.home()/".deploom-mcp")))))
    try:
        record,goal=load_run(run_id,expected_goal_digest,root)
        current=await plan_reader(record.get("projectPath"),goal,30.0)
        return summarize_imported(record,goal,current,root)
    except GoalContractError as exc:return {"ok":False,"error":{"code":exc.code},"verification":"NOT_VERIFIED"}
    except (OSError,ValueError,TypeError,KeyError):return {"ok":False,"error":{"code":"GOAL_REPORT_UNAVAILABLE"},"verification":"NOT_VERIFIED"}
