---
name: deploom-upgrade
description: Plan and safely execute JavaScript and TypeScript dependency upgrades using the DepLoom MCP get_upgrade_plan tool, isolated Git worktrees, exclusions and evidence-based verification. Use when the user asks to upgrade/update project dependencies, proposes an upgrade to a target level (yellow/green), says "don't touch <package>" / exclusions, or wants the build/tests verified after updating packages.
metadata:
  version: "1.0.0"
  source: "https://github.com/DepLoom/mcp/tree/main/skills/deploom-upgrade"
  languages: "javascript, typescript"
---

# deploom-upgrade

Plan and (on request) safely apply dependency upgrades for a Node.js /
JavaScript / TypeScript project using the **DepLoom MCP** planning boundary,
then verify the result with real checks and produce an evidence report.

The Skill is a **workflow and safety contract**, not a dependency solver.
Planning verdicts come exclusively from `get_upgrade_plan` (pinned DepLoom Core).
Execution and verification use the coding client's own tools (file edits, git,
shell). It works the same in OpenCode, Claude Code and Codex.

## When to use

Trigger when the user asks to:

- "upgrade / update dependencies", "bring the project to yellow (or green)",
  "raise target level";
- "look at what can be upgraded but don't touch `<pkg>`" (plan-only);
- "upgrade and verify the build/tests" (execute mode).

Do **not** use for dependency *audits* that do not ask for an upgrade plan, or
for a project without a `package.json` manifest.

## Prerequisites

- The DepLoom MCP server is reachable and exposes the single tool
  `get_upgrade_plan` (STDIO local server; see the client-specific integration
  instructions in `integrations/<client>/README.md` of the DepLoom/mcp repo).
- If `get_upgrade_plan` is **not** available, STOP: report `BLOCKED` and tell
  the user how to enable it. Never substitute your own dependency-resolution
  logic for the planner.

## Mode selection (mandatory)

Classify the user intent **before** any action:

- `PLAN_ONLY` — "look at / propose / what can be upgraded", or analysis with no
  instruction to apply anything. Produce a blueprint analysis, change nothing.
- `EXECUTE` — "upgrade / apply / update and verify", an explicit instruction to
  change the project. Run the full controlled-execution workflow.

Rules:

- A plan-only request must **never** silently become execution.
- An execute request must pass every safety gate before any change.
- If intent is ambiguous, ask the user and default to `PLAN_ONLY`.

## Workflow

Follow the steps below in order. Each step has a detailed reference
(`references/*.md`) — load it when you need the specifics, don't keep the
whole contract in context.

1. **Preflight** — git root/HEAD/branch, tree state, package manager & lockfile,
   Node engines, manifests/workspaces, available check scripts, registry,
   target level, exclusions, client file permissions, MCP availability, worktree
   feasibility. If any safety condition cannot be established → `BLOCKED`.
   See `references/preflight.md`.
2. **Get upgrade blueprint** — call `get_upgrade_plan(project_path=…,
   target_level=…, exclude=[…], deadline_seconds=…, registry=…, …)`. Pass an
   explicit `deadline_seconds` so full-project scans finish inside the budget:
   `20` (the contract default) is too small for workspaces with dozens of
   packages — use `600` for a whole-repo scan, and pass the project `registry`
   detected in preflight. Read the compact response; open the full blueprint
   artifact if `truncation` limited inline rows.
   See `references/blueprint-contract.md`.
3. **Safety gate** — validate planning authority, policy identity, source
   identity, partial/conflict handling and the exclusions contract. A failed
   gate → `BLOCKED` (report, never act). See
   `references/preflight.md`, `references/blueprint-contract.md`.
4. **Plan-only response** — for `PLAN_ONLY`: summarize what is proposed /
   excluded / blocked / needs review, note the run id and artifact paths, change
   nothing.
5. **Controlled execution** — for `EXECUTE`: after the gate passes, create an
   isolated Git worktree at the baseline, apply only the planner-approved rows,
   keep exclusions intact, update the lockfile with the project's package
   manager, and make only minimal, justified code repairs (≤ 3 attempts per root
   cause). See `references/execution-policy.md`.
6. **Verification** — run the project's real checks (install/lockfile,
   typecheck, lint, build, tests) plus a policy audit and a diff audit; report
   each result with the honest status vocabulary. See
   `references/verification-policy.md`.
7. **Evidence report** — emit the single client-neutral JSON evidence report and
   a user-facing summary. Never auto-commit, -push, -merge, or -delete the
   worktree. See `references/evidence-report.md`.

## Mandatory safety gates (never skip)

- **Planning authority.** The blueprint remains
  `verificationStatus=NOT_VERIFIED`, `authority=PLANNING_ONLY`,
  `compatibility=UNKNOWN`, `parallelExecutionSafety=UNPROVEN` (the planner
  never produces `CONSTRAINT_SATISFIED`/`MATERIALIZED`/`PROJECT_VERIFIED`).
  `PROPOSED` means "this upgrade can be proposed", **not** "already updated".
- **Policy identity.** Compare requested vs applied target level, exclusions,
  `minLagOkPct`, `policyHash`. If the response carries a `policyDiscrepancy` —
  STOP execution even when `ok=true`. Do not ignore it.
- **Source identity.** Verify the project state the blueprint was built for
  matches the checkout you are about to edit (`source.inputFiles`; treat
  `source.inputHashes` as opaque Core fingerprints — do not invent your own
  per-file hash). If inputs changed, re-plan before executing.
- **Partial planning.** `draftStatus=DRAFT_PARTIAL` means the plan is
  **not** complete: do not execute it as complete. First re-plan **once** with a
  larger `deadline_seconds` (up to `600` within the contract) — the usual cause
  is the planner hitting its metadata budget, not a real conflict. Only
  `DRAFT_READY` plans may be executed.
- **Registry metadata failure.** If planning reports
  `registry-failed` / `UNKNOWN_METADATA` on every package (planner subprocess
  could not fetch packuments inside its budget), treat it as a budget/infra
  problem: re-plan with a larger `deadline_seconds` (or after verifying the
  `registry`), and only then evaluate the result. Never silently substitute
  planner targets with your own `npm view` picks — any such manual selection is
  a `needsReview` item that changes execution authority and must be presented
  to the user for confirmation and recorded in the evidence report.
- **Conflicts & needsReview.** Rows with `needsReview`, breaking changes,
  unknown dependencies, unresolved peers, missing metadata, or unproven safety
  conditions require user confirmation before action. Never decide for the user.
- **Exclusions.** Keep the exclusion set intact through the whole workflow.
  Check the excluded packages in manifest and lockfile before and after — if the
  plan would change them anyway, STOP. The planner guarantees only **direct**
  dependencies, never transitive copies.
- **No substitute execution.** If a gate cannot be proven, `BLOCKED`, not
  worked around. Missing permissions in the coding client → `BLOCKED`, never
  bypass the client's sandbox.

## Honesty rules

- Verification results use the status vocabulary
  `PASSED / FAILED / INCONCLUSIVE / NOT_RUN / NOT_AVAILABLE`. Never report
  `PASSED` for a check that did not run, and never substitute DepLoom's
  internal verdicts for real check results.
- Report what was updated, excluded, blocked, unconfirmed, and where the
  worktree and diff live. If a name (e.g. the client) is not known, don't invent it.
- A successful plan is **not** a successful migration.

## References

Progressive disclosure — load on demand:

- `references/blueprint-contract.md` — the `get_upgrade_plan` contract, compact
  response keys, status vocabulary, error codes, artifact layout.
- `references/preflight.md` — the 12 preflight checks and the `BLOCKED` rules.
- `references/execution-policy.md` — worktree isolation, source-identity proof,
  dependency application, exclusions enforcement, bounded code repair.
- `references/verification-policy.md` — check pipeline, honest statuses,
  baseline-vs-regression, safe install for untrusted projects.
- `references/evidence-report.md` — the single evidence JSON format and the
  mandatory user-facing report.

## Examples

- `examples/plan-only.md` — "look at what can be upgraded, don't touch React"
  (no writes).
- `examples/yellow-exclude-react.md` — full execute flow with worktree, install,
  build/test and evidence report.
- `examples/blocked-upgrade.md` — `DRAFT_PARTIAL` / `policyDiscrepancy` stopped
  cleanly with a re-plan path.
