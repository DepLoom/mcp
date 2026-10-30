#!/usr/bin/env python3
"""D1 one LOCAL, sequential, intentionally non-restartable Chalk6 A/B pair.

NO model calls from doctor, preflight, dry-run, score or CI. Only the explicit
local "run --approve-one-paid-pair" subcommand may invoke Codex.
Auth exists ONLY in a temporary private CODEX_HOME outside the benchmark
artifacts, never inside A/B workspaces, logs, receipts or uploaded evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any

from correctness_oracle import source_files
from d1_chalk6_scorer import (
    EXPECTED_SEED_SHA256, FROZEN_FILENAMES, D1_AB_CHALK6_TARGET,
    source_sha, verify_chalk6_frozen,
)
from d1_owned_process import OwnedProcess
from d1_yellow_policy_demo import check_policy_receipt, DemoFailure

CLI_VERSION = "0.165.0"
MODEL = "gpt-6.1-terra"
EFFORT = "medium"
MAX_INPUT, MAX_OUTPUT, MAX_TOOLS, MAX_WALL = 20000, 4000, 40, 600
NATIVE_USAGE = ("input_tokens","cached_input_tokens","output_tokens",
                "reasoning_output_tokens")
TOOL_ITEMS = {"command_execution","mcp_tool_call","web_search","file_change"}
NON_TOOL_ITEMS = {"agent_message","reasoning","plan","todo_list","error"}
NEEDED_FEATURES = ("apps", "plugins", "multi_agent")
DEFAULT_PROMPT = """Upgrade this PUBLIC synthetic Chalk4.1.2 application to EXACTLY
chalk 6.0.0. This is the separate explicitly specified Chalk6 A/B benchmark,
not the older Chalk5.3.0 exercise described in the frozen README.md.
The immutable dependency is is-number@7.0.0. Target policy is Yellow >=80%
over BOTH dependencies, with no exclusions; the only mutable package is chalk
and its approved target major is 6. Keep both package versions exact.
Change ONLY package.json, package-lock.json and src/format.cjs; all other
source, README, scripts, lock provenance, test behavior and .npmrc are frozen.
Do not create extra files, delete tests, bypass checks, change scope or
read another task's outputs. Preserve READY <name> for arbitrary input names.
Source migration docs: https://github.com/chalk/chalk/releases/tag/v6.0.0
Use public npm package metadata. Run build/test and real behavior checks.
DepLoom MCP, if available, is read-only planning. A PROPOSED target and
feasible health policy are NOT runtime verification; you must apply and test.
Do not access secrets/private repositories or call external agents. Report
actual checks only. No user assistance or scope expansion is authorized.
"""


def quote_toml(v: str) -> str:
    return json.dumps(v, ensure_ascii=True)


def source_is_safe(seed: Path) -> bool:
    return set(source_files(seed)) == FROZEN_FILENAMES and source_sha(seed) == EXPECTED_SEED_SHA256


class UsageCollector:
    """Codex exec --json only. Deduplicate items by ID across event phases.

    An item.started is a call even without item.completed; item.completed with
    missing start is counted, never used to conceal a call. Unknown item types
    are conservatively counted as tools unless known presentational types.
    Turn usage is emitted at turn.completed and is ONLY a soft token stop.
    """
    def __init__(self, max_tools: int = MAX_TOOLS):
        self.max_tools = max_tools
        self.items: set[str] = set()
        self.tools = Counter()
        self.unknown_items = set()
        self.turns = 0
        self.values = {k: [] for k in NATIVE_USAGE}
        self.failed = False
        self.thread_ids: set[str] = set()
        self.malformed = 0
        self.turn_started = 0

    def ingest(self, event: dict) -> bool:
        kind = event.get("type")
        if kind == "thread.started":
            tid = event.get("thread_id")
            if isinstance(tid, str) and re.fullmatch(r"[A-Za-z0-9-]{10,100}", tid):
                self.thread_ids.add(tid)
            else:
                self.malformed += 1
        elif kind == "turn.started":
            self.turn_started += 1
        elif kind in ("turn.failed","error"):
            self.failed = True
        elif kind in ("item.started","item.completed"):
            item = event.get("item")
            if not isinstance(item, dict):
                self.malformed += 1
            else:
                typ = item.get("type")
                ident = item.get("id")
                if not isinstance(typ, str) or not isinstance(ident, str) or not ident:
                    self.malformed += 1
                elif typ not in NON_TOOL_ITEMS and ident not in self.items:
                    self.items.add(ident)
                    name = typ if typ in TOOL_ITEMS else "unknown_tool_type"
                    self.tools[name] += 1
                    if name == "unknown_tool_type":
                        self.unknown_items.add(typ)
        elif kind == "turn.completed":
            self.turns += 1
            usage = event.get("usage")
            if not isinstance(usage, dict):
                usage = {}
            for k in NATIVE_USAGE:
                v = usage.get(k)
                self.values[k].append(v if type(v) is int and v >= 0 else None)
        elif kind not in ("item.updated",):
            self.malformed += 1
        return (len(self.items) <= self.max_tools and self.malformed == 0
                and len(self.thread_ids) <= 1 and self.turns <= 1
                and self.turn_started <= 1)

    def result(self) -> dict:
        values = {
            k: (sum(v) if self.turns == 1 and len(v) == 1 and
                all(x is not None for x in v) else None)
            for k, v in self.values.items()
        }
        require_usage = all(
            type(values[k]) is int and values[k] > 0
            for k in ("input_tokens","output_tokens"))
        valid = (not self.failed and self.malformed == 0 and self.turns == 1
                 and len(self.thread_ids) == 1 and len(self.items) <= self.max_tools
                 and require_usage
                 and (values["cached_input_tokens"] is None or
                      values["cached_input_tokens"] <= values["input_tokens"])
                 and (values["reasoning_output_tokens"] is None or
                      values["reasoning_output_tokens"] <= values["output_tokens"]))
        return {
            "nativeThreadId": next(iter(self.thread_ids)) if len(self.thread_ids) == 1 else None,
            "nativeUsage": values, "usageComplete": require_usage,
            "usageBreakdownComplete": all(v is not None for v in values.values()),
            "turnCompletedEvents": self.turns, "toolCalls": dict(sorted(self.tools.items())),
            "toolCallTotal": len(self.items),
            "toolEventsDefinition": "DISTINCT_TOOL_ITEM_ID_ON_STARTED_OR_COMPLETED",
            "eventReportedFailure": self.failed, "eventSchemaSafe": valid,
            "malformedEvents": self.malformed,
            "unknownItemTypes": sorted(self.unknown_items),
        }


def price_usd(tokens: dict, rate: tuple[float, float, float] | None) -> float | None:
    """Historic test helper only; never infer the user's native billing."""
    if rate is None or any(tokens.get(k) is None
                           for k in ("input_tokens","cached_input_tokens","output_tokens")):
        return None
    uncached = tokens["input_tokens"] - tokens["cached_input_tokens"]
    if uncached < 0:
        return None
    return round((uncached * rate[0] + tokens["cached_input_tokens"] * rate[1]
                  + tokens["output_tokens"] * rate[2]) / 1_000_000, 6)


def codex_command(codex: str, workspace: Path, model: str, effort: str,
                  mcp_python: Path | None, mcp_state: Path | None) -> list[str]:
    if model != MODEL or effort != EFFORT:
        raise RuntimeError("EXACT_MODEL_EFFORT_MISMATCH")
    cmd = [codex, "exec", "--ignore-user-config", "--strict-config",
           "--ephemeral", "--json", "--sandbox", "workspace-write",
           "--skip-git-repo-check", "--model", model,
           "-c", f"model_reasoning_effort={quote_toml(effort)}",
           "-c", 'approval_policy="never"',
           "-c", "sandbox_workspace_write.network_access=true"]
    # Explicit feature disables are applied to both arms. If 0.165.0 does not
    # document these, native CLI doctor returns NO_GO before ANY model call.
    for feat in NEEDED_FEATURES:
        cmd.extend(["--disable", feat])
    cmd.extend(["-C", str(workspace)])
    if mcp_python is not None:
        if mcp_state is None:
            raise RuntimeError("MCP_STATE_REQUIRED")
        cmd.extend(["-c", "mcp_servers.deploom=" + (
            "{command=" + quote_toml(str(mcp_python)) +
            ',args=["-I","-m","deploom_mcp"],env={DEPLOOM_MCP_STATE_DIR=' +
            quote_toml(str(mcp_state)) +
            "},enabled=true,required=true,startup_timeout_sec=20,tool_timeout_sec=65}"
        )])
    cmd.append(DEFAULT_PROMPT)
    return cmd


def check_cli(codex: str) -> dict:
    out = {"present": False, "cliVersion": None, "jsonExecDocumented": False,
           "featureIsolationDocumented": False, "modelAccountAccess": "UNVERIFIED"}
    exe = shutil.which(codex)
    if exe is None:
        return out
    try:
        v = subprocess.run([exe,"--version"], capture_output=True, text=True, timeout=8)
        e = subprocess.run([exe,"exec","--help"], capture_output=True,text=True,timeout=8)
        h = subprocess.run([exe,"--help"], capture_output=True,text=True,timeout=8)
        feats = subprocess.run([exe,"features","list"],capture_output=True,text=True,timeout=8)
        text = v.stdout.strip().splitlines()[0][:120] if v.returncode == 0 and v.stdout.strip() else ""
        out.update(present=v.returncode == 0, cliVersion=text,
                   jsonExecDocumented=e.returncode == 0 and all(x in e.stdout for x in
                       ("--json","--model","--sandbox","--ignore-user-config","--strict-config")),
                   featureIsolationDocumented=h.returncode == 0 and "--disable" in h.stdout
                   and feats.returncode == 0 and all(
                       re.search(r"(?m)^" + re.escape(name) + r"\s", feats.stdout)
                       for name in NEEDED_FEATURES))
    except (OSError,subprocess.TimeoutExpired):
        pass
    return out


def clone_seed(seed: Path, dst: Path, expected: str) -> None:
    if expected != EXPECTED_SEED_SHA256 or not source_is_safe(seed):
        raise RuntimeError("FROZEN_SEED_NOT_VERIFIED")
    # No evaluator, reference solution, agent skills or hidden config copied.
    dst.mkdir(parents=True, exist_ok=False)
    for relative in sorted(FROZEN_FILENAMES):
        src = seed / relative
        if not src.is_file() or src.is_symlink():
            raise RuntimeError("UNSAFE_FROZEN_SEED_FILE")
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    if not source_is_safe(dst):
        raise RuntimeError("CLONED_SEED_NOT_IDENTICAL")


def validate_private_auth_source(auth_home: Path) -> dict:
    if not auth_home.is_dir() or auth_home.is_symlink():
        raise RuntimeError("PRIVATE_AUTH_HOME_UNAVAILABLE")
    auth = auth_home / "auth.json"
    if not auth.is_file() or auth.is_symlink():
        raise RuntimeError("CHATGPT_AUTH_FILE_UNAVAILABLE")
    # Copy only known applicable user restrictions alongside the auth. Codex
    # user config is ignored, so no foreign MCPs/projects are inherited.
    # Never serialize this file's data or any AGENTS/rules body.
    rule_files = []
    for name in ("AGENTS.md", "AGENTS.override.md"):
        p = auth_home / name
        if p.exists():
            if not p.is_file() or p.is_symlink():
                raise RuntimeError("UNSAFE_USER_INSTRUCTIONS")
            rule_files.append(p)
    rule_root = auth_home / "rules"
    if rule_root.exists():
        if not rule_root.is_dir() or rule_root.is_symlink():
            raise RuntimeError("UNSAFE_USER_RULES")
        for p in sorted(rule_root.iterdir()):
            if not p.is_file() or p.is_symlink() or p.suffix != ".rules":
                raise RuntimeError("UNRECOGNIZED_USER_RULES")
            rule_files.append(p)
    # The auth token is never hashed into a public output.
    return {"rules": rule_files, "auth": auth}


def private_home(root: Path, source: dict) -> tuple[Path, dict]:
    home = root / uuid.uuid4().hex
    home.mkdir(mode=0o700)
    shutil.copyfile(source["auth"], home / "auth.json")
    os.chmod(home / "auth.json", 0o600)
    digests = {}
    for p in source["rules"]:
        rel = (Path("rules") / p.name) if p.suffix == ".rules" else Path(p.name)
        dest = home / rel
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(p, dest)
        digests[str(rel).replace("\\","/")] = hashlib.sha256(dest.read_bytes()).hexdigest()
    return home, dict(sorted(digests.items()))


def isolated_env(root: Path, codex_home: Path) -> dict:
    forbidden = {"OPENAI_API_KEY","CODEX_API_KEY","GITHUB_TOKEN","GH_TOKEN",
                 "NPM_TOKEN","NODE_AUTH_TOKEN","AWS_SECRET_ACCESS_KEY",
                 "AZURE_OPENAI_API_KEY","PYTHONPATH","PYTHONHOME",
                 "GIT_ASKPASS","SSH_ASKPASS"}
    env = {k:v for k,v in os.environ.items()
           if k.upper() not in forbidden and not k.upper().startswith("DEPLOOM_")
           and not k.lower().startswith("npm_config_")}
    blank = root / "home"
    blank.mkdir(mode=0o700)
    env["HOME"] = str(blank)
    if os.name == "nt":
        env["USERPROFILE"] = str(blank)
    env.update(CODEX_HOME=str(codex_home), CI="true", NO_COLOR="1",
               npm_config_cache=str(root / "npm-cache"),
               npm_config_audit="false", npm_config_fund="false")
    return env


class Ledger:
    """Exclusive durable claim. Existing or interrupted outputs are NEVER run again."""
    def __init__(self, path: Path, model: str, effort: str, frozen_sha: str):
        if path.exists():
            raise RuntimeError("RUN_LEDGER_EXISTS_NO_RETRY")
        path.mkdir(parents=True, exist_ok=False)
        self.path = path
        self.claim = path / "NO_AUTOMATIC_RETRY.claim"
        with self.claim.open("x",encoding="ascii") as f:
            f.write("NEVER_RESUME_OR_AUTORERUN\n")
            f.flush()
            os.fsync(f.fileno())
        self.doc = {
            "schemaVersion": 1, "runId": uuid.uuid4().hex,
            "runState": "PREPARED", "model": model, "reasoning": effort,
            "seedSha256": frozen_sha, "automaticRetry": False,
            "sessions": {a: {"sessionId":uuid.uuid4().hex,
                             "state":"PENDING","nativeThreadId":None}
                         for a in ("A","B")},
        }
        self.write()

    def write(self):
        tmp = self.path / ("ledger-" + uuid.uuid4().hex + ".tmp")
        with tmp.open("x",encoding="utf-8") as f:
            json.dump(self.doc,f,sort_keys=True,indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,self.path/"session-ledger.json")

    def mark(self, arm: str, state: str, **fields):
        current = self.doc["sessions"][arm]["state"]
        allowed = {
            "PENDING": {"PREPARED","ABORTED"},
            "PREPARED": {"RUNNING","ABORTED"},
            "RUNNING": {"COMPLETED","UNKNOWN"},
            "COMPLETED": set(), "ABORTED": set(), "UNKNOWN": set(),
        }
        if state not in allowed[current]:
            raise RuntimeError("UNSAFE_LEDGER_STATE_TRANSITION")
        self.doc["sessions"][arm].update(state=state, **fields)
        self.doc["runState"] = "ACTIVE" if state == "RUNNING" else (
            "UNKNOWN" if state == "UNKNOWN" else self.doc["runState"])
        self.write()

    def record_native_thread(self, arm: str, native_id: str):
        item=self.doc["sessions"][arm]
        if (item["state"]!="RUNNING" or item["nativeThreadId"] not in (None,native_id)
            or not isinstance(native_id,str)):
            raise RuntimeError("NATIVE_THREAD_ID_CONFLICT_OR_UNSAFE_STATE")
        item["nativeThreadId"]=native_id
        self.write()

    def finish(self, status: str):
        if status not in ("COMPLETE","UNKNOWN","ABORTED"):
            raise RuntimeError("UNSAFE_FINAL_LEDGER_STATUS")
        self.doc["runState"] = status
        self.write()


def execute(argv: list[str], cwd: Path, env: dict, wall_limit: int, tools_limit: int,
            max_input: int, max_output: int, rate=None, max_api_estimate=None,
            on_native_thread=None) -> dict:
    if rate is not None or max_api_estimate is not None:
        raise RuntimeError("NATIVE_BILLING_UNAVAILABLE_NO_API_ESTIMATES")
    start = time.monotonic()
    collector = UsageCollector(tools_limit)
    cause = [None]
    done = threading.Event()
    args = {"cwd":str(cwd),"env":env,"stdout":subprocess.PIPE,
            "stderr":subprocess.DEVNULL,"text":True,"errors":"replace"}
    with OwnedProcess(argv,**args) as owned:
        proc=owned.proc
        def stop(code):
            if cause[0] is None:
                cause[0]=code
                owned.stop()
        def timer():
            while not done.wait(0.1):
                if time.monotonic()-start > wall_limit:
                    stop("WALL_WATCHDOG")
                    return
        watcher=threading.Thread(target=timer,daemon=True)
        watcher.start()
        try:
            for line in proc.stdout:
                if len(line) > 2_000_000:
                    stop("OVERSIZED_EVENT")
                    break
                try: event=json.loads(line)
                except ValueError:
                    collector.malformed += 1
                    stop("MALFORMED_JSONL")
                    break
                if not isinstance(event,dict) or not collector.ingest(event):
                    stop("UNSAFE_NATIVE_EVENT_OR_TOOL_BUDGET")
                    break
                if event.get("type")=="thread.started" and on_native_thread is not None:
                    native=collector.result()["nativeThreadId"]
                    if native is not None:
                        on_native_thread(native)
                tokens=collector.result()["nativeUsage"]
                if ((tokens["input_tokens"] is not None and tokens["input_tokens"]>max_input)
                    or (tokens["output_tokens"] is not None and tokens["output_tokens"]>max_output)):
                    stop("SOFT_TOKEN_OVERSHOOT_REPORTED")
                    break
        finally:
            done.set()
            watcher.join(timeout=3)
        try: exit_code=proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            stop("PROCESS_EXIT_UNCERTAIN")
            exit_code=proc.poll()
    parsed=collector.result()
    token_overrun=any(
        parsed["nativeUsage"][k] is not None and parsed["nativeUsage"][k]>limit
        for k,limit in (("input_tokens",max_input),("output_tokens",max_output)))
    parsed.update({
        "cliExitCode":exit_code,
        "wallSeconds":round(time.monotonic()-start,3),
        "stopReason":cause[0], "exitSuccess":exit_code==0 and cause[0] is None
            and parsed["eventSchemaSafe"] and not token_overrun,
        "tokenOvershoot":token_overrun,
        "nativeBillingUsd":None, "costUsd":None,
        "billingStatus":"NATIVE_BILLING_NOT_REPORTED",
        "limits":{"inputTokensSoft":max_input,"outputTokensSoft":max_output,
                  "toolCallStopAfterStarted":tools_limit,
                  "wallSecondsOwnedProcessTreeWatchdog":wall_limit,
                  "hardTokenCapSupported":False},
    })
    return parsed


def validate_gate(seed: Path, wheel: Path, p2: Path, d1: Path, yellow: Path) -> dict:
    if not source_is_safe(seed):
        raise RuntimeError("FROZEN_CHALK_SEED_SHA_MISMATCH")
    if not wheel.is_file():
        raise RuntimeError("PACKAGED_WHEEL_MISSING")
    wheel_sha=hashlib.sha256(wheel.read_bytes()).hexdigest()
    try:
        p=json.loads(p2.read_text(encoding="utf-8"))
        d=json.loads(d1.read_text(encoding="utf-8"))
        y=json.loads(yellow.read_text(encoding="utf-8"))
    except (OSError,ValueError,TypeError):
        raise RuntimeError("MISSING_OR_INVALID_PREFLIGHT_RECEIPT") from None
    required_controls=("baselinePASS","versionOnlyInstallPASS",
        "versionOnlyRuntimeFAIL","referenceCodePatchPASS",
        "hardcodedSuccessRejected","unsafeControlsFAIL","realPackagedStdioPASS")
    if (p.get("status")!="PASS" or
        any(p.get("checks",{}).get(c) is not True for c in required_controls)
        or p.get("provenance",{}).get("mcpWheelSha256")!=wheel_sha):
        raise RuntimeError("P2_PUBLIC_ORACLE_CONTROLS_FAILED")
    # The D1 planning-only GO still refers to original Chalk5.3.0.
    # The NEW requirement is the separately labeled, validated Chalk6
    # policy100 and independent runtime evidence, not a fabricated P2 pass.
    if d.get("wheelSha256")!=wheel_sha or d.get("gate")!="GO":
        raise RuntimeError("PACKAGED_STDIO_D1_GATE_NO_GO")
    try:
        checked=check_policy_receipt(d,wheel_sha,EXPECTED_SEED_SHA256)
    except (DemoFailure,KeyError,TypeError,ValueError):
        raise RuntimeError("D1_SEPARATE_CHALK6_POLICY_NOT_PROVEN") from None
    scenarios={x["id"]:x for x in checked["scenarios"]}
    original=scenarios.get("ORIGINAL_EXACT_5_3_0",{})
    six=scenarios.get("SEPARATE_CURRENT_POLICY_MIN_6_0_0",{})
    if (original.get("finalPolicyGoal")!="blocked"
        or original.get("lagOk")!=1
        or six.get("exactTarget")!="6.0.0"
        or six.get("finalPolicyGoal")!="feasible"
        or six.get("lagOk")!=2
        or six.get("requiresAuthorization") is not True):
        raise RuntimeError("CHALK6_OBJECTIVE_POLICY_MISMATCH")
    runtime=y.get("independentRuntime") or {}
    if (y.get("status")!="PASS" or y.get("wheelSha256")!=wheel_sha or
        y.get("modelCalls")!=0 or y.get("codexSessions")!=0 or
        runtime.get("status")!="PASS"
        or runtime.get("attribution")!="EVALUATOR_ONLY_NOT_AGENT"
        or any(runtime.get("checks",{}).get(k) is not True for k in (
            "lockGeneration","cleanInstall","build","test","runtimeMultiInput"))):
        raise RuntimeError("CHALK6_INDEPENDENT_RUNTIME_PREFLIGHT_NOT_PROVEN")
    return {
        "scenario":"SEPARATE_PUBLIC_CHALK6_EXACT_YELLOW",
        "originalP2": {"exactTarget":"5.3.0","policyStatus":"BLOCKED_1_OF_2",
                       "referenceRuntime":"EVALUATOR_ONLY"},
        "chosenTask":{"exactTargets":{"chalk":"6.0.0"},
            "approvedVersionLines":{"chalk":[6]},"mutablePackages":["chalk"],
            "immutablePackages":{"is-number":"7.0.0"},
            "targetLevel":"yellow","minLagOkPct":80,
            "requiresAuthorization":True,"planningAuthority":"PLANNING_ONLY",
            "policyStatus":"FEASIBLE_2_OF_2",
            "runtimeVerdict":"INDEPENDENT_EVALUATOR_CONTROL_ONLY"},
        "wheelSha256":wheel_sha,
        "frozenSourceSha256":EXPECTED_SEED_SHA256,
        "sourceUnchanged":True,
    }


def check_limits(args):
    limits=(("wall",MAX_WALL),("tools",MAX_TOOLS),
            ("input_cap",MAX_INPUT),("output_cap",MAX_OUTPUT))
    for name,cap in limits:
        val=getattr(args,name)
        if type(val) is not int or not 1<=val<=cap:
            raise RuntimeError("LIMIT_OUTSIDE_APPROVED_BOUND")
    if args.model != MODEL or args.effort != EFFORT:
        raise RuntimeError("TASK_MODEL_EFFORT_NOT_LOCKED")


def check_host_location(seed: Path, out: Path) -> None:
    if out.exists():
        raise RuntimeError("RUN_LEDGER_EXISTS_NO_RETRY")
    s=seed.resolve()
    o=out.resolve()
    if o==s or s in o.parents or o in s.parents:
        raise RuntimeError("OUTPUT_OVERLAPS_FROZEN_SEED")
    # Fail closed on inherited project instructions and MCP config without
    # silently stripping them. Choose a neutral OS temp dir for both arms.
    for p in [o.parent, *o.parent.parents]:
        if any((p/name).exists() for name in (
            "AGENTS.md","AGENTS.override.md",".codex/config.toml")):
            raise RuntimeError("APPLICABLE_PARENT_PROJECT_RULES_NOT_CAPTURED")
    if not o.parent.exists():
        raise RuntimeError("OUTPUT_PARENT_MISSING")
    if not os.access(o.parent,os.W_OK):
        raise RuntimeError("OUTPUT_PARENT_UNWRITABLE")


def probe_public_network() -> None:
    """Read-only native network check. Respect the operator's proxy unchanged."""
    import urllib.error
    import urllib.request
    endpoints = (
        ("NPM_REGISTRY_UNAVAILABLE", "https://registry.npmjs.org/chalk/6.0.0"),
        ("PYPI_UNAVAILABLE", "https://pypi.org/simple/build/"),
        ("PUBLIC_GITHUB_UNAVAILABLE", "https://api.github.com/repos/DepLoom/mcp"),
    )
    for code, url in endpoints:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "DepLoom-D1-public-readonly-preflight"})
            with urllib.request.urlopen(req, timeout=8) as response:
                if response.status != 200:
                    raise RuntimeError(code)
                response.read(1)
        except (OSError, ValueError, urllib.error.URLError):
            # No raw URL, credentials, private proxy settings or response
            # bodies may enter the benchmark evidence.
            raise RuntimeError(code) from None


def check_ready(args) -> dict:
    check_limits(args)
    if not args.seed.is_dir():
        raise RuntimeError("CHALK_SEED_MISSING")
    check_host_location(args.seed,args.out)
    gate=validate_gate(args.seed,args.wheel,args.p2_preflight,
                       args.mcp_preflight,args.yellow_preflight)
    native=check_cli(args.codex)
    if (not native["present"] or not native["jsonExecDocumented"]
        or not native["featureIsolationDocumented"]
        or not re.search(r"\b0\.165\.0\b",native["cliVersion"] or "")):
        raise RuntimeError("CODEX_0_165_0_ISOLATION_FLAGS_NOT_CONFIRMED")
    if not args.codex_auth_home:
        raise RuntimeError("CODEX_AUTH_HOME_REQUIRED")
    auth=validate_private_auth_source(args.codex_auth_home)
    probe_public_network()
    # Does NOT read or print a credential and never calls a model. The
    # operator's real login/model entitlement remains an external attestation.
    return {"gate":gate, "native":native,
            "userRuleFilenames":sorted(p.name for p in auth["rules"]),
            "noModelCalls":True,"authSourceValidated":True}


def dry_run(out: Path) -> dict:
    if out.exists():
        raise RuntimeError("OUTPUT_EXISTS")
    out.mkdir(parents=True)
    a=[
        {"type":"thread.started","thread_id":"FAKE-thread-A"},
        {"type":"item.started","item":{"id":"fake-command","type":"command_execution"}},
        {"type":"turn.completed","usage":{"input_tokens":1100,"cached_input_tokens":300,
            "output_tokens":400,"reasoning_output_tokens":100}},
    ]
    b=[
        {"type":"thread.started","thread_id":"FAKE-thread-B"},
        {"type":"item.started","item":{"id":"fake-mcp","type":"mcp_tool_call"}},
        {"type":"item.completed","item":{"id":"fake-mcp","type":"mcp_tool_call"}},
        {"type":"item.started","item":{"id":"fake-command","type":"command_execution"}},
        {"type":"turn.completed","usage":{"input_tokens":1400,"cached_input_tokens":400,
            "output_tokens":430,"reasoning_output_tokens":120}},
    ]
    def calculate(events):
        c=UsageCollector()
        for e in events:c.ingest(e)
        return c.result()
    result={"mode":"FAKE_DRY_RUN_NO_MODEL_CALLS","truth":"SYNTHETIC_NOT_EVIDENCE",
            "cliInvoked":False,"model":"FAKE_NOT_AN_AGENT",
            "A":calculate(a),"B":calculate(b),
            "correctnessA":None,"correctnessB":None,
            "costUsd":None,"manualHelpEvents":None,
            "comparison":"NOT_MEASURED"}
    (out/"d1-fake.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    return result


def score_candidate(baseline:Path,workspace:Path,sha:str) -> dict:
    return verify_chalk6_frozen(baseline,workspace,sha)


def launch(args) -> dict:
    if not args.approve_one_paid_pair or not args.confirm_model_access or not args.confirm_network_access:
        raise RuntimeError("EXPLICIT_LOCAL_PAIR_MODEL_NETWORK_ATTESTATION_REQUIRED")
    if args.manual_a is None or args.manual_b is None or min(args.manual_a,args.manual_b)<0:
        raise RuntimeError("MANUAL_HELP_COUNT_MUST_BE_DECLARED")
    ready=check_ready(args)  # all checks BEFORE first agent
    # Both arms have identical immutable task, prompt, rules, model/effort,
    # CLI/sandbox and cold network/cache conditions. B adds only packaged MCP.
    auth=validate_private_auth_source(args.codex_auth_home)
    ledger=Ledger(args.out,args.model,args.effort,EXPECTED_SEED_SHA256)
    results={}
    try:
        with tempfile.TemporaryDirectory(prefix="deploom-d1-private-auth-") as secret:
            private=Path(secret)
            homes=[private_home(private,auth) for _ in ("A","B")]
            if homes[0][1]!=homes[1][1]:
                raise RuntimeError("A_B_USER_RULE_IDENTITY_MISMATCH")
            for i,arm in enumerate(("A","B")):
                start=time.monotonic()
                root=args.out/arm
                root.mkdir()
                workspace=root/"workspace"
                clone_seed(args.seed,workspace,EXPECTED_SEED_SHA256)
                env=isolated_env(root,homes[i][0])
                ledger.mark(arm,"PREPARED",instructionsDigest=homes[i][1])
                mcp_python=None
                setup_t=time.monotonic()
                if arm=="B":
                    # MCP package and planner state are private setup data,
                    # not shareable benchmark artifacts.
                    v=private/"B-mcpvenv"
                    subprocess.run([sys.executable,"-m","venv",str(v)],check=True,
                                   capture_output=True,timeout=55)
                    mcp_python=v/("Scripts/python.exe" if os.name=="nt" else "bin/python")
                    cmd=[str(mcp_python),"-m","pip","install",
                         "--no-input","--disable-pip-version-check",str(args.wheel.resolve())]
                    inst=subprocess.run(cmd,capture_output=True,timeout=180)
                    if inst.returncode:
                        raise RuntimeError("B_PACKAGED_MCP_INSTALL_FAILED")
                setup_seconds=round(time.monotonic()-setup_t,3) if arm=="B" else 0.0
                cmd=codex_command(shutil.which(args.codex),workspace,args.model,args.effort,
                                  mcp_python,private/"B-mcp-state")
                # Durable RUNNING state is written BEFORE launching Codex.
                ledger.mark(arm,"RUNNING",startMonotonicUnknown=True)
                try:
                    metrics=execute(cmd,workspace,env,args.wall,args.tools,
                                    args.input_cap,args.output_cap,
                                    on_native_thread=lambda tid: ledger.record_native_thread(arm,tid))
                except BaseException:
                    ledger.mark(arm,"UNKNOWN",reason="PROCESS_OUTCOME_UNCERTAIN")
                    raise
                if not metrics["exitSuccess"]:
                    ledger.mark(arm,"UNKNOWN",nativeThreadId=metrics.get("nativeThreadId"),
                                reason=metrics.get("stopReason") or "CLI_OR_USAGE_INCOMPLETE")
                    raise RuntimeError("A_OR_B_SESSION_UNSAFE_NO_AUTO_RETRY")
                correctness=score_candidate(args.seed,workspace,EXPECTED_SEED_SHA256)
                if correctness.get("status") not in ("PASS","FAIL") or correctness.get("reason","") == "INVALID_FROZEN_BASELINE":
                    ledger.mark(arm,"UNKNOWN",nativeThreadId=metrics["nativeThreadId"],
                                reason="INDEPENDENT_ORACLE_UNAVAILABLE")
                    raise RuntimeError("INDEPENDENT_ORACLE_OUTCOME_UNSAFE")
                # An ordinary incorrect agent result is a valid observed
                # failure; a broken oracle or installation is not.
                if correctness.get("reason","").startswith((
                    "CHALK_ORACLE_INFRASTRUCTURE_","CHALK_CLEAN_INSTALL_FAILED")):
                    ledger.mark(arm,"UNKNOWN",nativeThreadId=metrics["nativeThreadId"],
                                reason="INDEPENDENT_ORACLE_INFRASTRUCTURE")
                    raise RuntimeError("INDEPENDENT_ORACLE_INFRASTRUCTURE")
                metrics["mcpWheelSetupWallSeconds"]=setup_seconds
                metrics["endToEndWallSecondsIncludingScorer"]=round(time.monotonic()-start,3)
                metrics["manualHelpEvents"]=args.manual_a if arm=="A" else args.manual_b
                result={"measurement":metrics,"correctness":correctness,
                        "requestedModel":args.model,"requestedReasoning":args.effort,
                        "finalSourceSha256":source_sha(workspace),
                        "manualHelpDeclaredByOperator":metrics["manualHelpEvents"]}
                results[arm]=result
                ledger.mark(arm,"COMPLETED",nativeThreadId=metrics["nativeThreadId"],
                            cliExitCode=metrics["cliExitCode"],
                            correctnessStatus=correctness["status"])
                (root/"receipt.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
        # No credential file inside args.out; private auth home is deleted.
        a,b=results["A"],results["B"]
        used=b["measurement"]["toolCalls"].get("mcp_tool_call",0)>0
        if not used:
            verdict="B_DID_NOT_USE_MCP_NO_TREATMENT_EFFECT"
        elif a["correctness"]["status"]=="PASS" and b["correctness"]["status"]=="PASS":
            aw=a["measurement"]["endToEndWallSecondsIncludingScorer"]
            bw=b["measurement"]["endToEndWallSecondsIncludingScorer"]
            verdict=("B_FASTER_PILOT_ONLY" if bw<aw else
                     "B_SLOWER_PILOT_ONLY" if bw>aw else "TIE_PILOT_ONLY")
        elif b["correctness"]["status"]=="PASS":
            verdict="B_CORRECT_A_INCORRECT_PILOT_ONLY"
        elif a["correctness"]["status"]=="PASS":
            verdict="A_CORRECT_B_INCORRECT_PILOT_ONLY"
        else:
            verdict="BOTH_INCORRECT_PILOT_ONLY"
        result={"status":"OBSERVED_PILOT_NOT_STATISTICAL",
            "runId":ledger.doc["runId"],"model":args.model,"reasoning":args.effort,
            "cliVersion":ready["native"]["cliVersion"],"agentSessions":2,
            "task":ready["gate"]["chosenTask"],"frozenSha256":EXPECTED_SEED_SHA256,
            "wheelSha256":ready["gate"]["wheelSha256"],
            "manualAssistanceKnown":True,"nativeBillingUsd":None,
            "cacheAndNetwork":"SEPARATE_COLD_CACHES_LIVE_NETWORK_NOT_PINNED",
            "bActuallyUsedMcp":used,"verdict":verdict,"arms":results}
        ledger.finish("COMPLETE")
        (args.out/"pair-summary.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
        return result
    except BaseException:
        ledger.finish("UNKNOWN")
        raise


def build_parser():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="mode",required=True)
    doctor=sub.add_parser("doctor")
    doctor.add_argument("--codex",default="codex")
    dr=sub.add_parser("dry-run")
    dr.add_argument("--out",type=Path,required=True)
    sc=sub.add_parser("score")
    sc.add_argument("--baseline",type=Path,required=True)
    sc.add_argument("--candidate",type=Path,required=True)
    sc.add_argument("--baseline-sha",required=True)
    sc.add_argument("--out",type=Path,required=True)
    for name in ("preflight","run"):
        op=sub.add_parser(name)
        op.add_argument("--seed",type=Path,required=True)
        op.add_argument("--wheel",type=Path,required=True)
        op.add_argument("--p2-preflight",type=Path,required=True)
        op.add_argument("--mcp-preflight",type=Path,required=True)
        op.add_argument("--yellow-preflight",type=Path,required=True)
        op.add_argument("--out",type=Path,required=True)
        op.add_argument("--codex",default="codex")
        op.add_argument("--codex-auth-home",type=Path)
        op.add_argument("--model",default=MODEL)
        op.add_argument("--effort",default=EFFORT)
        op.add_argument("--wall",type=int,default=MAX_WALL)
        op.add_argument("--tools",type=int,default=MAX_TOOLS)
        op.add_argument("--input-cap",type=int,default=MAX_INPUT)
        op.add_argument("--output-cap",type=int,default=MAX_OUTPUT)
        if name=="run":
            op.add_argument("--approve-one-paid-pair",action="store_true")
            op.add_argument("--confirm-model-access",action="store_true")
            op.add_argument("--confirm-network-access",action="store_true")
            op.add_argument("--manual-a",type=int)
            op.add_argument("--manual-b",type=int)
    return p


def main() -> int:
    args=build_parser().parse_args()
    try:
        if args.mode=="doctor":
            out={"mode":"NO_MODEL_DOCTOR",**check_cli(args.codex)}
        elif args.mode=="dry-run":
            out=dry_run(args.out)
        elif args.mode=="score":
            out=score_candidate(args.baseline,args.candidate,args.baseline_sha)
            if args.out.exists():
                raise RuntimeError("SCORE_OUTPUT_EXISTS")
            args.out.parent.mkdir(parents=True,exist_ok=True)
            args.out.write_text(json.dumps(out,indent=2,sort_keys=True)+"\n")
        elif args.mode=="preflight":
            ready=check_ready(args)
            out={"status":"READY_NO_MODEL_PREFLIGHT_ONLY",
                "objective":ready["gate"]["chosenTask"],
                "cli":ready["native"],
                "authSourceValidated":ready["authSourceValidated"],
                "userRuleFilenames":ready["userRuleFilenames"],
                "paidAgentSessions":0}
        else:
            out=launch(args)
        printed=(out if args.mode in ("doctor","dry-run","score","preflight")
                 else {"status":out["status"],"result":out["verdict"],
                       "agentSessions":out["agentSessions"]})
        print(json.dumps(printed,sort_keys=True))
        if args.mode=="score" and out.get("status")!="PASS":
            return 2
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, TypeError) as e:
        reason=(str(e) if isinstance(e,RuntimeError)
                and re.fullmatch(r"[A-Z0-9_]+",str(e)) else "UNVERIFIED")
        print(json.dumps({"status":"NO_GO","reason":reason},sort_keys=True))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
