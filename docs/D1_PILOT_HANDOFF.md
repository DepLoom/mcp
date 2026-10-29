# DepLoom D1 — one matched A/B pilot, **NO MODEL INVOCATION YET**

Review branch is stacked on unchanged P2 PR #10 HEAD `72e9899932fd5e56280c983d33353fb4de27b8ff`. Core #4, MCP #6/#7 and P1 #9 remain untouched. No merge/release/package publication/paid Codex calls were authorized in P3.

## Falsifiable hypothesis and the non-negotiable product-value gate

**H1:** For the same *exact* Chalk 4.1.2 → 5.3.0 breaking-API task, optional DepLoom MCP planning reduces invalid target selection or repair effort without harming independent final correctness versus the same bare agent. **H0:** MCP is irrelevant overhead or suggests a different version/unknown target. A faster unsuccessful migration is not success. One A/B pair is **pilot feasibility**, not a causal/statistical effectiveness claim.

First run `scripts/d1_mcp_value_preflight.py` against the actual frozen public Chalk project with **installed-wheel MCP stdio**; it sends (1) conservative pinned policy and (2) latest-approved-major/advisory policy. It records only whitelisted Chalk/is-number target/status and planning authority. **GO requires an exact `PROPOSED chalk@5.3.0`**, verified planning-only authority, preserved is-number, and no partial draft. A successful stdio handshake, generic `POLICY_SATISFIED`, missing security evidence or Chalk 5.6.x are **NO_GO**, never a fabricated advantage.

A negative result is a concrete product gap: exact-version target policy cannot be expressed/applied through the existing MCP `get_upgrade_plan` contract (or the solver doesn't expose a useful migration action). **Do not start paid B or A** until addressed or the experiment is explicitly redesigned and reapproved for a new objective. Do not manufacture a pinned registry with only two Chalk versions to make MCP appear useful.

## Observed product result — **NO_GO**, exact reviewed runtime

On [the real GitHub Actions run 40928352599](https://github.com/DepLoom/mcp/actions/runs/40928352599), with the P2 public snapshot SHA verified and **`draft_max_candidates=128`**, both valid `get_upgrade_plan` requests returned `ok=true`, `DRAFT_READY`, `PLANNING_ONLY/NOT_VERIFIED`, `chalk.status=NO_TARGET`, **`chalk.target=null`**, and `is-number.status=DEFERRED`. The former run with cap 5 returned `CANDIDATE_SEARCH_TRUNCATED`; widening candidates removes the cap symptom but **does not make the Chalk target actionable**. Therefore the stored receipt is `gate=NO_GO`, `productGap=NO_ACTIONABLE_CHALK_TARGET`. Model calls were zero, and the exact output is in the packaged launch artifact.

**Stop here.** This is a confirmed product-value gap for the exact public breaking-API task, not MCP transport failure or insufficient prompting. Do not launch bare A alone or invent B success from installed-wheel/stdin checks. A separate scoped product change must produce `PROPOSED chalk@5.3.0` with immutable is-number and reliable evidence; only then re-check the same gate (not a staged registry) and request **one-pair** authorization.

## One-command NO-AGENT preflight

From the exact P3 review branch with Python 3.12, Node 22/npm, public network to npm and OSV:

```bash
python -m pip install -e . build setuptools wheel
python -m build --no-isolation
python scripts/correctness_preflight.py breaking-demo --wheel dist/deploom_mcp-0.2.0-py3-none-any.whl --snapshot-dir ./d1-frozen-chalk --report ./d1-p2-control.json
python scripts/d1_mcp_value_preflight.py --frozen ./d1-frozen-chalk --wheel dist/deploom_mcp-0.2.0-py3-none-any.whl --report ./d1-value-gate.json
python scripts/d1_pilot.py dry-run --out ./d1-fake-output
python scripts/d1_pilot.py doctor
```

No Codex agent call in any command above. The fixture `d1-frozen-chalk` must reproduce source SHA256 `4adfa455d02e9f354e5b47e7b4d983f6676b620b13dc1454bda9248272d97cb6`; otherwise preflight is **NO_GO**. GitHub workflow `.github/workflows/d1-pilot-preflight.yml` runs these and retains **public** frozen input, sanitized receipts, and clearly marked fake parser events in one artifact. Note: CI PASS means preflight code ran; check the **independent product gate** inside `d1-mcp-value.json`.

## Codex CLI schema (verified against official docs, not an account session)

Use Codex CLI `exec --json`, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, explicit `--model`, `model_reasoning_effort`, `--sandbox workspace-write`, and only B has an enabled and **required** `mcp_servers.deploom` stdio config pointing to the exact wheel installed outside the workspace. The initial prompt is **byte identical** A/B and includes the public Chalk migration URL. Codex supports JSONL events `thread.started`, `item.started`, `turn.completed`; native `usage.input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens` are used when present. Cached input is a subset of input and reasoning output is a subset of output: **no double counting**. Missing or unsupported counters are JSON `null` and cannot support an efficiency claim.

The P3 runner never saves provider JSONL/raw model messages, shell command text, secrets or authentication. It retains event-derived counts, durations, budget stops and independent source/verification outcomes. The `dry-run` command writes `FAKE_DRY_RUN_NO_MODEL_CALLS`, never a measured success.

**Current execution environment has no Codex CLI or configured account credentials exposed for a model run.** `codex debug models`/CLI model catalog can be checked *without inference*, but catalog presence alone is not proof that the account can invoke a model. No exact model is locked or claimed available until an authenticated runner confirms it. Model/reasoning must be identical across both arms. Local `CODEX_HOME` per arm must contain only independently scoped private auth (never upload auth.json), no Skill, no parent configuration.

## Future single-pair launch (NOT authorized by this document)

After product value gate `GO`, native CLI/version/account verification, explicit cost-plan acceptance and a **separate user authorization**:

```bash
python scripts/d1_pilot.py run --approve-one-paid-pair --confirm-model-access \
  --seed ./d1-frozen-chalk --wheel dist/deploom_mcp-0.2.0-py3-none-any.whl \
  --mcp-preflight ./d1-value-gate.json --out ./d1-pair \
  --codex-auth-home /PRIVATE/PATH/TO/CODEX_HOME \
  --model EXACT_VERIFIED_MODEL_ID --effort medium \
  --manual-a 0 --manual-b 0 --wall 600 --tools 40 \
  --input-cap 20000 --output-cap 4000
```

The flags `--approve-one-paid-pair` and `--confirm-model-access` are **explicit local operator attestations**, not proof of subscription quota, provider entitlement or a hard dollar limit. When using API billing with independently verified rates, optional `--input-rate`, `--cached-rate`, `--output-rate` (USD/M) and `--max-api-estimate` enable only a **soft observed** API-equivalent USD stop after a completed turn; no hard dollar guarantee is available. They must not be set now. Change `--manual-a/b` from zero if human help actually occurred; unknown help must not be silently recorded as zero. The launcher stops **before starting A** if frozen SHA/wheel/model access/real Chalk MCP value gate fails.

**Hard-ish safeguards:** 600-second watchdog per Codex process, 40 started tool calls, one task turn; signals only the process spawned in a new process group. A and B use **separate secret-stripped environments** (ambient API keys/npm auth removed), fresh private `HOME`, and separate `CODEX_HOME` auth stores. These local protections are not a substitute for separate OS users or trusted throwaway runners. The receipt measures the MCP wheel setup separately and compares total task wall including that setup. B cannot claim an MCP benefit if its native tool-call events show zero MCP calls. On Windows, descendant cleanup is not guaranteed by `CTRL_BREAK_EVENT`; use independent throwaway runner isolation. **Soft ceilings only:** 20,000 input and 4,000 output tokens per arm. Codex reports aggregate usage at `turn.completed`, so spending can overshoot before the driver sees it; the receipt flags `tokenOvershoot`, never promises a hard cap. Billing unavailable with ChatGPT-managed subscription; optional rates estimate API-equivalent cost only, excluding cache writes/tool fees and never a guaranteed spend limit. Missing usage metrics and unknown waiting time remain unknown, not zero.

At standard OpenAI **GPT-6.1 Sol API** rates (if that exact model is *later confirmed available*): $2/M uncached input, $0.10/M cached input, $10/M output. A purely illustrative *uncached* 20K input + 4K output is $0.08/arm, $0.16/pair, **not** an upper bound or Codex subscription billing quote. Model entitlement, current CLI access, actual pricing tier, cache-write charges and rate limits must be verified by the operator before any paid run.

## Isolation, outcomes, and stopping criteria

- Exactly two **fresh** Codex sessions, new empty workspaces seeded from the same frozen SHA, distinct `CODEX_HOME` and npm cache. Run sequentially A then B; report order and no randomization. No Skill loaded in either condition. Only the required MCP stdio server differs.
- Both have `workspace-write`, the same public-network grant (not full-access), same model/reasoning, exact user task and editable scope; docs at `https://github.com/chalk/chalk/releases/tag/v5.0.0`. The offline frozen manifest/lock is identical; npm/OSV **live responses can drift** between sequential runs, so cache/network comparability is **best effort, not frozen equivalence**. Collect public network evidence when available and do not claim causality if it changed.
- MCP server's wheel installation and any common preparation are measured separately; B server startup/tool-use remain inside Codex task wall time. Report any extra MCP setup overhead separately, not hidden.
- Score **after each arm** using P2 independent `verify_chalk_frozen` on a completely fresh verification copy: exact chalk@5.3.0, frozen scripts, is-number@7.0.0 unchanged, clean npm ci/build/test and runtime for multiple names. An agent's JSON/text `PASSED` statement has no evidentiary authority.
- Stop on no-go value gate, invalid frozen SHA/wheel, unavailable/incorrect model, missing auth, unequal permissions/task scope, unsafe source edits, unreachable public registry, missing native usage counters required for token claims, 600s wall watchdog, 40 calls, or explicit operator cancel. One A/B pair cannot establish statistical advantage.
- Outcome is symmetric: when both correct, shorter end-to-end wall time may be labelled `B_FASTER_PILOT_ONLY` **or** `B_SLOWER_PILOT_ONLY`; an incorrect B cannot win on speed. Do not compare against historical A.
- If planning is non-actionable, report the product gap and **stop**. Next engineering step is a scoped API/intent feasibility issue, not another expensive benchmark.

## DepLoom handoff (for a fresh project chat)

- Core #4 HEAD `49232ab510da8fa854f36d84a639f3db28ab363c` ([CI](https://github.com/DepLoom/core/actions/runs/40905412766)).
- MCP #6 HEAD `2c6529449e6f7479dcb9b2cfd5bc2849776bdd1d`; #7 HEAD `292f1c7fea4be8f5b986764f748c2535d55e40d2`; canonical P1 #9 HEAD `ca0c90626613dc9615cf3b99085c4e7bb2993dd8`, all open/unmerged.
- P2 #10 exact HEAD `72e9899932fd5e56280c983d33353fb4de27b8ff`, [final CI 4/4](https://github.com/DepLoom/mcp/actions/runs/40923486267), [Chalk Linux/Windows oracle](https://github.com/DepLoom/mcp/actions/runs/40923486100) both PASS, 305 tests in each CI job, skill SHA256 `cdfd3be94cd0eba58125f2d5e2094807dbdca022cd29ff35160573d6091762a1`.
- P2 public frozen Linux source SHA `4adfa455d02e9f354e5b47e7b4d983f6676b620b13dc1454bda9248272d97cb6`; original evidence artifact in [P2 run](https://github.com/DepLoom/mcp/actions/runs/40923486100). Exact package versions chalk@4.1.2 → 5.3.0, is-number@7.0.0 untouched. P2's evaluator-only reference patch is NOT agent evidence.
- This P3 branch is stacked on **#10 only**. See its `d1-mcp-value.json` actual go/no-go. **No A/B session was run.**
- Re-verify all SHAs/CI when resuming; never merge/release without separate approval. The sole next decision: authorize exactly one paid A/B pair **only if** actual MCP value gate GO and account cost/usage enforcement are acceptable; otherwise fix exact Chalk actionability product gap first.
