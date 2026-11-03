# D1 local one-pair A/B — separate frozen Chalk 6.0.0 task

**Status: runner engineering / no-agent preflight. No paid sessions launched
by this repository, GitHub CI or ChatGPT.** This is a new locally selected task,
**not** a reinterpretation of the immutable P2 Chalk 5.3.0 negative control.

## Contract and experiment

- Original P2 seed: Chalk 4.1.2, is-number 7.0.0; exact frozen source digest
  \`4adfa455d02e9f354e5b47e7b4d983f6676b620b13dc1454bda9248272d97cb6\`.
- **New A/B task:** change **only** Chalk to **6.0.0**; Yellow ≥80% over
  both active dependencies, mutable=["chalk"], approved major={"chalk":[6]},
  immutable is-number 7.0.0, no exclusion/threshold/manifest scope changes.
  Both arms see the **same frozen eight files**, prompt and public docs.
- **A = bare Codex**, **B = same Codex + exact installed MCP wheel**.
  No agent Skill in either arm. Both use \`gpt-6.1-terra\`, medium reasoning,
  pinned Codex CLI **0.165.0**, workspace-write sandbox, public network,
  no-approval batch, ephemeral session, equal applicable AGENTS/rules
  snapshots and cold per-arm caches. Both use the exact same frozen README;
  the prompt explicitly supersedes its **old Chalk5** task language without
  changing README or exposing a solution.
- **Only B** receives the enabled, required \`mcp_servers.deploom\` stdio
  entry pointing to a wheel installed outside its editable workspace.
  All third-party MCPs/plugins/apps and global user config are disabled.
  A native \`thread.started.thread_id\` is stored for each arm, not invented.
- Scorer uses the existing **independent P2 static lock/integrity/origin and
  real npm ci/build/test/multi-input anti-hardcode runtime** with explicit
  \`target_dependencies={"chalk":"6.0.0","is-number":"7.0.0"}\`.
  The ORIGINAL P2 validator still defaults to Chalk5.3.0. The score is for
  final runtime only, **not** independent reproof of Yellow.
- \`d1-mcp-value.json\` proves original exact5 planning only.
  \`d1-yellow-policy.json\` plus matching \`d1-mcp-value.json\` independently
  establish separately labeled Chalk6 policy 2/2 (100%) and evaluator-only
  source adaptation runtime PASS **before** paid sessions. The reference
  source/solution is **never** copied to the agent workspace.
- \`PROPOSED / PLANNING_ONLY / NOT_VERIFIED\` are planning statuses; policy
  \`feasible\` is a different dimension; only the independent agent-candidate
  runtime oracle yields a correctness verdict. No A/B outcome is inferred
  from the evaluator-only positive.
- A single sequential A then B pair is pilot feasibility only, **not**
  statistical evidence of MCP uplift. No retry, history comparison, “C arm”
  or post-stop resumption is automatic.

## Strict local preflight and budget

The user selected **Codex CLI 0.165.0**, \`gpt-6.1-terra\`, **medium**.
\`doctor\` and \`preflight\` make **zero inference/model calls**. \`preflight\`
checks CLI feature isolation switches, exact model/effort/budgets, the
public frozen SHA, wheel hash, P2 controls, installed-wheel D1 and separate
Chalk6 positive receipt. It validates that a private ChatGPT auth.json
exists; it does **not** read or emit the token or prove online entitlement.
A real model call is permitted **only** from the explicit local \`run\`
command with one-pair, model-access and network-access attestations.

Windows 5.1 setup uses the already proven full P2/stdio/Yellow demo.
The output directory must be fresh and outside any project repository.
Run this from a clean exact MCP PR #13 HEAD (Python 3.12, Node 22/npm,
local Codex CLI0.165.0); **do not** run any of these against a 403 registry.
The local curator must first restore public network access **without** a proxy
or authentication workaround.

~~~powershell
$ErrorActionPreference = 'Stop'
$Sha = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'WRONG_CHECKOUT' }
git submodule update --init --recursive
if ($LASTEXITCODE -ne 0) { throw 'SUBMODULE_FAILED' }
$Evidence = Join-Path $env:TEMP ('deploom-chalk6-preflight-' + [guid]::NewGuid().ToString('N'))
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\d1_yellow_windows_demo.ps1 -ExpectedMcpSha $Sha -OutputDir $Evidence
if ($LASTEXITCODE -ne 0) { throw 'NETWORK_OR_P2_OR_YELLOW_NO_GO' }

$Bench = Join-Path $env:TEMP ('deploom-ab-' + [guid]::NewGuid().ToString('N'))
$Wheel = Join-Path $Evidence 'dist\deploom_mcp-0.2.0-py3-none-any.whl'
$Args = @('--seed',(Join-Path $Evidence 'd1-frozen-chalk'),
  '--wheel',$Wheel,
  '--p2-preflight',(Join-Path $Evidence 'd1-p2-control.json'),
  '--mcp-preflight',(Join-Path $Evidence 'd1-mcp-value.json'),
  '--yellow-preflight',(Join-Path $Evidence 'd1-yellow-policy.json'),
  '--codex-auth-home',(Join-Path $env:USERPROFILE '.codex'),
  '--out',$Bench,'--model','gpt-6.1-terra','--effort','medium',
  '--wall','600','--tools','40','--input-cap','20000','--output-cap','4000')
python scripts/d1_pilot.py preflight @Args
if ($LASTEXITCODE -ne 0) { throw 'NO_GO_DO_NOT_START_AGENTS' }
~~~

After the operator verifies \`READY_NO_MODEL_PREFLIGHT_ONLY\`, confirms
account model access and public network connectivity, and has made a private,
out-of-repository auth available, the **user** may initiate exactly one
pair locally (NOT in cloud ChatGPT):

~~~powershell
python scripts/d1_pilot.py run @Args --approve-one-paid-pair --confirm-model-access --confirm-network-access --manual-a 0 --manual-b 0
if ($LASTEXITCODE -ne 0) { throw 'RUN_ABORTED_NO_AUTO_RETRY' }
Get-Content -LiteralPath (Join-Path $Bench 'pair-summary.json')
~~~

No Bash \`&&\` in PowerShell 5.1. \`--manual-a 0 --manual-b 0\` is an
operator declaration that **no** manual assistance occurs; any unexpected
intervention invalidates this matched run and must be recorded separately.
A \`RUN_LEDGER_EXISTS_NO_RETRY\` or \`UNKNOWN\` is **not** a reason to rerun the
same arm or same output. Escalate to a fresh explicitly authorized experiment,
not automatic continuation.

## Security, comparability and stopping rules

1. The runner's \`NO_AUTOMATIC_RETRY.claim\` and fsynced
   \`session-ledger.json\` are written **before** launching A. Each arm has
   unique run/session IDs; \`RUNNING\` is durable before its native process.
   Actual Codex thread ID is recorded only from native \`thread.started\`.
   Missing/ambiguous thread, missing input/output usage, malformed JSONL,
   tool-count breach, token overshoot, CLI error or unknown process outcome
   produce \`UNKNOWN\` and **prevent B**. Nothing resumes automatically.
2. Per-arm limits are input **20,000 soft**, output **4,000 soft**, started
   tool items **40** and owned-process-tree watchdog **600 seconds**.
   The user's maxima 25k/8k/45/900 are **not** selected and never expanded.
   Tool count is unique \`item.id\` across \`item.started\`/
   \`item.completed\`, grouped by \`command_execution\`, \`mcp_tool_call\`,
   \`web_search\`, \`file_change\`; an unknown item type counts
   conservatively. Cached input is a subset of input; reasoning output is a
   subset of output (not additional spending). Missing optional cached or
   reasoning counters stay JSON null and cannot support cache/reasoning
   comparisons. Codex \`turn.completed\` may expose counters only **after**
   spending; **no hard token budget or cost cap is claimed**.
3. Windows launches a dedicated Job Object with \`KILL_ON_JOB_CLOSE\` and
   terminates **only its owned child tree**; Linux launches a separate process
   group, tests include an ignoring child and unrelated foreign process.
   Unavailable job ownership is NO_GO; no taskkill-by-name or unsafe fallback.
4. Private \`auth.json\` is copied **only** to isolated OS temporary
   \`CODEX_HOME\` stores, **outside** all benchmark artifacts, and deleted
   after execution (including failures). The original CODEX_HOME user
   \`AGENTS.md\`, \`AGENTS.override.md\`, and \`rules/*.rules\`, when present,
   are retained identically in both arms. \`--ignore-rules\` is **never** used.
   \`--ignore-user-config\` excludes private third-party MCP definitions;
   project/ancestor config or AGENTS constraints that cannot be safely
   applied fail closed. Explicit feature disables guard plugins/apps/multi-agent.
   If the local CLI lacks these controls, **NO_GO**, no bypass.
5. A and B are sequential with separate cold caches; external live
   npm/OSV/website responses **are not byte-pinned**. Do not claim causal
   token/time uplift if public network changes between arms; report
   \`SEPARATE_COLD_CACHES_LIVE_NETWORK_NOT_PINNED\`.
6. Wall time records CLI time, B's MCP installation/setup, and end-to-end
   time **including the independent scoring process** separately.
   Native API/subscription billing isn't exposed; \`costUsd=null\`,
   \`nativeBillingUsd=null\`. Manual-help declarations are recorded.
   Native token-count comparisons depend on valid usage in both arms.
7. Privacy: no raw Codex JSONL, agent transcript, auth.json, private config,
   third-party repository, registry credential, evaluator reference solution,
   or agent-editable oracle appears in the workspace or public CI artifact.
   The scorer is outside the agent workspaces. Public frozen seed, whitelisted
   machine receipts and source hashes are enough for audit.

## CI and acceptance

Run the existing \`.github/workflows/d1-pilot-preflight.yml\`: Linux and
Windows perform **zero-model** oracle/P2/MCP/Yellow checks, then the
\`tests/test_d1_ab_pair.py\` safety suite (synthetic native events,
uncompleted ledger, duplicate claim, malformed/missing usage, no-model
objective mismatch, immutable P2, and actual hanging child cleanup on the
OS). \`scripts/d1_pilot.py dry-run\` emits **fake** JSON only, not an A/B
result. The full Windows D1 job and Linux job must be green; standard
Windows unit CI alone is not a substitute.

**READY** means only that the offline runner and same-policy no-agent path
are validated. Real A/B stays **NO-GO on the curator's machine** while
the public GitHub/npm/PyPI proxy returns 403 or \`preflight\` fails.
There are still **0 paid agent sessions** until that local gate passes and
the user explicitly invokes \`run\`. No merge/release/publication in this work.
