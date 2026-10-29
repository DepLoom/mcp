#!/usr/bin/env python3
"""DepLoom D1-A/B: one Codex pair, fail-closed, NO automatic paid execution.

Commands:
  doctor --codex codex             # no model requests; local help/catalog
  dry-run --out DIR                # clearly labelled synthetic JSONL only
  run --approve-one-paid-pair ...  # requires confirmed MCP Chalk gate and model
  score --baseline DIR --candidate DIR --baseline-sha HASH --out FILE

No raw transcripts or credentials are stored. Only official Codex --json
turn.completed usage is counted; missing counters remain null. Token/cost
ceilings are SOFT (a completed turn can overshoot); wall/tool watchdogs cancel
only the spawned isolated process group, never foreign processes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from correctness_oracle import source_files
from correctness_preflight import verify_chalk_frozen
from d1_mcp_value_preflight import EXPECTED_SOURCE_SHA

DEFAULT_PROMPT = """Upgrade this PUBLIC synthetic application from chalk 4.1.2 to EXACTLY
chalk 5.3.0 while retaining is-number 7.0.0 unchanged. Adapt the source for
the documented Chalk v5 breaking API and preserve runtime behavior for all
input strings, not just the example. You may modify ONLY package.json,
package-lock.json and src/format.cjs. Do not change build.cjs, test.cjs,
src/index.cjs, README.md or .npmrc; do not create extra files or disable
checks. Use public migration docs at
https://github.com/chalk/chalk/releases/tag/v5.0.0 and public npm data.
Run the project's build and test if possible. DepLoom, when available, is
planning-only: do NOT interpret a proposal as an applied or verified migration.
Report real checks, not claimed success. Do not request further scope.
"""
TOOL_ITEMS={"command_execution","mcp_tool_call","web_search","file_change"}
FIELDS=("input_tokens","cached_input_tokens","output_tokens","reasoning_output_tokens")


def source_sha(root: Path) -> str:
    files=source_files(root)
    return hashlib.sha256(json.dumps(files,sort_keys=True,
                  separators=(",",":")).encode()).hexdigest()


class UsageCollector:
    def __init__(self, max_tools: int=40):
        self.tool_ids=set()
        self.tools=Counter()
        self.turns=0
        self.values={key:[] for key in FIELDS}
        self.failed=False
        self.max_tools=max_tools
        self.thread_ids=set()

    def ingest(self, event: dict) -> bool:
        kind=event.get("type")
        if kind=="thread.started":
            tid=event.get("thread_id")
            if isinstance(tid,str):self.thread_ids.add(tid)
        if kind in {"turn.failed","error"}:
            self.failed=True
        if kind=="item.started":
            item=event.get("item") or {}
            ident=item.get("id")
            typ=item.get("type")
            if typ in TOOL_ITEMS and isinstance(ident,str) and ident not in self.tool_ids:
                self.tool_ids.add(ident)
                self.tools[typ]+=1
        if kind=="turn.completed":
            self.turns+=1
            usage=event.get("usage") if isinstance(event.get("usage"),dict) else {}
            for key in FIELDS:
                value=usage.get(key)
                self.values[key].append(value if type(value) is int and value>=0 else None)
        return len(self.tool_ids)<=self.max_tools

    def result(self) -> dict:
        tokens={}
        for key,values in self.values.items():
            tokens[key]=(sum(values) if self.turns and len(values)==self.turns
                         and all(v is not None for v in values) else None)
        return {
            "nativeUsage":tokens,
            "turnCompletedEvents":self.turns,
            "usageComplete":all(tokens[k] is not None for k in ("input_tokens","output_tokens")),
            "toolCalls":dict(self.tools),
            "toolCallTotal":len(self.tool_ids),
            "threadCount":len(self.thread_ids),
            "eventReportedFailure":self.failed,
        }


def price_usd(tokens: dict, rate: tuple[float,float,float] | None) -> float | None:
    if rate is None or any(tokens.get(k) is None for k in
         ("input_tokens","cached_input_tokens","output_tokens")):
        return None
    uncached=tokens["input_tokens"]-tokens["cached_input_tokens"]
    if uncached<0:return None
    return round((uncached*rate[0]+tokens["cached_input_tokens"]*rate[1]
                  +tokens["output_tokens"]*rate[2])/1_000_000,6)


def stop_owned(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:return
    try:
        if os.name=="nt":
            # Signal only the explicitly created process group. Never taskkill
            # /F /T, pkill, or global process-name matches.
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(proc.pid,signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name=="nt":proc.kill()
            else:os.killpg(proc.pid,signal.SIGKILL)
    except (OSError,ProcessLookupError):
        pass


def execute(argv: list[str],cwd:Path,env:dict,wall_limit:int,tools_limit:int,
            max_input:int,max_output:int,rate:tuple|None,
            max_api_estimate:float|None=None) -> dict:
    start=time.monotonic()
    collector=UsageCollector(tools_limit)
    reason={"why":None}
    args={"cwd":str(cwd),"env":env,"stdout":subprocess.PIPE,
          "stderr":subprocess.DEVNULL,"text":True,"errors":"replace"}
    if os.name=="nt":
        args["creationflags"]=subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        args["start_new_session"]=True
    proc=subprocess.Popen(argv,**args)
    exit_signal=threading.Event()

    def watchdog():
        while not exit_signal.wait(0.15):
            if time.monotonic()-start>wall_limit:
                reason["why"]="WALL_WATCHDOG"
                stop_owned(proc)
                return
    watcher=threading.Thread(target=watchdog,daemon=True)
    watcher.start()
    try:
        for line in proc.stdout:
            if len(line)>2_000_000:
                reason["why"]="OVERSIZED_EVENT"
                stop_owned(proc)
                break
            try:event=json.loads(line)
            except ValueError:continue
            if not isinstance(event,dict):continue
            within_budget=collector.ingest(event)
            if not within_budget:
                reason["why"]="TOOL_CALL_WATCHDOG"
                stop_owned(proc)
                break
            result=collector.result()
            ti=result["nativeUsage"]
            # Codex often exposes usage only at turn completion. These stops
            # can occur AFTER paid tokens were spent; record overshoot.
            estimate=price_usd(ti,rate)
            if ((ti["input_tokens"] is not None and ti["input_tokens"]>max_input)
                or (ti["output_tokens"] is not None and ti["output_tokens"]>max_output)):
                reason["why"]="TOKEN_OVERSHOOT_REPORTED"
                stop_owned(proc)
                break
            if (estimate is not None and max_api_estimate is not None
                    and estimate>max_api_estimate):
                reason["why"]="API_COST_SOFT_OVERSHOOT_REPORTED"
                stop_owned(proc)
                break
    finally:
        exit_signal.set()
        watcher.join(timeout=1)
        try: code=proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            stop_owned(proc)
            code=proc.poll()
        if proc.stdout:proc.stdout.close()
    summary=collector.result()
    summary.update({
        "wallSeconds":round(time.monotonic()-start,3),
        "cliExitCode":code,
        "stopReason":reason["why"],
        "exitSuccess":code==0 and not collector.failed and reason["why"] is None,
        "costUsdApiRateEstimateOnly":price_usd(summary["nativeUsage"],rate),
        "costStatus":"UNAVAILABLE" if price_usd(summary["nativeUsage"],rate) is None
                     else "API_RATE_ESTIMATE_NOT_SUBSCRIPTION_BILLING",
        "limits":{
            "wallSecondsHardWatchdog":wall_limit,
            "toolCallsStopAfterStart":tools_limit,
            "inputTokensSoft":max_input,
            "outputTokensSoft":max_output,
            "tokenHardCapSupported":False,
            "apiCostSoftCeilingUsd":max_api_estimate,
            "apiCostHardCapSupported":False,
        },
        "tokenOvershoot":any(
            summary["nativeUsage"][k] is not None and summary["nativeUsage"][k]>cap
            for k,cap in (("input_tokens",max_input),("output_tokens",max_output))),
    })
    return summary


def quote_toml(value: str) -> str:
    return json.dumps(value,ensure_ascii=True)


def codex_command(codex: str,workspace:Path,model:str,effort:str,
                  mcp_python:Path|None,mcp_state:Path|None) -> list[str]:
    cmd=[codex,"exec","--ignore-user-config","--ignore-rules",
         "--ephemeral","--json","--sandbox","workspace-write",
         "--skip-git-repo-check","--model",model,
         "-c",f"model_reasoning_effort={quote_toml(effort)}",
         "-c","approval_policy=\"never\"",
         "-c","sandbox_workspace_write.network_access=true",
         "-C",str(workspace)]
    if mcp_python is not None:
        # Exact bundled wheel. Config override only in B; no Skill installed.
        command=quote_toml(str(mcp_python))
        state=quote_toml(str(mcp_state))
        mcp=f'{{command={command},args=["-I","-m","deploom_mcp"],env={{DEPLOOM_MCP_STATE_DIR={state}}},enabled=true,required=true,startup_timeout_sec=20,tool_timeout_sec=65}}'
        cmd+=["-c","mcp_servers.deploom="+mcp]
    cmd.append(DEFAULT_PROMPT)
    return cmd


def check_cli(codex:str) -> dict:
    result={"present":False,"cliVersion":None,"jsonExecDocumented":False,
            "modelAccountAccess":"UNVERIFIED","modelLocked":None}
    executable=shutil.which(codex)
    if executable is None:return result
    try:
        version=subprocess.run([executable,"--version"],capture_output=True,text=True,timeout=8)
        help_=subprocess.run([executable,"exec","--help"],capture_output=True,text=True,timeout=8)
        result.update(present=version.returncode==0,
                      cliVersion=(version.stdout.strip().splitlines()[0][:120]
                                  if version.returncode==0 else None),
                      jsonExecDocumented=help_.returncode==0 and
                          all(x in help_.stdout for x in ("--json","--model","--sandbox")))
    except (OSError,subprocess.TimeoutExpired):pass
    return result


def clone_seed(seed:Path,dst:Path,expected:str):
    if source_sha(seed)!=expected:raise RuntimeError("FROZEN_SOURCE_HASH_MISMATCH")
    shutil.copytree(seed,dst,ignore=shutil.ignore_patterns(
        ".git","node_modules","dist","__pycache__"))
    if source_sha(dst)!=expected:raise RuntimeError("CLONE_SOURCE_HASH_MISMATCH")


def secure_home(run:Path,login_source:Path|None) -> Path:
    home=run/"codex-home"
    home.mkdir(mode=0o700)
    if login_source:
        src=login_source/"auth.json"
        if not src.is_file():raise RuntimeError("PRIVATE_CODEX_AUTH_UNAVAILABLE")
        # Nothing from the user's config/skills is copied; never print auth.
        shutil.copyfile(src,home/"auth.json")
        (home/"auth.json").chmod(0o600)
    return home


def score_candidate(baseline:Path,workspace:Path,sha:str) -> dict:
    return verify_chalk_frozen(baseline,workspace,sha)


def dry_run(out:Path) -> dict:
    if out.exists():raise RuntimeError("OUTPUT_EXISTS")
    out.mkdir(parents=True)
    fake_a=[
      {"type":"thread.started","thread_id":"FAKE-thread-A"},
      {"type":"item.started","item":{"id":"fake-command","type":"command_execution"}},
      {"type":"turn.completed","usage":{"input_tokens":1100,"cached_input_tokens":300,
                                         "output_tokens":400,"reasoning_output_tokens":100}},
    ]
    fake_b=[
      {"type":"thread.started","thread_id":"FAKE-thread-B"},
      {"type":"item.started","item":{"id":"fake-mcp","type":"mcp_tool_call"}},
      {"type":"item.started","item":{"id":"fake-cmd","type":"command_execution"}},
      {"type":"turn.completed","usage":{"input_tokens":1400,"cached_input_tokens":400,
                                         "output_tokens":430,"reasoning_output_tokens":120}},
    ]
    def collect(events):
        c=UsageCollector()
        for e in events:c.ingest(e)
        return c.result()
    receipt={"mode":"FAKE_DRY_RUN_NO_MODEL_CALLS","truth":"SYNTHETIC_NOT_EVIDENCE",
             "cliInvoked":False,"model":"FAKE_MODEL_NOT_AVAILABLE",
             "A":collect(fake_a),"B":collect(fake_b),
             "correctnessA":None,"correctnessB":None,
             "costUsd":None,"manualHelpEvents":None,
             "comparison":"NOT_MEASURED"}
    (out/"d1-fake.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n")
    return receipt


def launch(args):
    if not args.approve_one_paid_pair:
        raise RuntimeError("REQUIRES_EXPLICIT_ONE_PAIR_AUTHORIZATION")
    if not args.model or not args.effort:
        raise RuntimeError("EXACT_MODEL_AND_REASONING_REQUIRED")
    if args.manual_a is None or args.manual_b is None:
        raise RuntimeError("RECORD_MANUAL_ASSISTANCE_EXPLICITLY")
    if args.out.exists():
        raise RuntimeError("OUTPUT_EXISTS")
    if args.seed.resolve() in args.out.resolve().parents or args.out.resolve() == args.seed.resolve():
        raise RuntimeError("OUTPUT_OVERLAPS_FROZEN_SEED")
    if any(x is not None and (x < 0 or not float(x) < float("inf"))
           for x in (args.input_rate,args.cached_rate,args.output_rate,args.max_api_estimate)):
        raise RuntimeError("INVALID_COST_LIMIT_OR_RATE")
    if args.manual_a < 0 or args.manual_b < 0:
        raise RuntimeError("INVALID_MANUAL_HELP_COUNT")
    gate=json.loads(args.mcp_preflight.read_text())
    if gate.get("gate")!="GO":
        raise RuntimeError("MCP_CHALK_PREFLIGHT_NO_GO")
    if gate.get("frozenObservedSha256")!=EXPECTED_SOURCE_SHA:
        raise RuntimeError("GATE_BASELINE_NOT_FROZEN")
    if args.wheel is None or not args.wheel.is_file() or (
        hashlib.sha256(args.wheel.read_bytes()).hexdigest()!=gate.get("wheelSha256")
    ):
        raise RuntimeError("MCP_WHEEL_PROVENANCE_MISMATCH")
    cli=check_cli(args.codex)
    if not cli["present"] or not cli["jsonExecDocumented"]:
        raise RuntimeError("CODEX_CLI_SCHEMA_NOT_AVAILABLE")
    if not args.confirm_model_access:
        raise RuntimeError("MODEL_ACCESS_NOT_CONFIRMED_WITH_ACCOUNT")
    rates=(args.input_rate,args.cached_rate,args.output_rate)
    if any(x is not None for x in rates) and not all(x is not None for x in rates):
        raise RuntimeError("INCOMPLETE_API_PRICING")
    if args.max_api_estimate is not None and not all(x is not None for x in rates):
        raise RuntimeError("API_COST_STOP_REQUIRES_ALL_RATES")
    rate=tuple(rates) if all(x is not None for x in rates) else None
    # Prepare everything BEFORE the timed task. A and B start from the same
    # exact frozen bytes; MCP wheel is setup outside the agent workspace.
    args.out.mkdir(parents=True)
    logins=args.codex_auth_home
    results={}
    for arm in ("A","B"):
        root=args.out/arm
        root.mkdir()
        workspace=root/"workspace"
        clone_seed(args.seed,workspace,EXPECTED_SOURCE_SHA)
        home=secure_home(root,logins)
        # Do not give arbitrary user credentials or inherited private npm
        # configuration to either agent. Auth stays in the private CODEX_HOME
        # copied above, outside the editable seed and never in a public report.
        secret_keys={"OPENAI_API_KEY","CODEX_API_KEY","GITHUB_TOKEN","GH_TOKEN",
                     "NPM_TOKEN","NODE_AUTH_TOKEN","AWS_SECRET_ACCESS_KEY",
                     "AZURE_OPENAI_API_KEY"}
        env={k:v for k,v in os.environ.items()
             if k not in secret_keys and k!="PYTHONPATH"
             and not k.startswith("DEPLOOM_")
             and not k.lower().startswith("npm_config_")}
        empty_home=root/"home"
        empty_home.mkdir(mode=0o700)
        env["HOME"]=str(empty_home)
        if os.name=="nt":env["USERPROFILE"]=str(empty_home)
        env["CODEX_HOME"]=str(home)
        env["CI"]="true"
        env["NO_COLOR"]="1"
        # A and B use distinct, initially empty caches and equal live network
        # permission. Without a pinned registry replay, this is BEST-EFFORT,
        # not a causal control for external npm/OSV traffic.
        env["npm_config_cache"]=str(root/"npm-cache")
        env["npm_config_audit"]="false"
        env["npm_config_fund"]="false"
        mcp_python=None
        t0=time.monotonic()
        if arm=="B":
            vpath=root/"mcpvenv"
            subprocess.run([sys.executable,"-m","venv",str(vpath)],check=True,
                           capture_output=True,timeout=40)
            mcp_python=vpath/("Scripts/python.exe" if os.name=="nt" else "bin/python")
            installed=subprocess.run([str(mcp_python),"-m","pip","install",
                                       "--no-input","--disable-pip-version-check",
                                       str(args.wheel)],capture_output=True,timeout=160)
            if installed.returncode:raise RuntimeError("B_MCP_INSTALL_FAILED")
        mcp_setup_seconds=round(time.monotonic()-t0,3) if arm=="B" else 0.0
        cmd=codex_command(shutil.which(args.codex),workspace,args.model,args.effort,
                          mcp_python,root/"mcp-state")
        # Time begins just before codex exec. MCP startup, tool calls, npm
        # commands, permissions and retries are included in each arm's wall.
        measurement=execute(cmd,workspace,env,args.wall,args.tools,args.input_cap,
                            args.output_cap,rate,args.max_api_estimate)
        measurement["mcpWheelSetupWallSeconds"]=mcp_setup_seconds
        measurement["wallIncludingMcpSetupSeconds"]=round(
            measurement["wallSeconds"]+mcp_setup_seconds,3)
        independent=score_candidate(args.seed,workspace,EXPECTED_SOURCE_SHA)
        results[arm]={"measurement":measurement,"correctness":independent,
                      "manualHelpEvents":args.manual_a if arm=="A" else args.manual_b,
                      "finalSourceSha256":source_sha(workspace)}
        (root/"receipt.json").write_text(json.dumps(results[arm],sort_keys=True,indent=2)+"\n")
    aa,bb=results["A"],results["B"]
    ca=aa["correctness"]["status"]=="PASS"
    cb=bb["correctness"]["status"]=="PASS"
    b_mcp_used=bb["measurement"]["toolCalls"].get("mcp_tool_call",0)>0
    if not b_mcp_used:
        verdict="B_DID_NOT_USE_MCP_NO_MCP_EFFECT_CLAIM"
    elif cb and not ca:verdict="B_CORRECT_A_INCORRECT"
    elif ca and not cb:verdict="B_INCORRECT_A_CORRECT"
    elif ca and cb:
        # Compare total task wall including B's MCP preparation overhead;
        # retain CLI-only timing separately for causal interpretation.
        am=aa["measurement"]["wallIncludingMcpSetupSeconds"]
        bm=bb["measurement"]["wallIncludingMcpSetupSeconds"]
        verdict="B_FASTER_PILOT_ONLY" if bm<am else ("B_SLOWER_PILOT_ONLY" if bm>am else "TIE_PILOT_ONLY")
    else:verdict="BOTH_INCORRECT"
    full={"status":"OBSERVED_PILOT_NOT_STATISTICAL","experimentalArms":["A","B"],
          "model":args.model,"reasoning":args.effort,"codex":cli["cliVersion"],
          "frozenSha256":EXPECTED_SOURCE_SHA,
          "wheelSha256":gate.get("wheelSha256"),
          "cachedNetworkComparability":"BEST_EFFORT_COLD_SEPARATE_CACHE_LIVE_NETWORK",
          "agentSessions":2,"result":verdict,
          "bActuallyCalledMcp":b_mcp_used,"arms":results}
    (args.out/"pair-summary.json").write_text(json.dumps(full,sort_keys=True,indent=2)+"\n")
    return full


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="mode",required=True)
    d=sub.add_parser("doctor")
    d.add_argument("--codex",default="codex")
    dr=sub.add_parser("dry-run")
    dr.add_argument("--out",type=Path,required=True)
    sc=sub.add_parser("score")
    sc.add_argument("--baseline",type=Path,required=True)
    sc.add_argument("--candidate",type=Path,required=True)
    sc.add_argument("--baseline-sha",required=True)
    sc.add_argument("--out",type=Path,required=True)
    run=sub.add_parser("run")
    run.add_argument("--approve-one-paid-pair",action="store_true")
    run.add_argument("--confirm-model-access",action="store_true")
    run.add_argument("--seed",type=Path,required=True)
    run.add_argument("--wheel",type=Path,required=True)
    run.add_argument("--mcp-preflight",type=Path,required=True)
    run.add_argument("--out",type=Path,required=True)
    run.add_argument("--codex",default="codex")
    run.add_argument("--codex-auth-home",type=Path,help="private Codex auth.json source, never exported")
    run.add_argument("--model",help="exact model; access must be confirmed out of band")
    run.add_argument("--effort",choices=["low","medium","high","xhigh"])
    run.add_argument("--wall",type=int,default=600)
    run.add_argument("--tools",type=int,default=40)
    run.add_argument("--input-cap",type=int,default=20000)
    run.add_argument("--output-cap",type=int,default=4000)
    run.add_argument("--manual-a",type=int)
    run.add_argument("--manual-b",type=int)
    run.add_argument("--input-rate",type=float)
    run.add_argument("--cached-rate",type=float)
    run.add_argument("--output-rate",type=float)
    run.add_argument("--max-api-estimate",type=float,help="SOFT! observed turn-only API estimate, no hard spending cap")
    args=p.parse_args()
    try:
        if args.mode=="doctor":
            output={"mode":"NO_MODEL_DOCTOR",**check_cli(args.codex)}
        elif args.mode=="dry-run":
            output=dry_run(args.out)
        elif args.mode=="score":
            output=score_candidate(args.baseline,args.candidate,args.baseline_sha)
            args.out.parent.mkdir(parents=True,exist_ok=True)
            args.out.write_text(json.dumps(output,indent=2,sort_keys=True)+"\n")
        else:
            output=launch(args)
        print(json.dumps(output if args.mode in ("doctor","dry-run","score") else
                         {"status":output["status"],"result":output["result"],"agentSessions":2},
                         sort_keys=True))
        return 0 if args.mode!="score" or output.get("status")=="PASS" else 2
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:
        # Never include stdout, raw session text, private paths or credentials.
        print(json.dumps({"status":"NO_GO","reason":type(e).__name__+":"+
                         (str(e) if isinstance(e,RuntimeError) and
                          re.fullmatch(r"[A-Z0-9_]+",str(e)) else "UNVERIFIED")}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
