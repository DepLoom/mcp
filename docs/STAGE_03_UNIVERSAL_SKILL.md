# STAGE 03 — Deploom-Upgrade universal Agent Skill

## Current contract and historical evidence

The remaining sections record the original 2026-10-24 Stage 3 delivery and pilot.
Their test counts, file count, client versions and E2E PASS refer to that revision,
not to the current Skill contract. The current canonical Skill is **2.0.0**;
`skills/deploom-upgrade/contract.json` binds SKILL.md, all references/examples and
the evidence validator by SHA-256. Its installer receipt identifies on-disk
content and explicitly leaves loaded-client content `NOT_OBSERVED`.

The new contract separates pinned/advisory authority, policy-minimal/latest
objectives, compact decision completeness, workspace reconciliation, final-input
installation evidence, targeted runtime checks and policy attainment. It uses
progressive references; ordinary planning does not require an execution report.
See [SKILL_CONTRACT_V2.md](SKILL_CONTRACT_V2.md) for verification and protocol.
The new offline tests check contract/delivery/evidence consistency only. No real
agent migration or client reload is claimed for version 2.0.0. The legacy E2E
harness has its original evidence assertions and is not a v2 acceptance gate.

## Historical Stage 3 record

Date: 2026-10-24. Repo: `DepLoom/mcp`, branch `stage3-universal-skill`
(merged to `main`).

## 1. Objective

Ship a single, client-neutral Agent Skill `deploom-upgrade` that lets an AI
coding agent plan and then safely *execute* JS/TS dependency upgrades on top of
the Stage 1/2 boundary (`get_upgrade_plan`), and prove it for real in a
disposable OpenCode E2E pilot. One canonical `SKILL.md` (no per-client forks),
portable install, honest status reporting.

## 2. Delivered

| Artifact | Path | Notes |
|---|---|---|
| Canonical skill | `skills/deploom-upgrade/` | `SKILL.md` + 5 references + 3 examples (9 files) |
| OpenCode adapter | `integrations/opencode/README.md` | verified against opencode 1.17.14 |
| Claude Code adapter | `integrations/claude-code/README.md` | **NOT_VERIFIED** (host CLI broken) |
| Codex adapter | `integrations/codex/README.md` | CLI verified; skill flow **NOT_VERIFIED** (see §6) |
| Installer | `scripts/install_skill.py` | stdlib, project/user scope, dry-run, idempotent, verify, no MCP writes |
| Skill tests | `tests/test_skill_contract.py`, `tests/test_skill_install.py` | 20 offline tests, 4-config CI |
| E2E harness | `scripts/e2e_registry.py`, `e2e_fixture.py`, `e2e_pilot.py`, `e2e_seed.json`, `e2e_prompt.md` | reproducible offline pilot |
| Stage doc | `docs/STAGE_03_UNIVERSAL_SKILL.md` | this file |

Installer smoke added to `.github/workflows/ci.yml` (runs on all 4 configs).

## 3. Design invariants (enforced by tests)

- **Client-neutral workflow.** The canonical `SKILL.md` + `references/` contain
  no client-specific command or config token (`test_skill_contract.py` fails on
  e.g. `opencode run`, `claude mcp add`, `codex mcp add`, `--auto`,
  `.mcp.json`). Client specifics live only in `integrations/<client>/`.
- **Exact tree.** `skills/deploom-upgrade` contains exactly
  `SKILL.md` + the 5 documented references + the 3 documented examples; no
  stray files; `SKILL.md` references resolve; frontmatter is a string-string
  metadata map; `name == folder == "deploom-upgrade"`.
- **Reproducible install.** `test_skill_install.py` runs the real installer:
  byte-identical copy, idempotent re-run, refusal to overwrite a foreign
  `SKILL.md` without `--force`, `--force` replacement, `--dry-run` writes
  nothing, user scope honors `--config-home` (simulated home) for all three
  clients, `--verify`, no MCP config written by default, `--print-mcp-config`
  writes nothing, `--target` override.

## 4. Honest compatibility matrix

| Client | Skill/mount | MCP config | E2E execute | Verdict |
|---|---|---|---|---|
| OpenCode 1.17.14 | `.opencode/skills` (project + user) | `opencode.json` `mcp.<name>` local stdio, `enabled:true` | **PASSED** (12/12) | **VERIFIED** |
| Claude Code | `.claude/skills` | `claude mcp add` / `.mcp.json` | not run | **NOT_VERIFIED** — host `claude` CLI is broken (`claude.ps1` cannot find `claude.exe`) |
| Codex 0.144.4 | `.agents/skills` | `codex mcp add` / `config.toml` | not run | **NOT_VERIFIED** — `codex exec` CLI + ChatGPT auth work, but the account rejects every code model (`model_not_found` for `gpt-6.1-sol`, `gpt-5-codex`, `gpt-4o`), so no real skill run possible on this host |

Per the honesty rules: `NOT_VERIFIED` is reported, never `PASSED`.

## 5. E2E pilot (OpenCode) — evidence

Environment: offline (registry.npmjs.org unreachable), so the pilot used a
local npm-compatible registry on `127.0.0.1:4873`
(`scripts/e2e_registry.py`) served to both the pinned Core
(`get_upgrade_plan registry=…`) and npm (`--registry=…` / `.npmrc`).

Fixture `fixture-app`: 3 direct deps pinned by the local registry —
`kontur-fixture-a 1.2.0→1.9.0`, `kontur-fixture-b 2.0.0→2.1.0`,
`kontur-fixture-c 3.0.0 (4.0.0 exists but excluded)`. `verify.js` makes the
exclusion invariant **executable**: it fails if `kontur-fixture-c` ever leaves
`3.0.0`, so the pilot proves non-touch at runtime, not just by diff.

Real plan (pinned Core through the real boundary):

```jsonc
{
  "ok": true,
  "status": { "draftStatus": "DRAFT_READY", "verificationStatus": "NOT_VERIFIED",
              "authority": "PLANNING_ONLY", "compatibility": "UNKNOWN",
              "parallelExecutionSafety": "UNPROVEN" },
  "summary": { "totalDependencies": 3, "proposed": 2, "needsReview": 0,
               "statusCounts": { "PROPOSED": 2, "EXCLUDED": 1 } },
  "policy": { "targetLevel": "yellow", "minLagOkPct": 80,
              "exclusions": ["kontur-fixture-c"],
              "policyHash": "60373d3d…" }
}
```

`dependencies`: `kontur-fixture-a` PROPOSED 1.2.0→1.9.0,
`kontur-fixture-b` PROPOSED 2.0.0→2.1.0, `kontur-fixture-c` EXCLUDED.
No `policyDiscrepancy`; `truncation.truncated=false`.

Pilot run: `opencode run -m <configured-provider/model> --auto --dir <fixture>`
(provider identifier omitted from this public record), with the
skill prompt (`scripts/e2e_prompt.md`). The model loaded the skill, ran the
preflight, called `get_upgrade_plan` with `registry` + `exclude`, passed the
safety gate, created an isolated worktree **outside** the project at the
baseline, applied the PROPOSED rows, updated the lockfile against the loopback
registry, ran the real checks, and wrote `deploom-evidence.json` — exit 0,
`OUTCOME: PASSED`.

Independent verification (`scripts/e2e_pilot.py --phase verify`), 12/12 PASS:

| Check | Result |
|---|---|
| worktree created outside project | PASS (`…/fixture2-wt-run-39ed634713d1`, branch `deploom-upgrade-run-…`) |
| original checkout deps untouched | PASS (a 1.2.0 / b 2.0.0 / c 3.0.0) |
| original checkout clean | PASS |
| worktree a → 1.9.0 | PASS (actual 1.9.0) |
| worktree b → 2.1.0 | PASS (actual 2.1.0) |
| excluded c unchanged in worktree | PASS (actual 3.0.0; also `npm ls` → c@3.0.0) |
| npm test in worktree (exclusion invariant) | PASS (`SMOKE_test_OK a=1.9.0 b=2.1.0 c=3.0.0`) |
| evidence JSON present | PASS |
| evidence outcome PASSED | PASS |
| evidence records both updates | PASS |
| evidence records c EXCLUDED | PASS |
| transcript captured | PASS |

Evidence file (excerpt): `outcome=PASSED`, `blueprintRunId=run-39ed634713d1`,
`updatedDependencies=[{a 1.2.0→1.9.0},{b 2.0.0→2.1.0}]`,
`skippedDependencies=[{c EXCLUDED}]`, `codeChanges=[]`, and a
`verification[]` list with honest statuses: install/build/tests/lockfile-sync/
policy-audit/diff-audit `PASSED`, typecheck/lint `NOT_AVAILABLE` (no such
scripts). `sourceIdentity.status=PROVEN` — the model byte-proved worktree
inputs match the plan (noting CRLF normalization and matching git blobs).

The original checkout was never modified, nothing was committed/pushed, the
worktree was kept. The pilot driver, registry seed and the exact plan are
reproducible from `scripts/e2e_*.py` (run
`python scripts/e2e_pilot.py --phase all --out <dir> --fixture <dir>`).

## 6. Remaining risks / honest gaps

- Claude Code and Codex execute flow untested on this host (CLI/model/account
  limits above); the install + mount paths are covered by unit tests and the
  adapters document the exact steps.
- The pilot ran against a local registry; a public-registry pilot was not
  possible (npmjs.org unreachable) — the contract and the Core's
  `registry=` override are identical, but public-metadata behavior was not
  exercised online.
- `Desktop` (`AlexanderLevenskikh/deploom`) was **not** modified; `upstream/deploom`
  submodule pin untouched.
