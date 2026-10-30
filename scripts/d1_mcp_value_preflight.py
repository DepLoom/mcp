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
EXPECTED_CORE_SHA = "d1594c36eb5b35447e941cf0f528b8cbd15705b5"
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$")
STATUSES = {
    "PROPOSED", "NO_TARGET", "NO_CHANGE", "POLICY_SATISFIED", "BLOCKED",
    "UNKNOWN_SECURITY", "UNKNOWN_METADATA", "CANDIDATE_SEARCH_TRUNCATED",
    "EXCLUDED", "PEER_UNRESOLVED", "UNKNOWN", "DEFERRED",
}
# Runtime is installed from the provided wheel, NEVER imported from editable source.
RUNTIME = r"""
import asyncio,hashlib,json,os,pathlib,sys,re
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
   tool=next(t for t in tools.tools if t.name=="get_upgrade_plan")
   tool_schema=getattr(tool,"inputSchema",None)
   schema=tool_schema if isinstance(tool_schema,dict) else {}
   # MCP SDKs may expose inputSchema through a serialization alias.
   alternate=tool.model_dump(by_alias=True) if hasattr(tool,"model_dump") else {}
   if not schema and isinstance(alternate,dict):
    schema=alternate.get("inputSchema") or alternate.get("input_schema") or {}
   properties=schema.get("properties") if isinstance(schema,dict) else {}
   properties=properties if isinstance(properties,dict) else {}
   known_fields=("project_path","target_level","mutable_packages",
                 "approved_version_lines","required_packages",
                 "draft_max_candidates")
   observed_fields=[name for name in known_fields if name in properties]
   supports_required="required_packages" in properties
   base={"project_path":str(project),"registry":"https://registry.npmjs.org",
         "target_level":"yellow","min_lag_ok_pct":80,"deadline_seconds":75,
         "use_system_proxy":True,"registry_metadata_ttl":0,
         "draft_max_candidates":128}
   requests=[
    dict(base,target_mode="pinned",mutable_packages=["chalk"],
         approved_version_lines={"chalk":[5]}),
    dict(base,target_mode="advisory",
         objective="latest-compatible-in-approved-lines",
         mutable_packages=["chalk"],approved_version_lines={"chalk":[5]}),
    # This is the Core-supported legacy REQUIRED movement policy. It is NOT
    # a pinned exact patch version and must not become an A/B GO on coincidence.
    dict(base,target_mode="pinned",mutable_packages=["chalk"],
         approved_version_lines={"chalk":[5]},required_packages=["chalk"]),
    # New general V2 exact objective; deliberate user target, not latest guess.
    dict(base,target_mode="pinned",objective="explicit-version-targets",
         mutable_packages=["chalk"],approved_version_lines={"chalk":[5]},
         exact_targets={"chalk":"5.3.0"}),
    # Clearly separate PUBLIC read-only scenarios. No change to frozen P2,
    # initial exact target, excluded packages, or effective Yellow policy.
    dict(base,target_mode="pinned",objective="explicit-version-targets",
         mutable_packages=["chalk"],approved_version_lines={"chalk":[5]},
         exact_targets={"chalk":"5.6.2"}),
    dict(base,target_mode="pinned",objective="explicit-version-targets",
         mutable_packages=["chalk"],approved_version_lines={"chalk":[6]},
         exact_targets={"chalk":"6.0.0"}),
   ]
   scenario_ids=("LEGACY_PINNED","LATEST_IN_APPROVED_5","LEGACY_REQUIRED",
       "ORIGINAL_EXACT_5_3_0","SEPARATE_MAJOR5_5_6_2",
       "SEPARATE_CURRENT_POLICY_MIN_6_0_0")
   responses=[]
   for n,query in enumerate(requests):
    query["output_dir"]=str(pathlib.Path.cwd()/("plan-"+str(n)))
    try:
     response=decode(await session.call_tool("get_upgrade_plan",query))
     status=response.get("status") or {}
     decision=(response.get("policy") or {}).get("decisionContract") or {}
     expected_target=(query.get("exact_targets") or {}).get("chalk")
     explicit=(
       query.get("objective")=="explicit-version-targets"
       and decision.get("version")==2
       and decision.get("objective")=="explicit-version-targets"
       and decision.get("exactTargets")=={"chalk":expected_target}
     )
     rows={r.get("package"):r for r in response.get("dependencies",[]) if isinstance(r,dict)}
     chalk=rows.get("chalk") or {}
     companion=rows.get("is-number") or {}
     # Inspect the actual Core plan artifact, but never emit raw notes/URLs.
     # Distinguish an unavailable exact candidate from solver/peer blocking.
     safe_plan_reasons=set()
     public_codes=("EXACT_TARGET_UNAVAILABLE","EXACT_TARGET_SCOPE_CONFLICT",
       "EXACT_TARGET_NOT_AN_UPGRADE","EXPLICIT_USER_TARGET",
       "USER_EXACT_TARGET","PROJECT_NODE_ENGINE_CONFLICT",
       "REGISTRY_TARGET_UNAVAILABLE","PEER_RESOLUTION_DEFERRED")
     # Existing Core's safe, fixed reason fragments disclose WHICH admission
     # guard denied the real exact version without exposing registry URLs,
     # tokens, free-form errors or raw planner output.
     guard_reasons={
       "target absent from registry metadata":"TARGET_VERSION_METADATA_ABSENT",
       "registry tarball origin or metadata rejected":"TARGET_TARBALL_ORIGIN_OR_METADATA_REJECTED",
       "type declaration guard rejected target":"TARGET_TYPES_GUARD_BLOCKED",
       "runtime entrypoint guard rejected target":"TARGET_ENTRYPOINT_GUARD_BLOCKED",
     }
     plan_path=(response.get("artifacts") or {}).get("plan")
     if isinstance(plan_path,str):
      try:
       candidate=pathlib.Path(plan_path).resolve()
       if candidate.is_relative_to(pathlib.Path.cwd().resolve()) and candidate.is_file() and candidate.stat().st_size<16_000_000:
        raw_plan=json.loads(candidate.read_text(encoding="utf-8"))
        for proposal in raw_plan.get("proposals",[]):
         for item in proposal.get("rows",[]):
          if item.get("package")!="chalk":continue
          for field in ("objectiveNote","conflict","reason","targetDefaultReason"):
           text_field=str(item.get(field) or "")
           for code in public_codes:
            if code in text_field: safe_plan_reasons.add(code)
           for fragment,code in guard_reasons.items():
            if fragment in text_field: safe_plan_reasons.add(code)
      except (OSError,ValueError,TypeError,KeyError):
       pass
     regdiag=chalk.get("registryCandidateDiagnostics") or {}
     if not isinstance(regdiag,dict):regdiag={}
     counts=regdiag.get("rejectionCounts") or {}
     if not isinstance(counts,dict):counts={}
     safe_counts={k: v for k,v in counts.items()
        if k in {"FOREIGN_TARBALL_ORIGIN","BAD_REGISTRY_PATH",
                 "MISSING_DIST_TARBALL","HTTP_ARTIFACT_UNAVAILABLE"}
        and type(v) is int and 0<=v<=100000}
     safe_codes={"CANDIDATE_CAP","NO_INSTALLABLE_ARTIFACT",
        "NO_PUBLISHED_UPGRADE_CANDIDATE",
        "UNRESOLVED_TARGET_CONSTRAINT_OR_POLICY"}
     code=chalk.get("noTargetDiagnosticCode")
     if code not in safe_codes:code=None
     reason=str(chalk.get("reason") or "")
     # A failed Core subprocess can embed paths / URLs in error.detail.
     # Publish only known exception / policy class names and a numeric exit.
     error_obj=response.get("error") if isinstance(response.get("error"),dict) else {}
     error_detail=error_obj.get("detail") or ""
     error_detail=error_detail if isinstance(error_detail,str) else ""
     safe_failure_classes=(
         "DECISION_CONTRACT_INVALID","BaselineConstraintVerificationError",
         "ValueError","TypeError","KeyError","AttributeError","RuntimeError",
         "AssertionError","UnboundLocalError","OverflowError","SyntaxError",
         "ModuleNotFoundError","ImportError","Z3Exception","unsat",
         "required update","required assignment","candidate search",
         "USER_BASELINE_REQUIRED_UPDATE", "NO_FEASIBLE_ASSIGNMENT",
         "PROJECT_NODE_ENGINE_CONFLICT","USER_APPROVED_VERSION_LINE",
         "EXACT_SOLVER_UNSAT","manifest", "lockfile",
         "NO_INSTALLABLE_ARTIFACT","Traceback")
     safe_failure_markers=[x for x in safe_failure_classes if x.lower() in error_detail.lower()]
     planner_message=str(error_obj.get("message") or "")
     exit_match=re.search(r"planner exited with code ([0-9]{1,3})",planner_message)
     exit_code=int(exit_match.group(1)) if exit_match else None
     # Core failure's machine envelope can be in the bounded error.detail.
     # Never publish raw stderr, summary, rootCause, paths or URL. Only
     # stable type/attribute diagnostics explicitly matched below.
     core_exception={"class":None,"identifier":None,"code":None}
     for label in ("NameError","AttributeError","TypeError","KeyError",
                   "ValueError","IndexError","AssertionError","RuntimeError",
                   "FileNotFoundError"):
      if label in error_detail:
       core_exception["class"]=label
       break
     ident=re.search(r"name ['\"]([A-Za-z_][A-Za-z0-9_]{0,75})['\"] is not defined",error_detail)
     if ident:
      core_exception["identifier"]=ident.group(1)
     ident=re.search(r"has no attribute ['\"]([A-Za-z_][A-Za-z0-9_]{0,75})['\"]",error_detail)
     if ident:
      core_exception["identifier"]=ident.group(1)
     for code_label in ("TOOL_INTERNAL_ERROR","EXACT_SOLVER_UNSAT_PROVEN",
                        "FINAL_PROVEN_ASSIGNMENT_REGISTRY_DRIFT",
                        "SOURCE_SNAPSHOT_INTEGRITY_FAILED"):
      if code_label in error_detail:
       core_exception["code"]=code_label
       break
     # Whitelist typed symbolic categories; never propagate free-text strings.
     reason_codes=[
         key for key in (
           "LATEST_CANDIDATE_UNKNOWN","USER_APPROVED_VERSION_LINE",
           "PEER_RESOLUTION_DEFERRED","NO_INSTALLABLE_ARTIFACT",
           "candidate list capped","policy-driven target",
           "no installable","нет policy-driven target",
           "runtime","engine","type declarations",
         ) if key.lower() in reason.lower()
     ]
     def safe_status(v):
      return v if type(v) is str and v in {
       "PROPOSED","NO_TARGET","NO_CHANGE","POLICY_SATISFIED","BLOCKED",
       "UNKNOWN_SECURITY","UNKNOWN_METADATA","CANDIDATE_SEARCH_TRUNCATED",
       "EXCLUDED","PEER_UNRESOLVED","UNKNOWN","DEFERRED"} else None
     def safe_version(v):
      import re
      return v if type(v) is str and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+",v) else None
     responses.append({
       "scenarioId":scenario_ids[n],
       "expectedExactTarget":expected_target,
       "approvedMajors":(query.get("approved_version_lines") or {}).get("chalk"),
       "ok":response.get("ok") is True,
       # Safe typed code only: do not serialize untrusted error messages,
       # subprocess stderr, registry URLs or environment paths.
       "coreFailureMarkers":safe_failure_markers,
       "coreExitCode":exit_code,
       "coreExceptionSanitized":core_exception,
       "errorCode":((error_code if re.fullmatch(r"[a-z][a-z0-9_-]{0,79}",error_code)
                      else "UNSAFE_OR_MISSING_CODE")
                     if isinstance((error_code := ((response.get("error") or {}).get("code")
                                                   if isinstance(response.get("error"),dict)
                                                   else None)),str)
                     else "OTHER_OR_NONE"),
       "draftStatus":status.get("draftStatus") if status.get("draftStatus") in {"DRAFT_READY","DRAFT_PARTIAL"} else "OTHER",
       "authority":status.get("authority") if status.get("authority") in {"PLANNING_ONLY","UNKNOWN"} else "OTHER",
       "verificationStatus":status.get("verificationStatus") if status.get("verificationStatus") in {"NOT_VERIFIED","UNKNOWN"} else "OTHER",
       "explicitObjectiveEchoed":explicit,
       "explicitObjectiveRequested":query.get("objective")=="explicit-version-targets",
       "scopeMutable":decision.get("mutablePackages")==["chalk"],
       "majorLineAuthorized":decision.get("approvedVersionLines")=={"chalk":[5]},
       "chalkStatus":safe_status(chalk.get("status")),
       "chalkTarget":safe_version(chalk.get("target")),
       "chalkCurrent":safe_version(chalk.get("current")),
       "corePlanReasonCodes":sorted(safe_plan_reasons),
       "noTargetDiagnosticCode":code,
       "registryStructuralRejections":safe_counts,
       "registryExaminedVersions":(regdiag.get("examinedVersions")
                                    if type(regdiag.get("examinedVersions")) is int else None),
       "registryAcceptedVersions":(regdiag.get("acceptedVersions")
                                    if type(regdiag.get("acceptedVersions")) is int else None),
       "reasonMarkers":reason_codes,
       "requiredPackagesRequested":(response.get("policy") or {}).get("requestedRequiredPackages") or [],
       "companionStatus":safe_status(companion.get("status")),
       "companionTarget":safe_version(companion.get("target")),
       "toolAvailable":True,
     })
     # An exact version can be *inspected* with an existing read-only tool,
     # but this never recomputes solver peers, OSV or runtime compatibility.
     blueprint=(response.get("artifacts") or {}).get("blueprint")
     # Source-backed latest is the actual Core-published reference, not an
     # inferred npm value. Only two public package names leave this probe.
     if isinstance(blueprint,str):
      try:
       bp_path=pathlib.Path(blueprint).resolve()
       if bp_path.is_relative_to(pathlib.Path.cwd().resolve()) and bp_path.is_file():
        bp=json.loads(bp_path.read_text(encoding="utf-8"))
        rows_raw=(bp.get("dependencies") or [])
        responses[-1]["coreReferenceVersions"]={
          r["package"]:r["latest"] for r in rows_raw
          if isinstance(r,dict) and r.get("package") in ("chalk","is-number")
          and isinstance(r.get("latest"),str)
          and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+",r["latest"])}
      except (OSError,TypeError,ValueError):
       responses[-1]["coreReferenceVersions"]={}
     assessment={"status":"NOT_AVAILABLE"}
     if isinstance(blueprint,str) and blueprint and response.get("ok") is True:
      try:
       alternative=decode(await session.call_tool("evaluate_assignment",{
          "blueprint_path":blueprint,
          "assignment":{"chalk":expected_target or "5.3.0"}}))
       whitelist={"REPLAN_REQUIRED","REVIEW_REQUIRED",
          "BLOCKED_BY_KNOWN_CONSTRAINTS","MATCHES_PUBLISHED_PLAN",
          "STALE_BLUEPRINT"}
       vv=alternative.get("verdict")
       safechecks={"PINNED_TARGET_CHANGE_REQUIRES_REPLAN",
          "ALTERNATIVE_PEER_SECURITY_POLICY_NOT_RECHECKED",
          "IMMUTABLE_PACKAGE_CHANGED","EXCLUDED_PACKAGE_CHANGED",
          "MAJOR_LINE_NOT_APPROVED","PUBLISHED_BLOCKERS_REQUIRE_REASSESSMENT",
          "WORKSPACE_RECONCILIATION_REQUIRED","PARTIAL_BLUEPRINT",
          "PUBLISHED_PLAN_BLOCKER"}
       # Sanitized SAME-blueprint blocker provenance from real packaged stdio.
       explained=decode(await session.call_tool("explain_conflict",{
          "blueprint_path":blueprint,"max_items":50}))
       provenance=[]
       for r in (explained.get("reasons") or []):
        if not isinstance(r,dict) or r.get("severity")!="BLOCKER":continue
        code,ref=r.get("code"),r.get("evidenceRef")
        if not isinstance(code,str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,80}",code):continue
        if not isinstance(ref,str) or not re.fullmatch(r"#/(?:dependencies|conflicts|unknowns|unresolved)/[0-9]+(?:/evidence)?|#/targetAssessment(?:/[A-Za-z0-9_/-]+)?",ref):continue
        provenance.append({"code":code,"evidenceRef":ref,
          "lifecycle":r.get("lifecycle") if r.get("lifecycle") in ("ACTIVE_FINAL","CURRENT_ROW","UNRESOLVED_LEGACY") else "OTHER",
          "package":r.get("package") if r.get("package") in ("chalk","is-number") else None,
          "scope":r.get("scope") if r.get("scope") in ("project","aggregate") else None})
       assessment={"status":vv if vv in whitelist else "OTHER",
            "authority":alternative.get("authority"),
            "compatibility":alternative.get("compatibility"),
            "peerConstraints":alternative.get("peerConstraints"),
            "checks":sorted({x.get("code") for x in (alternative.get("checks") or [])
                              if isinstance(x,dict) and x.get("code") in safechecks}),
            "publishedBlockerProvenance":provenance}
      except Exception:
       assessment={"status":"CALL_FAILED"}
     responses[-1]["explicitVersionAssessment"]=assessment
     # Sanitized public frozen fixture health counters; do not confuse
     # exact-solver feasibility with final policy attainment.
     health=(response.get("targetAssessment") or {}).get("aggregate") or {}
     keys=("postPlanGoal","postPlanTargetLevel","postPlanPolicyShortfall",
       "postPlanReserveShortfall","postPlanLagOk","postPlanLagOkPct",
       "postPlanPolicyRequired","postPlanScopeTotal","postPlanSecurityUnknown",
       "postPlanSecurityTotal","postPlanCritical","postPlanHigh",
       "postPlanPolicyMaxCritical","postPlanPolicyMaxHigh")
     safe_health={}
     for key in keys:
      value=health.get(key)
      if key=="postPlanGoal" and value in ("feasible","blocked","unknown"):
       safe_health[key]=value
      elif key=="postPlanTargetLevel" and value in ("yellow","green","mixed"):
       safe_health[key]=value
      elif key not in ("postPlanGoal","postPlanTargetLevel") and type(value) in (int,float) and 0<=value<=1000000:
       safe_health[key]=value
     responses[-1]["finalTargetHealth"]=safe_health
     applied=response.get("policy") or {}
     lag=applied.get("minLagOkPct")
     responses[-1]["appliedHealthPolicy"]={
       "targetLevel":applied.get("targetLevel") if applied.get("targetLevel") in ("yellow","green","mixed") else None,
       "minLagOkPct":lag if type(lag) in (int,float) and 0<=lag<=100 else None}
    except Exception as exc:
     # Never publish the exception text: it can contain paths or URLs.
     kind=type(exc).__name__
     if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,50}",kind):kind="OTHER"
     responses.append({"ok":False,"errorCode":"CALL_FAILED",
                       "scenarioId":scenario_ids[n],
                       "exceptionType":kind,"toolAvailable":True})
   # Independent PUBLIC registry read-only control. It differentiates a
   # planner's indexed packument view from the exact-version endpoint, never
   # supplies candidate evidence to the planner, and is not agent authority.
   probe={"status":"UNKNOWN"}
   try:
    sys.path.insert(0,str(core))
    from dependency_live_roadmap_generator import LiveDataClient
    c=LiveDataClient("https://registry.npmjs.org",timeout=10,batch_size=1,
                     sleep_sec=0,use_system_proxy=True)
    metadata=c.fetch_npm_metadata("chalk")
    has_version=(isinstance(metadata,dict)
       and isinstance((metadata.get("versions") or {}).get("5.3.0"),dict))
    res=c.session.get("https://registry.npmjs.org/chalk/5.3.0",
                      timeout=10,allow_redirects=False)
    try:
     answer=res.json() if res.status_code==200 else {}
     good=(isinstance(answer,dict) and answer.get("name")=="chalk"
           and answer.get("version")=="5.3.0")
     url=((answer.get("dist") or {}).get("tarball") or "") if good else ""
     probe={
      "status":"CONFIRMED" if isinstance(metadata,dict) else "PACKUMENT_UNKNOWN",
      "packumentHasExact":has_version if isinstance(metadata,dict) else None,
      "packumentVersionCount":(len(metadata.get("versions") or {})
                                  if isinstance(metadata,dict) else None),
      "versionEndpointHttpCode":res.status_code if res.status_code in (200,404,410) else None,
      "versionEndpointNameVersionMatch":good,
      "versionEndpointOriginAllowed":c.registry_artifact_url_allowed(url) if good else None,
     }
    finally:res.close()
   except Exception:
    probe={"status":"UNAVAILABLE"}
   # Public npm capture is performed AFTER the packaged MCP calls. Digests
   # identify the observed registry versions/time; drift vs the planner is
   # detected, never waved away as a proven single network snapshot.
   snapshots={}
   try:
    import datetime as dt
    from dateutil.relativedelta import relativedelta
    from dependency_live_roadmap_generator import (
      latest_version,version_date,versions_from_current,min_by_lag,
      current_meets_target)
    client=LiveDataClient("https://registry.npmjs.org",timeout=12,batch_size=1,
                          sleep_sec=0,use_system_proxy=True)
    for package,current in (("chalk","4.1.2"),("is-number","7.0.0")):
     captured_start=dt.datetime.now(dt.timezone.utc).isoformat()
     meta=client.fetch_npm_metadata(package)
     assert isinstance(meta,dict) and isinstance(meta.get("versions"),dict)
     latest=latest_version(meta)
     latest_dt=version_date(meta,latest)
     assert latest_dt is not None
     cutoff=latest_dt-relativedelta(months=12)
     versions,_=versions_from_current(meta,current,False,0)
     minimum=min_by_lag(meta,versions,12,latest_override=latest)
     times=meta.get("time") or {}
     selected=(("4.1.2","5.3.0","5.6.2","6.0.0","6.0.1")
       if package=="chalk" else ("7.0.0",))
     dates={v:times.get(v) for v in selected if isinstance(times.get(v),str)}
     all_times=sorted((v,times.get(v)) for v in meta["versions"])
     snapshot_sha=hashlib.sha256(json.dumps({
       "package":package,"latest":latest,"times":all_times
     },sort_keys=True,separators=(",",":")).encode()).hexdigest()
     eligible_major5=sum(1 for v in versions if v.startswith("5.")
          and (when:=version_date(meta,v)) is not None and when>=cutoff)
     snapshots[package]={
      "status":"CAPTURED","capturedStartedUtc":captured_start,
      "capturedFinishedUtc":dt.datetime.now(dt.timezone.utc).isoformat(),
      "registryAuthority":"https://registry.npmjs.org",
      "metadataTimeDigestSha256":snapshot_sha,
      "metadataVersionCount":len(meta["versions"]),
      "latestDistTag":latest,"latestPublishedUtc":latest_dt.isoformat(),
      "lagMonths":12,"cutoffUtc":cutoff.isoformat(),
      "minWithinLagWindowMetadata":minimum,"selectedPublishedUtc":dates,
      "eligibleMajor5Count":eligible_major5 if package=="chalk" else None}
    for result in responses:
     if not result.get("ok"):continue
     facts=[]
     for package in ("chalk","is-number"):
      ref=snapshots[package]
      if package=="chalk":
       chosen=result.get("chalkTarget") or result.get("chalkCurrent")
      else:
       chosen=result.get("companionTarget") or "7.0.0"
      boundary=ref["minWithinLagWindowMetadata"]
      facts.append({
       "package":package,"finalVersion":chosen,
       "registryLatest":ref["latestDistTag"],"lagMinimum":boundary,
       "lagOk":bool(chosen and current_meets_target(chosen,boundary)),
       "mutable":package=="chalk","healthExcluded":False})
     result["registryLagRows"]=facts
     reference=result.get("coreReferenceVersions") or {}
     result["coreRegistryReferenceMatch"]=all(
       reference.get(package)==snapshots[package]["latestDistTag"]
       for package in ("chalk","is-number"))
   except Exception:
    snapshots={"status":"UNAVAILABLE"}
   # Diagnose ONLY this isolated public fixture, never read the caller's
   # HOME, never export traceback/raw exception message/paths.
   safe_failure={"class":None,"frames":[],"code":None,
                 "isolatedDiagnosticCount":0,"observedCodeTypes":[]}
   try:
    directory=pathlib.Path.home()/".deploom"/"diagnostics"
    public_files=sorted(directory.glob("deploom-failure-*.json"))[-8:]
    safe_failure["isolatedDiagnosticCount"]=len(public_files)
    for path in public_files:
     if not path.is_file() or path.stat().st_size>300000:continue
     obj=json.loads(path.read_text(encoding="utf-8"))
     if obj.get("schemaVersion")!="DEPLOOM_FAILURE_V2":
      continue
     label=obj.get("code")
     if isinstance(label,str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}",label):
      safe_failure["observedCodeTypes"].append(label)
     else:
      safe_failure["observedCodeTypes"].append("OTHER")
     if label == "EXACT_SOLVER_UNSAT_PROVEN":
      continue
     raw=str(obj.get("traceback") or "")
     cls=re.findall(r"(?m)^([A-Za-z][A-Za-z0-9]{0,40}(?:Error|Exception)):",raw)
     frames=[]
     for fname,lineno,fun in re.findall(
       r'File "[^"\n]*?([A-Za-z_][A-Za-z0-9_]*\.py)", line ([0-9]{1,6}), in ([A-Za-z_][A-Za-z0-9_]{0,75})',
       raw):
      if fname in ("dependency_live_roadmap_generator.py","draft_decision_contract.py",
                   "peer_solver_z3.py","peer_solver_model.py","verification_proof.py"):
       frames.append({"file":fname,"line":int(lineno),"function":fun})
     safe_failure.update({"class":cls[-1] if cls else "UNKNOWN",
                   "frames":frames[-7:],"code":(
                       label if isinstance(label,str)
                       and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}",label)
                       else "OTHER")})
   except (OSError,ValueError,TypeError):
    pass
   return {"responses":responses,"toolAvailable":True,
           "registrySnapshots":snapshots,
           "publicRegistryControl":probe,
           "isolatedCoreFailure":safe_failure,
           "requiredFieldInMcpToolSchema":supports_required,
           "knownFieldsAdvertised":observed_fields,
           "inputSchemaType":(type(tool_schema).__name__
                if type(tool_schema).__name__ in ("dict","JsonSchemaDict","Schema") else "OTHER"),
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
        and p.get("explicitObjectiveRequested") is True
        and p.get("explicitObjectiveEchoed") is True
        and p.get("scopeMutable") is True
        and p.get("majorLineAuthorized") is True
        and p.get("chalkStatus") == "PROPOSED"
        and p.get("chalkTarget") == "5.3.0"
        and p.get("companionStatus") != "PROPOSED"
        and p.get("companionTarget") in (None, "7.0.0")
    ]
    if exact:
        return {"gate":"GO", "productGap":None,
                "exactVersionObjectiveSupported":True}
    # No V1/latest incidental match can ever pass the exact user objective.
    if any(p.get("chalkTarget")=="5.3.0" and not p.get("explicitObjectiveEchoed")
           for p in rows):
        return {"gate":"NO_GO",
                "productGap":"EXACT_VERSION_OBJECTIVE_NOT_ECHOED",
                "incidentalTargetMatch":True}
    if any(p.get("explicitObjectiveRequested") and p.get("errorCode")=="generator-failed"
           for p in rows):
        return {"gate":"NO_GO","productGap":"EXPLICIT_CORE_GENERATOR_FAILED"}
    if any(p.get("explicitObjectiveRequested") and p.get("errorCode") not in
           (None,"OTHER_OR_NONE") for p in rows):
        return {"gate":"NO_GO","productGap":"EXPLICIT_REQUEST_REJECTED"}
    for p in rows:
        if p.get("explicitObjectiveRequested") and p.get("chalkStatus")=="BLOCKED":
            reasons=p.get("corePlanReasonCodes") or []
            if "EXACT_TARGET_UNAVAILABLE" in reasons:
                return {"gate":"NO_GO","productGap":"EXPLICIT_REGISTRY_TARGET_UNAVAILABLE",
                        "reasonCodes":reasons}
            return {"gate":"NO_GO","productGap":"EXPLICIT_TARGET_BLOCKED",
                    "reasonCodes":reasons}
    if any(p.get("errorCode")=="generator-failed" and p.get("coreExitCode")==3
           and "unsat" in (p.get("coreFailureMarkers") or []) for p in rows):
        return {"gate":"NO_GO",
                "productGap":"EXACT_VERSION_OBJECTIVE_UNSUPPORTED_REQUIRED_UNSAT",
                "exactVersionObjectiveSupported":False}
    if any(p.get("chalkTarget") not in (None, "5.3.0") for p in rows):
        return {"gate":"NO_GO","productGap":"NO_EXACT_CHALK_5_3_0_TARGET"}
    if any(p.get("chalkStatus") in {"NO_CHANGE","POLICY_SATISFIED"} for p in rows):
        return {"gate":"NO_GO","productGap":"POLICY_ALREADY_SATISFIED_NO_MIGRATION_ACTION"}
    if any(p.get("errorCode")=="decision-contract-unsupported" for p in rows):
        return {"gate":"NO_GO","productGap":"UNSUPPORTED_EXACT_MIGRATION_DECISION_CONTRACT"}
    if any(p.get("chalkStatus")=="CANDIDATE_SEARCH_TRUNCATED" for p in rows):
        return {"gate":"NO_GO","productGap":"CHALK_CANDIDATE_SEARCH_TRUNCATED_AT_CAP_128"}
    if any(p.get("chalkStatus") in {"UNKNOWN_SECURITY","UNKNOWN_METADATA","BLOCKED"} for p in rows):
        return {"gate":"NO_GO","productGap":"UNRESOLVED_PLANNING_OR_SECURITY"}
    return {"gate":"NO_GO","productGap":"NO_ACTIONABLE_CHALK_TARGET",
            "exactVersionObjectiveSupported":False}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen",type=Path,required=True)
    parser.add_argument("--wheel",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--require-go",action="store_true")
    args=parser.parse_args()
    report={"gate":"NO_GO","productGap":"INFRASTRUCTURE_PRECHECK_FAILED",
            "gateScope":"EXACT_VERSION_PLANNING_FEASIBILITY_ONLY",
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
                    # Fresh isolated HOME: only generated public Chalk diagnostics.
                    env["HOME"]=str(work)
                    if os.name=="nt":env["USERPROFILE"]=str(work)
                    t=time.monotonic()
                    process=subprocess.run([str(python),"-I","-c",RUNTIME,str(project),EXPECTED_CORE_SHA],
                                           cwd=work,env=env,capture_output=True,timeout=240)
                    report["mcpPlanWallSeconds"]=round(time.monotonic()-t,3)
                    if process.returncode:
                        report["productGap"]="PACKAGED_MCP_CALL_FAILED"
                        # Never emit subprocess stderr, which may expose paths
                        # or private registry/auth diagnostics. Record only a
                        # fixed-class failure marker.
                        stderr=process.stderr.decode("utf-8",errors="replace")
                        names=("SyntaxError","NameError","AttributeError",
                               "UnboundLocalError","TypeError","KeyError",
                               "AssertionError","ImportError","ModuleNotFoundError",
                               "RuntimeError","McpError","ExceptionGroup")
                        report["failureClass"]=next(
                            (n for n in names if n in stderr),"OTHER")
                    else:
                        payload=json.loads(process.stdout.decode("utf-8").splitlines()[-1])
                        report.update(evaluate(payload))
                        report["requests"]=payload.get("responses",[])
                        report["publicRegistryControl"]=payload.get("publicRegistryControl")
                        report["registrySnapshots"]=payload.get("registrySnapshots")
                        report["isolatedCoreFailure"]=payload.get("isolatedCoreFailure")
                        report["requiredFieldInMcpToolSchema"]=payload.get("requiredFieldInMcpToolSchema")
                        report["knownFieldsAdvertised"]=payload.get("knownFieldsAdvertised",[])
                        report["inputSchemaType"]=payload.get("inputSchemaType")
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
