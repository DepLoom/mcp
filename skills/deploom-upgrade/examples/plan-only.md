# Example: advisory plan only

**User:** "Propose the latest compatible updates in the existing major lines.
Do not edit the project or change React."

Record `PLAN_ONLY`, objective `latest-compatible-in-approved-lines`,
`target_mode=advisory`, `exclude=["react"]` and the observed approved major lines.
The user delegated version proposals, not execution.

Call `get_upgrade_plan` with that contract and the configured registry. Read
workspace coverage, policy/source identity and critical conflict/search evidence,
not only the dependency table. Open the full artifact if decision evidence is
missing even when no rows were truncated. A root-only result is not a monorepo
completion verdict.

Summarize proposed targets, excluded React, hard blockers and unknown coverage.
Explain any alternative as an agent proposal. If a row is POLICY_SATISFIED, do not
claim it is latest without candidate evidence. Write `outcome=PLAN_READY` with
all execution categories NOT_RUN, no changes and no fabricated verification.
No follow-up permission is needed to complete the analysis already requested.
