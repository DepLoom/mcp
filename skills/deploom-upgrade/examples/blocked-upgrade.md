# Example: blocked upgrade

Three realistic ways a flow must stop with `BLOCKED` — never work around them.

## 1. `policyDiscrepancy` present

`get_upgrade_plan(..., exclude=["react"])` returns `ok=true` but:

```jsonc
"policyDiscrepancy": {
  "requested": { "targetLevel": "yellow", "minLagOkPct": 80, "exclusions": ["react"] },
  "applied":   { "targetLevel": "yellow", "minLagOkPct": 80,
                 "exclusions": ["react", "lodash"], "policyHash": "…" },
  "mismatchedFields": ["exclusions"],
  "message": "..."
}
```

**Agent behavior:** STOP execution even though `ok=true`.

- Report: blocked by policy discrepancy — the planner applied an exclusion set
  that differs from the request; acting on the rows could apply a policy the
  user never asked for.
- Do not reinterpret the plan; do not "correct" it silently. Ask the user how to
  proceed (e.g. re-plan with the full intended exclusion list) and re-run the
  gate on the new response.

## 2. `DRAFT_PARTIAL` — the plan is not complete

```jsonc
"status": { "draftStatus": "DRAFT_PARTIAL", "partialReason": "deadline budget..." },
"partial": { "partial": true, "replanRequired": true, "processed": 40, "processedTotal": 300 }
```

**Agent behavior:** do **not** apply a partial plan as if it were complete.

- Report: partial planning; only 40/300 dependencies processed, whole proposal
  cannot be trusted for execution.
- Re-plan with a larger `deadline_seconds` (within [5, 600]) once; if still
  partial or the planner errors, stop and report the cause.

## 3. Conflict / needsReview that requires the user

`dependencies` contain a row with `status: "PROPOSED"` and
`needsReview: true` plus `breakingChanges: ["drop of CommonJS export"]`, or a
dependency is `PEER_UNRESOLVED` / `UNKNOWN_METADATA`.

**Agent behavior:** the plan itself is valid, but acting on that dependency is a
user decision.

- Stop before touching it; present the exact row (package, from, to, reason,
  notes) and ask for explicit confirmation.
- `userApprovalRequired: true` in the evidence report; outcome `BLOCKED` for the
  affected dependency, `PARTIAL` if the rest proceeded with consent.

## Common to all three

- Nothing is written to the original checkout; if a worktree was created for a
  stopped execution, it is kept for inspection and reported (never deleted
  automatically).
- The evidence report records the stopping reason verbatim; the user gets a
  clear "what blocked it and what I need from you" summary.
