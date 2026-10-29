# P1 — Integration preflight and independent correctness / benchmark preflight

**State:** reviewable preparation only; not merged, released, published or executed by an agent. Existing [P2.6 evidence](https://github.com/DepLoom/mcp/pull/7#issuecomment-6501618116) establishes diagnostic correctness and packaged stdio, not migration correctness or agent speedup.

## 1. Exact merge and ancestry checklist

Frozen checkpoint (2026-10-29):
- Core PR #4: `49232ab510da8fa854f36d84a639f3db28ab363c`; main `14cae77162478349a8de69db680ab656404daace`; compare status `ahead`, 15 ahead / 0 behind.
- MCP PR #6: `2c6529449e6f7479dcb9b2cfd5bc2849776bdd1d`; main `71a6bc1da0c14a20c89147db24aaa4f87aeb3528`; 9 ahead / 0 behind.
- MCP PR #7: `292f1c7fea4be8f5b986764f748c2535d55e40d2`; **base is #6 branch at its exact HEAD**, 13 ahead / 0 behind. Its reviewed diff has only repin/inventory/smoke/CI/docs/tests plus the gitlink.
- #7 tree `upstream/deploom` mode `160000` points at **exact Core #4 SHA** and equals `release-manifest.json.core.revision`. Inventory contains 134 files. All listed P2.6 HEAD workflows were PASS.

**No need to rebase/retarget #7 before #6 merges.** After separate user authorization to merge, recheck live refs, approvals, permissions, freeze them and proceed sequentially:

1. **Core #4**: verify current main and HEAD, `git merge-base --is-ancestor origin/main <core-4-head>`, full reviewed diff and latest CI. Choose *merge commit* vs *squash* explicitly; do not assume they are equivalent. Record actual Core main commit C.
2. **MCP #6**: verify `git merge-base --is-ancestor origin/main <mcp-6-head>`, schema/Skill/release manifest identity, applied statuses and full tests. Merge only after separate approval and Core merge. Record actual MCP main commit M.
3. **MCP #7**: only now refresh base and ancestry against M. A merge commit for #6 retains the reviewed #6 HEAD as ancestor; a squash for #6 does **not**. For squash, recreate #7-only delta on M by reviewed cherry-pick/rebase (not a blind force-update), then compare the resulting PR diff with the original seven-file delta. Stop on extra files, missing source history, or conflicts. 
4. **Core gitlink identity**: a Core *merge commit* retaining #4 as an ancestor can preserve the exact reviewed `49232ab...` gitlink; Core *squash* does not bring that SHA into Core main ancestry. Prefer repinning to the **actual Core main commit C** after checking all 134 source bytes match the reviewed HEAD; update `core.revision`, inventory digest, installed wheel assertions and release audit. Never point a gitlink at an orphaned PR-only commit and treat a matching tree as a matching provenance. For non-byte-identical squash C, stop and review as new code.
5. On the resulting **new** #7 HEAD, run release inventory check, Skill integrity, JSON Schema, Windows/Linux Python 3.12/3.13, wheel/sdist outside checkout, real stdio, native audit, OSV proxy. Keep decision/semantic-security identities and planning-only guarantees. Capture SHA/CI/artifact receipts. No merge unless separately approved.

No unrequested main write, merge, release, package publication, or agent A/B/C execution is part of this P1 preflight.

## 2. Existing DepLoom experiment artifacts and concrete gap

Reused, rather than replaced:
- `scripts/e2e_registry.py` + `e2e_seed.json` — three synthetic packages served from localhost with real tarball integrity metadata.
- `scripts/e2e_fixture.py` — npm install, lockfile, minimal app's `npm test` and `npm run build` with excluded package invariant.
- `scripts/e2e_pilot.py` — **historical agent-run** driver. Its `verify_e2e` included a tautological original-manifest check and derived success targets from the planner, so it is **not an independent final migration oracle**.
- `docs/AB_NO_SKILL_PROTOCOL.md` in Core, `docs/EXPERIMENT_HANDOFF.md` in MCP and `docs/STAGE_03_UNIVERSAL_SKILL.md` — correct separation of planning and executed changes, token metrics, frozen inputs and loaded Skill identity. The historical OpenCode pilot is not a controlled A/B or current Skill v2 verification.

**Actual fix:** the local registry no longer synthesizes tarballs for unpublished versions (HTTP 404 instead). New `scripts/correctness_preflight.py` reuses the above seed/fixture, but operates independently of an LLM: 
- Calls the **installed non-editable wheel** through real official MCP stdio, with source/override paths absent and runtime provenance checked against the release manifest.
- Positively checks a known assignment in the plan, `PLANNING_ONLY` / `NOT_VERIFIED` / `UNKNOWN`, explicit exclusion, full JSON Schema and read-only input fingerprints.
- Applies a *deterministic fixture-only reference mutation* outside the original project (this is deliberately **NOT MCP-driven migration**): desired `a=1.9.0`, `b=2.1.0`, **excluded `c=3.0.0`**. Its independent oracle requires exact manifest and lockfile entries, allowed loopback resolved URLs, integrity fields, **fresh npm ci**, `npm run build`, `npm test`, `npm ls --all --json` and exact `dist/result.json` versions.
- Asserts **negative controls are rejected**, not PASS: unchanged baseline (ordinary build/test alone can pass), excluded `c@4.0.0`, tampered lockfile, and a deliberately broken runtime source. A second local registry serves an upgrade with a **foreign tarball origin**; the packaged planner must **not select the foreign artifact** and must expose structural `FOREIGN_TARBALL_ORIGIN` evidence; `NO_INSTALLABLE_ARTIFACT` is required only if the unchanged planner status is `NO_TARGET`. A policy-satisfied current version may remain policy-satisfied. The input must not be modified.
- Uses isolated temporary workspaces and disposable per-run npm caches, no credentials, no corporate project, no external npm registry, no LLM. Only status codes, SHA digests and check names enter the public CI receipt; raw URLs and logs are not uploaded.

Use the focused local unit suite:
```bash
python -m unittest discover -s tests -p test_correctness_preflight.py -v
```

Use the optional [correctness-preflight workflow](../.github/workflows/correctness-preflight.yml) for a real packaged-wheel run on Ubuntu + Node 22 + Python 3.12. It installs the wheel into a temporary isolated --target directory outside checkout, explicitly prepended for a -I runtime; tests create only localhost inputs. A workflow failure is **FAIL**, not a claim that the harness ran successfully. The receipt records `agentMigrationCorrectness=NOT_TESTED` even if every deterministic gate passes.

**Limitations:** the three tiny packages do not have real breaking API adaptations or cross-cohort peer conflicts; a green result is *smoke/reliability evidence*, not realistic migration quality. The known target values are fixed fixture oracle values, not evidence of a globally optimal dependency assignment. No installed-tree security audit against a live authority is asserted by this offline fixture. Corporate Nexus and proprietary application behavior remain untested.

## 3. Cheap benchmark protocol — ready, NOT executed

Objective: measure **incremental tool/planning value**, not merely availability. Recommended future fixture: public/synthetic single-manifest app with 12–16 dependencies, interacting peer constraints and observable API-changing runtime behavior as proposed in Core `docs/AB_NO_SKILL_PROTOCOL.md`. Baseline must pass clean install, build, test, lint/typecheck (where declared) and targeted runtime before agents see it. A version-only edit must fail a behavioral oracle. Freeze tarballs, registry/OSV metadata and exact lockfile **before** participant starts.

Arms:
- **A / bare agent:** same coding agent/model/effort and allowed shell/reading tools, *no DepLoom MCP or Skill*. Equal human task and independent access to **the same frozen package/security evidence**.
- **B / agent + MCP, no Skill:** same tools/authority, plus packaged `get_upgrade_plan` only. The planner is read-only; compare the **agent's applied changes**, not the planner's proposed versions as if installed.
- **C / agent + MCP + Skill:** exactly B's MCP build and grants, plus **verified Skill v2 loaded in this session**. C–B estimates bundled guidance value, not a single Skill rule, unless the only changed variable is that rule.

For each arm, freeze explicit immutable model identifier and provider release, reasoning effort, client/runtime/Node/npm/Python versions, context and system prompt, prompt text, acceptance policy and allowed scope. Use separate fresh sessions, HOME, worktrees, registry/OSV caches, Skill catalogs, tool configuration and evidence directories. A must not advertise unavailable MCP tools; C must prove loaded Skill content/hash, not just installed bytes. No transcript, answer, source diff or local cache from another arm is visible. Randomize/balance sequence in later repetitions; a **single matched A/B pair or A/B/C triple is only a pilot**, not a statistical improvement claim. Historical A runs are **not controls**.

Prepare one immutable seed/lock set; enforce equally cold (fresh caches) or equally frozen warm snapshots across conditions. Record all network requests and cache hits as separate columns. If live registry/OSV evidence is necessary, capture/replay permitted public snapshots consistently; network denial or ambiguous unknown is not `KNOWN_CLEAN`. Keep the original project immutable; score the final agent-edited artifact in a fresh, blind isolated copy with the **same independent** install, build/test, targeted runtime, peer/npm ls, policy/severity/unknown oracle and source fingerprint. An agent-written `PASSED` JSON alone is not an oracle.

Per session retain non-secret fields:
- run/arm/slot, model **exact version and effort**, client and tool versions, commit SHA for Core/MCP/Skill/fixture, prompt and wheel SHA-256, invocation config, scope and operator actions;
- UTC start/end and **wall seconds** (include startup/waits), separate verified infrastructure/approval delays, real tool-call count by category, attempts, failed commands and manual help events;
- **provider-native** input, cached-input, output and reasoning tokens where exposed (use unique provider response IDs; never sum overlapping counters), pricing source/version and exact costs; absent data = `UNAVAILABLE`, not estimated tokens;
- full final independent oracle checks (pass/fail/unknown) and proof hashes, no inferred speedup.

**Proposed pilot stop limits (not a promise of billing enforcement):** one trial per arm; max 20 minutes wall, 30,000 total input tokens and 6,000 output tokens per arm, 50 tool calls, 3 bounded repair attempts per root cause, **$2 verified cost per arm / $6 aggregate**. Require operator-side/token API enforcement and evidence of current account/model rates before enabling any paid agent. Formula: `cost = (uncached_input*USD_per_million_input + cached_input*USD_per_million_cached_input + output*USD_per_million_output)/1e6`; do not double-charge reasoning if it is included in output. Prices, actual token availability and quotas **remain unverified** until a model/provider is explicitly frozen. If no reliable usage/cost counter, stop rather than invent. Stop also on missing baseline oracle, unknown required security, wrong registry/authority, unauthorized writes, inability to verify loaded Skill, differing task scope, crossed session cache or any quality-critical behavior failure. Do not run B/C when preflight shows packaged MCP is defective.

After a diagnostic pilot, repeat 3–5 times per arm under counterbalanced order **only after separate approval and cost estimate**. Report distributions/uncertainty; no causal speedup inference from two runs, and never compare with the historical A as a concurrent control.

## 4. Readiness claims

- An installed-wheel/stdin/diagnostic PASS proves protocol integration and planning, not migration, compatibility in real projects or token savings.
- Passing this preflight proves one known tiny fixture's independent reference installation/build/test gates and negative-control sensitivity; it does not prove an autonomous coding agent can perform edits.
- C versus B separates **bundled Skill** only if the actually loaded Skill is observed and no other tool/config/authority changes.
- User authorization still required for all merges, releases, publications, paid-agent sessions and any expansion of existing tool authority.
