# Evidence report

One client-neutral JSON format produced at the end of every walk-through
(`PLAN_ONLY` or `EXECUTE`). The format is the same in OpenCode, Claude Code and
Codex; only the `client` value (when determinable) differs.

## Outcome

`outcome` distinguishes the five cases:

- `PASSED` — full execute flow succeeded: gates passed, updates applied,
  checks ran, plan verified.
- `PARTIAL` — a subset of the plan was applied/verified; some rows remain.
- `BLOCKED` — a safety gate or missing capability stopped the flow before
  changes (or before applying changes).
- `FAILED` — checks failed after changes and the repair budget was exhausted.
- `INFRA_ERROR` — infrastructure failure (MCP unreachable, planner errored,
  network, toolchain) — not a project decision.

## Format

```json
{
  "workflow": "deploom-upgrade",
  "client": "opencode",
  "targetLevel": "yellow",
  "outcome": "PASSED",
  "baselineCommit": "cc10d36…",
  "workingTree": "C:/…/.git/worktrees/deploom-upgrade-<runId>",
  "blueprintRunId": "…",
  "appliedPolicy": {
    "targetLevel": "yellow",
    "minLagOkPct": 80,
    "exclusions": ["react"],
    "policyHash": "…"
  },
  "updatedDependencies": [
    {"package": "uuid", "from": "3.4.0", "to": "9.0.1", "needsReview": false}
  ],
  "skippedDependencies": [
    {"package": "react", "status": "EXCLUDED", "reason": "user excluded react"},
    {"package": "is-number", "status": "NO_CHANGE", "reason": "already satisfied"}
  ],
  "codeChanges": [
    {"file": "src/legacy.js", "diffSummary": "adapt uuid v9 import", "reason": "breaking change", "attempt": 1}
  ],
  "verification": [
    {"check": "install", "status": "PASSED", "command": "npm ci --registry=…",
     "exitCode": 0, "elapsedMs": 1234, "note": "lockfile in sync"},
    {"check": "build", "status": "PASSED", "command": "npm run build", "exitCode": 0},
    {"check": "tests", "status": "NOT_RUN", "command": null, "note": "no test script"}
  ],
  "remainingRisks": [
    "react transitive copies unconfirmed (planner guarantees direct deps only)"
  ],
  "userApprovalRequired": false
}
```

Rules for the format:

- `client` is set only when the client can be identified reliably; otherwise
  omit it (never invent a name).
- For `PLAN_ONLY` runs: `outcome` is `BLOCKED` only when gates failed; otherwise
  use `PASSED`/`PARTIAL` for a successfully produced and analyzed plan, set
  `updatedDependencies`/`codeChanges` empty, `workingTree` null, and
  `userApprovalRequired` true only if the analysis surfaced decisions for the
  user (needsReview rows, exclusions).
- `verification[]` entries follow the status vocabulary
  `PASSED / FAILED / INCONCLUSIVE / NOT_RUN / NOT_AVAILABLE` with command, exit
  code, elapsed, reason and artifact paths.
- `userApprovalRequired` must be true whenever the user must decide before the
  next step (needsReview rows, breaking changes, unproven conditions, unsafe
  install scripts).

## Mandatory user-facing report

Show the user, in plain language:

- what was updated (packages, versions) and to what;
- what was excluded (and that it stayed untouched in manifest and lockfile);
- what remained blocked or unconfirmed;
- which files were changed (worktree diff summary);
- which checks passed and which failed/skipped;
- where the worktree and the full diff live;
- whether a user decision is required and what exactly.

## Boundaries

- Never create a commit, push, PR, or merge automatically — stop at the report.
- Never delete the worktree before the user reviewed it.
- A successful plan is not presented as a successful migration; the report
  states verification honestly even when `PASSED` applies only to what actually
  ran.
