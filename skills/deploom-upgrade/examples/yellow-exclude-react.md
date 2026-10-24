# Example: full execute with exclusion ("upgrade to yellow, don't touch React, verify")

**User:** «Обнови зависимости проекта до Yellow. React не трогай. Проверь сборку
и тесты.» / "Upgrade the project's dependencies to yellow, don't touch React,
verify the build and tests."

**Intent:** `EXECUTE`.

## Agent flow

1. **Preflight** — git root/branch/HEAD clean; npm + `package-lock.json`;
   node satisfies `engines`; scripts `build`/`test` present; `get_upgrade_plan`
   available; wordtree feasible. Record baseline.
2. **Blueprint** — call `get_upgrade_plan(project_path, target_level="yellow",
   exclude=["react"])`. Gate: `ok=true`, `status.draftStatus=DRAFT_READY`,
   `policy.exclusions=["react"]`, **no** `policyDiscrepancy`; `needsReview`
   rows confirmed with the user (none in this example).
3. **Isolation** — `git worktree add <tmp>/deploom-upgrade-<runId>
   -b deploom-upgrade-<runId>` from `baselineCommit`. Prove the worktree's
   `source.inputFiles` byte-match the planned inputs (baseline clean, so equal
   to the commit) — record the proof.
4. **Apply** — for each `PROPOSED` row in the worktree manifest
   (`uuid 3.4.0 → 9.0.1`, `lodash 4.17.15 → 4.17.21`); run
   `npm install --registry=<project registry>`; verify `npm ls` matches targets;
   diff manifest+lockfile and map every change to a `PROPOSED` row; confirm
   `react` is byte-identical before/after in manifest and lockfile.
5. **Repair** — an import breaks (`require('uuid').v4` → `uuid.v4()` per the
   breaking-change note). Apply one minimal edit (attempt 1), rerun tests.
6. **Verification** — run install, typecheck (if present), build, tests; record
   each with command/exit code/status.
7. **Evidence report** — write the report; show the user the worktree path and
   `git diff`; no commit/PR/merge.

## Evidence (illustrative excerpt)

```jsonc
{
  "workflow": "deploom-upgrade",
  "client": "opencode",
  "targetLevel": "yellow",
  "outcome": "PASSED",
  "baselineCommit": "f3a1c…",
  "workingTree": "C:/…/.git/worktrees/deploom-upgrade-run-…",
  "blueprintRunId": "run-…",
  "appliedPolicy": { "targetLevel": "yellow", "minLagOkPct": 80,
                     "exclusions": ["react"], "policyHash": "…" },
  "updatedDependencies": [
    {"package": "uuid", "from": "3.4.0", "to": "9.0.1", "needsReview": false},
    {"package": "lodash", "from": "4.17.15", "to": "4.17.21", "needsReview": false}
  ],
  "skippedDependencies": [
    {"package": "react", "status": "EXCLUDED", "reason": "user excluded react"}
  ],
  "codeChanges": [
    {"file": "src/uuid-client.js", "diffSummary": "uuid v9 named export",
     "reason": "breaking change", "attempt": 1}
  ],
  "verification": [
    {"check": "install", "status": "PASSED", "command": "npm ci --registry=…",
     "exitCode": 0},
    {"check": "typecheck", "status": "NOT_AVAILABLE", "note": "no typecheck script"},
    {"check": "build", "status": "PASSED", "command": "npm run build", "exitCode": 0},
    {"check": "tests", "status": "PASSED", "command": "npm test", "exitCode": 0}
  ],
  "remainingRisks": ["react transitive copies unconfirmed"],
  "userApprovalRequired": false
}
```

## User-facing summary

- Updated: `uuid`, `lodash` (with versions); `react` untouched (byte-identical
  in manifest + lockfile).
- Build and tests passed; diff in the worktree (path above); nothing committed.
