# Execution policy

Rules for applying a **`DRAFT_READY`** blueprint in `EXECUTE` mode. Nothing in
this file is client-specific — the same rules apply in OpenCode, Claude Code and
Codex.

## Git isolation (mandatory)

Make every change in an isolated working copy; never edit the original checkout.

1. From the **baseline** (the recorded `baselineCommit` / clean tree): create a
   worktree at the same commit on a dedicated branch, e.g.
   `git worktree add <path> -b deploom-upgrade-<runId>`.
2. Record before touching anything:
   - `baselineCommit` (HEAD SHA) and the branch;
   - blueprint `runId`;
   - the applied policy (`targetLevel`, `minLagOkPct`, `exclusions`, `policyHash`);
   - the plan's `source.inputFiles`;
   - the new worktree path.
3. Never auto-run `git reset --hard`, `--force` push, or merge. Never delete the
   worktree before the user has reviewed it and the evidence report exists.

### Source identity across the worktree

A blueprint is only valid for the project state it was planned against. A fresh
worktree at `baselineCommit` is **not** automatically equivalent to what the
planner saw — prove it:

- Collect the plan's `source.inputFiles` (absolute paths in the analysed
  checkout).
- Compare those files' bytes in the worktree against the state they had at
  planning time. If the baseline was clean, they are identical to the commit
  files — verify by hashing the same files in both checkouts.
- If they differ (e.g. the original checkout was dirty or inputs changed after
  planning), **re-plan inside the worktree** (`get_upgrade_plan` with the
  worktree as `project_path`) and use that blueprint instead.
- If identity cannot be proven, mark it unconfirmed in the evidence report; do
  not present it as proven.

## Dependency application

1. Select **actionable rows**: `status == PROPOSED` and not excluded by policy.
   Skip `EXCLUDED`, `NO_CHANGE`, `POLICY_SATISFIED`, `DEFERRED` and anything
   unconfirmed.
2. For each actionable row, apply exactly the planner-recorded change
   `current → target` into the manifest (in the worktree), using the package
   manager's consistent syntax. **Never** pick alternative versions by hand in
   place of the planner's decision.
3. Update the lockfile with the project's own package manager, limiting to the
   plan's scope (e.g. `npm install --save-exact-…` where appropriate; prefer
   `npm install`/`yarn`/`pnpm install` and verify the resulting lockfile).
4. Verify the **actual** installed versions (`npm ls`, `node -e`, or reading the
   resolved lockfile entries / `node_modules`) match the plan `target`.
5. Diff the manifest and lockfile against the plan rows; every changed
   dependency must trace to a `PROPOSED` row. Anything else changed is an
   anomaly — stop and report.
6. If an install fails, record the error, revert that one step, and **re-plan**
   (in the worktree) rather than improvising resolutions.

## Exclusions (mandatory)

- Keep the exclusion set intact end-to-end: planning scope (passed to
  `get_upgrade_plan`), application scope, and audit.
- Before and after execution, compare the excluded packages' entries in the
  manifest **and** lockfile. If any other update would force the excluded
  package to change → STOP and report instead of quietly changing it.
- The planner guarantees exclusions for **direct** dependencies only. Do not
  promise that transitive copies are untouched.

## Code repair (bounded)

Repair only what is necessary to restore compatibility after the upgrade.

Banned:
- disabling/deleting tests or checks to make them pass;
- silently replacing TypeScript types with `any`;
- removing safety checks;
- re-architecting the project;
- unbounded retry loops.

Allowed: minimal, justified code changes that a breaking-change note implies.
Each change must be explained and recorded (`file`, reason, attempt number).

- At most **3 attempts** per root cause of an error.
- After the third failed attempt, report `FAILED` with diagnostics; do not lower
  the bar to force a green.

## Stopping conditions

Stop and report `BLOCKED` or `FAILED` (never silently continue) when:

- `policyDiscrepancy` is present in the response;
- `draftStatus != DRAFT_READY` (e.g. `DRAFT_PARTIAL`);
- any excluded package would change;
- the diff diverges from the plan;
- a needed client permission is denied;
- the repair budget is exhausted.
