# DepLoom P1 — exact-pin integration and deterministic correctness preflight

**Scope:** reviewable preparation. Do not merge, release, publish, run paid agents or
change `main` in this stage. This extends—not replaces—[EXPERIMENT_HANDOFF.md](EXPERIMENT_HANDOFF.md)
and Core's [AB_NO_SKILL_PROTOCOL.md](https://github.com/DepLoom/core/blob/main/docs/AB_NO_SKILL_PROTOCOL.md).

## Verified ancestor graph before integration

Verified by GitHub's compare API at review time (2026-10-29):

| Comparison | Status | Behind / ahead | Exact merge base |
|---|---|---:|---|
| Core `main` → #4 | ahead | 0 / 15 | `14cae77162478349a8de69db680ab656404daace` |
| MCP `main` → #6 | ahead | 0 / 9 | `71a6bc1da0c14a20c89147db24aaa4f87aeb3528` |
| MCP #6 → stacked #7 | ahead | 0 / 13 | `2c6529449e6f7479dcb9b2cfd5bc2849776bdd1d` |

Frozen PR heads at inspection: Core #4 `49232ab510da8fa854f36d84a639f3db28ab363c`,
MCP #6 `2c6529449e6f7479dcb9b2cfd5bc2849776bdd1d`,
MCP #7 `292f1c7fea4be8f5b986764f748c2535d55e40d2`.
All three were OPEN with successful head CI. #7's base equals #6's HEAD.
**Do not rebase/retarget #7 while its base remains intact.** This P1 review branch
is stacked *on #7* and must be integrated separately, not injected into #6.

### Merge checklist (future work; no merge authorized)

1. Re-fetch all branch/main SHAs and compare graph; confirm the three original
   PRs are still open/green and `main` has not moved. Freeze the intended
   *post-merge* Core commit SHA, **not a branch ref**.
2. Core #4: choose a strategy **before merging**:
   - **merge commit / fast-forward preserving original PR HEAD:** original Core
     `49232ab...` remains an ancestor of merged `main`. #7's gitlink may
     remain exactly `49232ab...`, provided it is still fetchable, the 134-file
     inventory matches and the release process explicitly accepts a submodule
     pointer to that ancestor rather than top-of-main.
   - **squash / rebase creating new Core commit(s):** resulting Core `main`
     commit **does not equal** the prior #4 head, even if the files are
     byte-identical. Never silently claim #7 is pinned to merged `main`.
     Choose explicitly either (a) retain the review SHA as a reachable immutable
     pin with independently attested reachability/content, or preferably
     (b) repin #7 to the exact resulting Core merge/squash commit and regenerate
     `core.revision` and `core.contentSha256`/file inventory. If the file
     bytes differ, review the diffs. Re-run installed-wheel/OSV/stdio gates.
3. MCP #6: if merged with **squash**, current #7 ancestry will not contain the
   new squashed commit. After parent integration, create an explicit post-merge
   branch/rebase (preserving #7-only changes) or reconstruct a fresh child diff
   of the seven #7 paths. Inspect `git diff` against new MCP main and ensure
   none of #6's schema/projection changes disappear. If #6 is merged with
   original commits preserved, ancestry may remain valid but confirm it.
4. Confirm `git ls-tree HEAD upstream/deploom` reports mode `160000` and
   the intended Core SHA. Check `git -C upstream/deploom rev-parse HEAD`,
   `release-manifest.json.core.revision`, 134 SHA-256 resource hashes, schema,
   Skill bundle and installed wheel. Do not trust the Core version string:
   both reviewed pins identify `1.0.0-preview.20`.
5. Check `decisionDigest`, semantic input identity, target/security evidence,
   backward-compatible legacy Core and JSON Schema against the new ancestry.
   Review any different expected behavior before accepting it.
6. After **each effective code/pin change**, run targeted tests first; only the
   final changed HEAD gets the full Windows/Linux × Python 3.12/3.13 CI,
   wheel/sdist, Skill, native-audit and real stdio/OSV gates. No repeat matrix
   on unchanged trees merely to collect another green badge.
7. The P1 harness PR is optional and must remain isolated from #7 until reviewed.
   An explicit later user approval is required for **every merge** and release.
   
Useful commands (in a *clean local clone with the same remote refs*):

```sh
git fetch origin main feat/registry-diagnostics-projection-20261029 feat/repin-core-registry-diagnostics-20261029
git merge-base --is-ancestor 2c6529449e6f7479dcb9b2cfd5bc2849776bdd1d 292f1c7fea4be8f5b986764f748c2535d55e40d2
git diff --stat 2c6529449e6f7479dcb9b2cfd5bc2849776bdd1d..292f1c7fea4be8f5b986764f748c2535d55e40d2
git ls-tree 292f1c7fea4be8f5b986764f748c2535d55e40d2 upstream/deploom
python scripts/update_release_manifest.py --check
python scripts/check_skill.py
```

## Existing assets reused; false-positive gap closed

- `scripts/e2e_registry.py` provides a deterministic 127.0.0.1 npm registry with
  actual SHA-512 tarballs; `scripts/e2e_seed.json` provides the versions.
- `scripts/e2e_fixture.py` creates the real npm manifest/lock and app JS
  build/test. These files are **not** replaced.
- `scripts/e2e_pilot.py` previously compared an `unchanged` subset of the
  *current* original checkout against its *own current* dependency map. That is
  not a baseline comparison and could let edits masquerade as unchanged.
  Its check now uses the canonical fixture manifest and the independent bytes
  of original build/test/source scripts.
- `scripts/validate_evidence.py` validates claimed evidence structure but
  explicitly **does not** independently run npm, build or runtime. Reuse it as
  an evidence validator only; this oracle is the missing independent replay.
- Stage 3 OpenCode historical pilot is not a control for the current Skill
  2.0 revision. No paid trial is included here.

## No-agent correctness harness

`scripts/correctness_oracle.py` is an independent, fail-closed evaluator for
**this fixture only**. It does not read the agent's self-reported success or
infer safety from the MCP's `PROPOSED` status. A separate baseline with
the original `package.json` and `package-lock.json` must be provided.
Only two files may change: `package.json` and `package-lock.json`. The
oracle checks immutable source/test/build/`.npmrc` hashes, explicit dependency
scope/exclusion and real package-lock resolved URL/integrity consistency.
It stages a *separate copy*, runs `npm ci --ignore-scripts`,
`npm ls --all --json`, the frozen `npm run build` and `npm test`,
then a **separate Node runtime oracle** that imports installed packages,
original app source and the build artifact and verifies exact versions.
It checks no non-ephemeral source was modified during replay. Failures are
categorised and **never** promoted to PASS.

Known fixture expectations, not model output: a `1.2.0 → 1.9.0`,
b `2.0.0 → 2.1.0`, c **remains** `3.0.0`. A candidate that only edits
`verify.js` to print a success marker, upgrades c to `4.0.0`, leaves
a stale lock, invents an extra source file, forges version strings or changes
a lockfile tarball to a foreign host is rejected. This is deliberately a small
**deterministic sanity fixture**, not realistic React/Vue source migration.

`scripts/correctness_preflight.py selftest` reuses those assets and adds
**real packaged-wheel MCP stdio** (official SDK, real pinned Core) for
initialize/list_tools, invalid-registry rejection, output-inside-project
rejection, valid empty-project planning and source unchanged. The planning
result is explicitly `PLANNING_ONLY / NOT_VERIFIED / UNKNOWN`. The harness
does **not** ask MCP to edit source. The oracle only evaluates a separate
deterministic positive-control change written by the fixture setup, not by a
model. All registry traffic in install stages stays local to 127.0.0.1.
No real npm/OSV result should be invented if the environment is offline.

```sh
# Engineering preflight: zero model tokens. Use a clean checkout of the
# P1 harness branch, Node >=22, npm, Python 3.12+, project dependencies.
python -m pip install -e .
python -m pip install build setuptools wheel
python -m build --no-isolation
python -m unittest discover -s tests -p test_correctness_oracle.py -v
python scripts/correctness_preflight.py selftest \
  --wheel dist/deploom_mcp-0.2.0-py3-none-any.whl \
  --report /tmp/deploom-p1-correctness.json
# Report status PASS is required; inspect its scoped evidence.
```

For *later* independently scored agent work, build a canonical baseline using
the existing `e2e_fixture.py make` under the existing local seed registry.
Keep the baseline and its receipt **outside** each agent's accessible slot.
After the agent session, stop the registry; the verifier recreates the exact
loopback seed on the **same bound port** referenced by the frozen baseline
lockfile. The port must be available; a conflict is an infrastructure failure,
not an agent failure.

```sh
python scripts/correctness_preflight.py verify \
  --baseline /abs/evaluator/baseline \
  --candidate /abs/isolated/agent-final-worktree \
  --report /abs/evaluator/results/independent-oracle.json
```

The verifier's `--report` must be outside both source directories. Do not
commit or publish raw experiment logs, registry URLs, secrets or private
projects. Retain private raw transcripts only in operator-controlled evidence
storage. The CI synthetic `correctness.json` has outcome categories, version
intent, hashes and command exit codes, **no URLs or raw logs**.

## Ready-to-run benchmark study protocol — *not executed*

### Claim and contrasts

- **A: bare agent** — same model/client and npm/Node project tools, but no
  DepLoom MCP or Skill. Independent access to the same local fixture registry.
- **B: agent + MCP** — same as A plus *only* DepLoom read-only planning tools.
  No DepLoom Skill.
- **C: agent + MCP + Skill** — identical MCP runtime, tool schema, user goal,
  and budgets as B; only the actually loaded Skill changes. If loaded-content
  fingerprints cannot be observed, record `SKILL_TREATMENT_UNVERIFIED`
  rather than attributing an improvement.

A↔B tests the benefit of *planner access* including its tool-call/response cost.
B↔C tests Skill guidance, not MCP itself. This is not an authority bypass:
the agent remains responsible for changes and the same permission boundaries
apply to all arms. The tools are read-only and produce planning evidence,
not a completed migration. No variant receives another variant's answers.

### Freeze and preflight

Before any tokens are spent, freeze and record:

- Repository Core/MCP/Skill SHAs, wheel SHA-256, actual bundled Core revision,
  `schemaSha256`, Skill files and loaded-client content; one exact
  model provider + immutable model version/snapshot, reasoning effort,
  client/CLI version, temperature/settings, tool grants and sandbox policy.
- A single synthetically seeded task: upgrade a,b to their latest published
  same-major versions while **never upgrading excluded c**; preserve original
  source and test/build scripts; deliver a final lockfile and evidence. Do
  **not** show the hidden correct version assignment to any agent. Both
  allowed and forbidden package managers/tools must be explicit.
- Identical baseline bytes. Run each arm **sequentially** on fresh clones with
  separate user home, git/worktree, npm cache, MCP state/cache, Skill scope,
  transcripts, output directories and token/session IDs. Use the **same local
  port and immutable seed** after shutting down the previous registry; do not
  preload another arm's registry/OSV data or Core blueprint.
- Preflight the **same** Node/npm/Python runtime, clean install/build/test,
  independent oracle and packaged MCP stdio. Cache strategy is COLD for each
  arm, not inherited warm state. If the same frozen registry/OSV responses
  cannot be served with comparable latency, report `NETWORK_NOT_COMPARABLE`.
- Confirm A and B have identical common evidence-access tools and permission
  budgets; A does not see MCP tools. C's Skill is verified as *loaded in that
  session*, not merely installed. If any parity or policy gate fails,
  **STOP without running agents**.

### Three-arm pilot, budget and metering

This is a **pilot of one A/B/C set**, not statistical evidence. Two independent
A/B runs, if later authorized, are *two pilots*, never a significance claim.
Historical A logs are ineligible as controls. For a proper repeat, use at least
3–5 independently randomized/balanced runs per arm *after* resolving correctness.

Conservative pilot ceilings **per arm** (hard-stop only if monitorable):
- Up to **25,000 unique billed input tokens**, **8,000 billed output tokens**,
  **45 tool calls**, **15 minutes wall clock**; **zero retries** and zero
  manual code fixes by operator. Distinguish cache-read tokens as an input
  subset and reasoning tokens as an output subset; do not add them twice.
- 3 arms = no more than **75,000 input + 24,000 output tokens**, **135 tool
  calls** and **45 minutes sequential wall time** excluding setup/review.
  The actual client quota, billing, rate limits, cache fee and model-specific
  tokens must be checked *before launch*. If the CLI/provider cannot enforce
  the agreed hard budget, use a supervised abort mechanism; otherwise STOP.
- **Cost preflight, not a pricing assertion:** with exact provider prices
  `P_in`, `P_cached`, `P_out` (USD / million tokens), per-arm upper
  estimate `[(25,000-cache)*P_in + cache*P_cached + 8,000*P_out]/1e6 +
  external tool fees`. For all three arms, apply the same formula with each
  arm's cache separately. Do not quote a made-up dollar price for an
  unspecified model. Require explicit budget approval and available quota;
  no credit card or API session is used in this P1 stage.

For every arm record: immutable baseline and final commit/content hashes,
model ID/snapshot, request/session IDs, delivered tool/schema/Skill hashes,
exact wall start/stop, provider-reported actual input/output/cache/reasoning
tokens and billable cost if present (or `UNKNOWN`), tool calls by type and
wall spans, manual help/retry/permission waits, input/network/cache receipts,
stop reason, independent install/build/test/runtime/policy results, source
mutations, and uncertainty. Deduplicate overlapping intervals and response
usage IDs. Score correctness **blind to arm identity/tokens**, then reveal
timings/usage. A faster but incorrect or unknown outcome is a FAILURE, not a
speedup.

Stop immediately on a changed task/baseline, disallowed package or source edit,
unexpected external host/contact, unapproved tool authority, lost client
isolation, missing real usage metrics, runaway token/tool/wall budget,
leaked Skill treatment, unverified installed MCP/Core pin, network mismatches,
or failed independent correctness. Preserve failure evidence as FAIL/UNKNOWN.

### Scope boundaries and limitations

This P1 fixture establishes that a *bounded independent evaluation process*
can identify a correct simple lockfile upgrade and reject obvious cheating.
It does **not** prove an autonomous agent can do it, that MCP improves
performance/correctness, that a Skill contributes, or that real app
migrations (Router/Storybook/React/Vue) pass behavioral/peer/security gates.
A second, public realistic fixture with actual source adaptations and an
independent runtime oracle would be required before any presentation claim
about migration correctness or speedup.
