#!/usr/bin/env python3
"""D1: actual Chalk-specific value gate, real packaged MCP stdio, NO model.

A successful stdio handshake is not an actionable upgrade recommendation.
If the exact approved chalk@5.3.0 target is not proposed with intact is-number
scope, publish a typed NO_GO and keep paid A/B sessions disabled.
Only whitelisted statuses/semver and SHA hashes leave this process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import venv

from correctness_oracle import source_files

EXPECTED_SOURCE_SHA = "4adfa455d02e9f354e5b47e7b4d983f6676b620b13dc1454bda9248272d97cb6"
EXPECTED_CORE_SHA = "49232ab510da8fa854f36d84a639f3db28ab363c"
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$")
STATUSES = {
    "PROPOSED", "NO_TARGET", "NO_CHANGE", "POLICY_SATISFIED", "BLOCKED",
    "UNKNOWN_SECURITY", "UNKNOWN_METADATA", "CANDIDATE_SEARCH_TRUNCATED",
    "EXCLUDED", "PEER_UNRESOLVED", "UNKNOWN", "DEFERRED",
}
# Runtime is installed from the provided wheel, NEVER imported from editable source.
RUNTIME = r"""
import asyncio,hashlib,json,os,pathlib,sys
from mcp import ClientSession,StdioServerParameters,stdio_client
import deploom_mcp
from deploom_mcp.distribution import release_manifest_path,resolve_core_dir
root=pathlib.Path(sys.prefix).resolve()
core=resolve_core_dir().resolve()
assert pathlib.Path(deploom_mcp.__file__).resolve().is_relative_to(root)
assert core.is_relative_to(root)
manifest=json.loads(release_manifest_path().read_text())
assert manifest["core"]["revision"]==sys.argv[2]
assert hashlib.sha256((core/"registry_candidate_diagnostics.py").read_bytes()).hexdigest()==manifest["core"]["files"]["registry_candidate_diagnostics.py"]
project=pathlib.Path(sys.argv[1])
def decode(r): return r.structured_content or json.loads(r.content[0].text)
async def run():
 env={k:v for k,v in os.environ.items() if k!="PYTHONPATH" and not k.startswith("DEPLOOM_")}
 env["DEPLOOM_MCP_STATE_DIR"]=str(pathlib.Path.cwd()/"mcp-state")
 params=StdioServerParameters(command=sys.executable,args=["-I","-m","deploom_mcp"],cwd=str(pathlib.Path.cwd()),env=env)
 async with stdio_client(params) as (reader,writer):
  async with ClientSession(reader,writer) as session:
   await session.initialize()
   tools=await session.list_tools()
   if "get_upgrade_plan" not in {t.name for t in tools.tools}: return {"toolAvailable":False}
   base={"project_path":str(project),"registry":"https://registry.npmjs.org",
         "target_level":"yellow","min_lag_ok_pct":80,"deadline_seconds":40,
         "use_system_proxy":True,"registry_metadata_ttl":0,
         "draft_max_candidates":5}
   requests=[
    dict(base,target_mode="pinned",mutable_packages=["chalk"],
         approved_version_lines={"chalk":[5]}),
    dict(base,target_mode="advisory",
         objective="latest-compatible-in-approved-lines",
         mutable_packages=["chalk"],approved_version_lines={"chalk":[5]})
   ]
   responses=[]
   for n,query in enumerate(requests):
    query["output_dir"]=str(pathlib.Path.cwd()/("plan-"+str(n)))
    try:
     response=decode(await session.call_tool("get_upgrade_plan",query))
     status=response.get("status") or {}
     rows={r.get("package"):r for r in response.get("dependencies",[]) if isinstance(r,dict)}
     chalk=rows.get("chalk") or {}
     companion=rows.get("is-number") or {}
     def safe_status(v):
      return v if type(v) is str and v in {
       "PROPOSED","NO_TARGET","NO_CHANGE","POLICY_SATISFIED","BLOCKED",
       "UNKNOWN_SECURITY","UNKNOWN_METADATA","CANDIDATE_SEARCH_TRUNCATED",
       "EXCLUDED","PEER_UNRESOLVED","UNKNOWN","DEFERRED"} else None
     def safe_version(v):
      import re
      return v if type(v) is str and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+",v) else None
     responses.append({
       "ok":response.get("ok") is True,
       "errorCode":(response.get("error") or {}).get("code")
           if (response.get("error") or {}).get("code") in {
            "decision-contract-unsupported","decision-contract-mismatch",
            "invalid-args","invalid-policy","internal-error","timeout"} else "OTHER_OR_NONE",
       "draftStatus":status.get("draftStatus") if status.get("draftStatus") in {"DRAFT_READY","DRAFT_PARTIAL"} else "OTHER",
       "authority":status.get("authority") if status.get("authority") in {"PLANNING_ONLY","UNKNOWN"} else "OTHER",
       "verificationStatus":status.get("verificationStatus") if status.get("verificationStatus") in {"NOT_VERIFIED","UNKNOWN"} else "OTHER",
       "chalkStatus":safe_status(chalk.get("status")),
       "chalkTarget":safe_version(chalk.get("target")),
       "companionStatus":safe_status(companion.get("status")),
       "companionTarget":safe_version(companion.get("target")),
       "toolAvailable":True,
     })
    except Exception:
     responses.append({"ok":False,"errorCode":"CALL_FAILED","toolAvailable":True})
   return {"responses":responses,"toolAvailable":True,
           "coreRevision":manifest["core"]["revision"],
           "coreInventorySha256":manifest["core"]["contentSha256"],
           "mcpVersion":manifest["mcpVersion"],
           "skillBundleSha256":manifest["skill"]["bundleSha256"]}
print(json.dumps(asyncio.run(run()),sort_keys=True))
"""


def source_sha(path: Path) -> str:
    files = source_files(path)
    return hashlib.sha256(json.dumps(
        files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def evaluate(payload: dict) -> dict:
    rows = payload.get("responses") or []
    exact = [
        p for p in rows
        if p.get("ok") and p.get("authority") == "PLANNING_ONLY"
        and p.get("verificationStatus") == "NOT_VERIFIED"
        and p.get("draftStatus") == "DRAFT_READY"
        and p.get("chalkStatus") == "PROPOSED"
        and p.get("chalkTarget") == "5.3.0"
        and p.get("companionStatus") != "PROPOSED"
        and p.get("companionTarget") in (None, "7.0.0")
    ]
    if exact:
        return {"gate":"GO", "productGap":None}
    if any(p.get("chalkTarget") not in (None, "5.3.0") for p in rows):
        return {"gate":"NO_GO","productGap":"NO_EXACT_CHALK_5_3_0_TARGET"}
    if any(p.get("chalkStatus") in {"NO_CHANGE","POLICY_SATISFIED"} for p in rows):
        return {"gate":"NO_GO","productGap":"POLICY_ALREADY_SATISFIED_NO_MIGRATION_ACTION"}
    if any(p.get("errorCode")=="decision-contract-unsupported" for p in rows):
        return {"gate":"NO_GO","productGap":"UNSUPPORTED_EXACT_MIGRATION_DECISION_CONTRACT"}
    if any(p.get("chalkStatus") in {"UNKNOWN_SECURITY","UNKNOWN_METADATA","BLOCKED","CANDIDATE_SEARCH_TRUNCATED"} for p in rows):
        return {"gate":"NO_GO","productGap":"UNRESOLVED_PLANNING_OR_SECURITY"}
    return {"gate":"NO_GO","productGap":"NO_ACTIONABLE_CHALK_TARGET"}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen",type=Path,required=True)
    parser.add_argument("--wheel",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--require-go",action="store_true")
    args=parser.parse_args()
    report={"gate":"NO_GO","productGap":"INFRASTRUCTURE_PRECHECK_FAILED",
            "modelCalls":0,"authority":"PLANNING_ONLY",
            "frozenExpectedSha256":EXPECTED_SOURCE_SHA}
    start=time.monotonic()
    try:
        project=args.frozen.resolve(strict=True)
        wheel=args.wheel.resolve(strict=True)
        observed=source_sha(project)
        report["frozenObservedSha256"]=observed
        report["wheelSha256"]=hashlib.sha256(wheel.read_bytes()).hexdigest()
        if observed!=EXPECTED_SOURCE_SHA:
            report["productGap"]="FROZEN_BASELINE_SHA_DRIFT"
        else:
            with tempfile.TemporaryDirectory(prefix="d1-real-mcp-") as temp:
                work=Path(temp)
                venv.EnvBuilder(with_pip=True).create(work/"venv")
                python=work/"venv"/("Scripts/python.exe" if os.name=="nt" else "bin/python")
                t=time.monotonic()
                installed=subprocess.run(
                    [str(python),"-m","pip","install","--disable-pip-version-check",
                     "--no-input",str(wheel)],capture_output=True,timeout=150)
                report["mcpSetupWallSeconds"]=round(time.monotonic()-t,3)
                if installed.returncode:
                    report["productGap"]="PACKAGED_WHEEL_INSTALL_FAILED"
                else:
                    env={k:v for k,v in os.environ.items()
                         if k!="PYTHONPATH" and not k.startswith("DEPLOOM_")}
                    t=time.monotonic()
                    process=subprocess.run([str(python),"-I","-c",RUNTIME,str(project),EXPECTED_CORE_SHA],
                                           cwd=work,env=env,capture_output=True,timeout=180)
                    report["mcpPlanWallSeconds"]=round(time.monotonic()-t,3)
                    if process.returncode:
                        report["productGap"]="PACKAGED_MCP_CALL_FAILED"
                    else:
                        payload=json.loads(process.stdout.decode("utf-8").splitlines()[-1])
                        report.update(evaluate(payload))
                        report["requests"]=payload.get("responses",[])
                        report["coreRevision"]=payload.get("coreRevision")
                        report["coreInventorySha256"]=payload.get("coreInventorySha256")
                        report["skillBundleSha256"]=payload.get("skillBundleSha256")
                    report["sourceUnchanged"]=source_sha(project)==observed
                    if not report["sourceUnchanged"]:
                        report.update(gate="NO_GO",productGap="PLANNER_MUTATED_SOURCE")
    except Exception as exc:
        report["gate"]="NO_GO"
        report["productGap"]="PREFLIGHT_"+type(exc).__name__
    report["wallSeconds"]=round(time.monotonic()-start,3)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"gate":report["gate"],"productGap":report["productGap"],
                      "modelCalls":0,"requests":report.get("requests")},sort_keys=True))
    return 2 if args.require_go and report["gate"]!="GO" else 0


if __name__=="__main__":
    raise SystemExit(main())
