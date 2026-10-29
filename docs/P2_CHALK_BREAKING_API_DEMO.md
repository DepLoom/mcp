# P2 — real public breaking-API fixture: Chalk 4.1.2 → 5.3.0

**Review-only, no agent run, no merge or release.** This extends canonical P1 PR #9; no second harness or competing branch. An independent evaluator (not the agent) makes the known-good reference patch and verifies it. It is not a DepLoom MCP migration.

## Why this public case

Chalk's [official v5 release notes](https://github.com/chalk/chalk/releases/tag/v5.0.0) identify pure ESM and the removal of .keyword(). This case exercises the latter rather than relying on require() failing: newer Node 22 runtimes can require synchronous ESM in some cases. The public fixture starts with chalk@4.1.2 using .keyword("orange"), moves to chalk@5.3.0, and must preserve the exact user-visible string `READY Ada`. is-number@7.0.0 remains explicitly excluded from change. No corporate source or private package is involved.

An install-only check would falsely declare success when Chalk 5 is present but `src/format.cjs` still calls the removed method. This case demands an actual source adaptation, unlike the older P1 three-version smoke.

## Single demo command

From **the exact reviewed PR checkout** with Python 3.12/3.13, Node 22 and npm, after installing project dependencies and building the wheel:

```bash
python -m pip install -e . build setuptools wheel
python -m build --no-isolation
python scripts/correctness_preflight.py breaking-demo --wheel dist/deploom_mcp-0.2.0-py3-none-any.whl --report ./p2-chalk-receipt.json --snapshot-dir ./p2-chalk-frozen-baseline
```

A later agent output is scored **separately, without any agent access to the oracle**, using the recorded baselineSourceSha256 from this receipt and the public frozen snapshot:

```bash
python scripts/correctness_preflight.py breaking-verify --baseline ./p2-chalk-frozen-baseline --candidate ./isolated-agent-output --expected-baseline-sha256 <receipt.provenance.baselineSourceSha256> --report ./p2-independent-verdict.json
```

This second command fails closed on any baseline SHA drift **before npm**, and only the independent evaluator may execute it. The future agent gets only the snapshot and task; it is not given this verifier, source gold patch, or any results from other slots.

The command spends **zero agent/model tokens**. It requires the public npm registry at setup and during independent fresh `npm ci`; it does not simulate external network availability. The resulting receipt has an overall PASS only if **all** controls and actual packaged stdio PASS. On a required network failure it must fail, never fabricate proof.

On GitHub Actions, run the existing `correctness-preflight.yml` new `public-breaking-api` job (Node 22, Windows/Linux). The independent oracle and tests are in the *harness checkout*, **not** in the unsolved baseline copied by `--snapshot-dir`. That snapshot contains only the starting project (source, read-only build/test scripts, manifest, actual resolved lockfile). **Never deliver the control/reference patch or the evaluator source to an experimental agent.** The evaluator is public repository code, so a future experiment must prohibit tools that read the evaluator/repository source beyond the isolated task slot; an unrestricted web agent could otherwise discover the gold patch.

## Independent controls — exact expected outcomes

| Control | Real install | Build/test/runtime | Oracle verdict |
|---|---|---|---|
| Baseline Chalk 4.1.2 + is-number 7.0.0 | PASS | PASS / PASS / PASS | PASS |
| Version-only Chalk 5.3.0, unchanged `src/format.cjs` | PASS | FAIL / FAIL / FAIL (removed .keyword method) | FAIL |
| Reference Chalk 5.3.0 + evaluator's source adaptation | PASS | PASS / PASS / PASS | PASS |
| Hardcoded `READY Ada` adapter (does not use the input) | PASS | Build/test PASS, independent runtime FAIL | FAIL |
| Wrong lock origin/integrity; excluded is-number changed; deleted tests; forged build command; fake-evidence file | **NOT RUN** | **NOT RUN** | FAIL before any npm |

The oracle checks the *real installed modules and produced build artifact*, not output markers alone. It independently executes `node -e` with an external assertion program, verifies **three different input names** (`Ada`, `Lin`, `Ada & Lin`) and excluded `is-number` functionality, and requires unchanged source/build/test scripts and no extra files, except the one approved source adaptation. A hardcoded output that fools the original one-input build/unit tests must be rejected by this separate runtime check. It runs `npm ci`/build/test/runtime on a separate temporary copy with a clean npm user/global configuration and a fresh cache, regardless of whether an agent previously claimed `PASSED`. Its reference patch is hardcoded in the evaluator for deterministic positive-control purposes **only**.

The existing bundled MCP is separately exercised via genuine installed-wheel stdio using the official client; this is a planning-only protocol check on a disposable empty project. **It does not prove that MCP proposes Chalk 5, applies edits, or successfully migrates this fixture.** Do not combine the independent code-patch PASS with MCP to claim automated migration.

## Immutable provenance and comparability

The receipt records exact installed-wheel SHA256, Core revision/content SHA256 from the release inventory, Skill bundle and contract SHA256, public package versions, baseline and target lockfile SHA256, baseline source digest, Node/npm/Python versions and `setupWallSeconds`. Registry authority is the public npmjs registry and actual locked SHA512 tarball integrity; all public network/package metadata may change before bootstrap, so **the generated baseline snapshot and recorded lockfile are the frozen input for a later experiment**, not a claim that re-resolving a public package is byte-stable in perpetuity.

The standard workflow uploads only the public unsolved snapshot and sanitized machine-readable receipt; it uploads neither raw CLI logs, private URLs, credentials nor the reference patch. Network setup overhead is recorded independently of oracle replay wall-time; per-arm model time must exclude shared preflight, and overall wall time must disclose waits and installation.

If the public registry is unreachable, the receipt status is **FAIL / infrastructure unknown**, not a simulated offline success; the previous synthetic local npm fixture remains the completely offline P1 control.

## One next pilot (requires separate authorization)

**Pilot D1-A/B, one matched pair only**: use the same exact reviewed baseline snapshot SHA from this receipt and a single pinned model/provider version plus reasoning effort. Independent fresh agent sessions with identical user objective, edit scope (`package.json`, `package-lock.json`, `src/format.cjs`), Node/npm, comparable frozen registry/cache, workspace and approval budget. A: bare agent, no DepLoom MCP or Skill. B: same agent + the exact packaged MCP wheel, no Skill. Both allowed the same evidence, and neither sees the gold patch/evaluator. Run only after a new preflight confirms both accesses and realistic per-session token/cost hard-stop controls. Record provider-native input/cached/output/reasoning tokens, wall time, tool calls, repairs, human help, exact output artifact SHA, and independent replay result. One pair is a pilot and **not a statistically meaningful speedup or correctness superiority claim**. For later C (MCP+Skill), keep everything from B fixed and verify actual loaded Skill SHA; it tests bundled guidance, not an isolated rule.

No paid agent sessions, publication, private data, merge or release are authorized by this document.
