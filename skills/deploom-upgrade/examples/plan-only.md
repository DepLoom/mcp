# Example: plan-only ("don't touch React")

**User:** «Посмотри, что можно обновить до Yellow. React не трогай.» /
"Look at what can be upgraded to yellow; don't touch React."

**Intent:** `PLAN_ONLY` — analysis only, no writes.

## Agent flow

1. Preflight (read-only): confirm the git root, that `get_upgrade_plan` is
   available, detect the package manager.
2. Call the tool:

```jsonc
get_upgrade_plan(project_path = "<project>", target_level = "yellow", exclude = ["react"])
```

3. Read `ok`, `status`, `policy`, `summary`, `dependencies`,
   `policyDiscrepancy` (absent), and open `artifacts.blueprint` when rows were
   truncated.

## Example response analysis (illustrative)

```jsonc
{
  "ok": true,
  "runId": "run-…",
  "status": { "draftStatus": "DRAFT_READY", "verificationStatus": "NOT_VERIFIED",
              "authority": "PLANNING_ONLY", "compatibility": "UNKNOWN",
              "parallelExecutionSafety": "UNPROVEN" },
  "policy": { "targetLevel": "yellow", "minLagOkPct": 80,
              "exclusions": ["react"], "policyHash": "…" },
  "summary": { "totalDependencies": 4, "proposed": 2, "needsReview": 0,
               "statusCounts": { "PROPOSED": 2, "EXCLUDED": 1, "NO_CHANGE": 1 } }
}
```

Agent summary to the user:

- Proposed (would go yellow with current policy): `uuid 3.4.0 → 9.0.1`,
  `lodash 4.17.15 → 4.17.21`.
- Excluded: `react` (will not be touched — plan scope).
- Already satisfied: `is-number`.
- Nothing was changed; no worktree was created. Blueprint run `run-…`; full
  blueprint artifact at `artifacts.blueprint`.
- Ask: "Запустить выполнение? (EXECUTE)" — never start executing on your own.

## Evidence

`evidence-report.md` with `outcome: PASSED` (plan produced and analyzed), empty
`updatedDependencies`, `userApprovalRequired: true` if the user must decide
whether to proceed.
