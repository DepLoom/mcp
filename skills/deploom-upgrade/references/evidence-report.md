# Evidence report

For executed migrations or explicitly requested evaluations, keep one concise
decision/acceptance record and link existing logs; do not duplicate them across
reports. The record indexes applicable deliverables required by the user or the
Core execution workflow, including migration guides/reports; it does not replace
them or repeat their full text. User instructions govern the task scope. Ordinary
`PLAN_ONLY` work needs only a concise decision summary and does not execute Core
migration or delivery instructions. The structured format below is optional for
planning and is client-neutral.
Validate its logical consistency with the bundled
`scripts/validate_evidence.py`. The validator checks claims and input identities;
it cannot prove that a reported command actually ran or that a supplied hash is
correct. Retain the real logs and covered input files for review.

## Before execution

Record authority references, action, objective, `targetMode`, approved version
lines, scope and exclusions. Define required checks in four separate categories:
`install`, `checks`, `runtime`, `policy`. Keep the accepted criteria stable; record
an explicitly authorized waiver instead of silently removing a failed criterion.

Use this **plan-only skeleton** and fill actual identities when they are known:

```json
{
  "schemaVersion": 2,
  "workflow": "deploom-upgrade",
  "mode": "PLAN_ONLY",
  "targetMode": "advisory",
  "objective": "latest-compatible-in-approved-lines",
  "targetLevel": "yellow",
  "completion": "COMPLETED",
  "outcome": "PLAN_READY",
  "authorities": [{"id": "task", "reference": "user request to propose updates"}],
  "loadedSkill": {"contractVersion": "2.0.0", "sha256": null, "status": "UNKNOWN"},
  "blueprintRunId": null,
  "finalInputs": {"manifests": null, "lockfile": null, "environment": null, "source": null},
  "acceptance": {"install": ["clean-install"], "checks": ["typecheck", "build", "tests"], "runtime": [], "policy": ["installed-policy"]},
  "results": {"install": "NOT_RUN", "checks": "NOT_RUN", "runtime": "NOT_RUN", "policy": "NOT_RUN"},
  "decisions": [],
  "declarationChanges": [],
  "deliverables": [],
  "counts": {"changedDeclarations": 0, "updatedUniqueNames": 0, "addedUniqueNames": 0, "removedUniqueNames": 0},
  "verification": [],
  "remainingUnknowns": ["execution has not been requested"]
}
```

`PLAN_READY` only means the requested analysis was completed. For execution, set
`mode=EXECUTE`; completion and outcome remain separate. `PASSED` requires all
accepted criteria on the final state. Otherwise use `PARTIAL`, `BLOCKED`,
`FAILED`, `INFRA_ERROR` or `CANCELLED` according to the observed result. Interrupted
or incomplete sessions must not claim a completed migration.

## Decisions and changes

Example of a **synthetic manual proposal**, not a solver-approved result:

```json
{
  "id": "utility-decision",
  "package": "example-utility",
  "project": "root",
  "plannerStatus": "CANDIDATE_SEARCH_TRUNCATED",
  "plannerTarget": null,
  "selectedTarget": "1.4.2",
  "targetOrigin": "manual",
  "authorityRef": "task",
  "evidence": ["registry-snapshot.json#/example-utility/1.4.2", "peer-review.json"],
  "reason": "Stable same-line candidate inside delegated scope; planner coverage remains incomplete",
  "requiredVerification": ["clean-install", "typecheck", "affected-runtime"]
}
```

The authority reference must cover this action. In pinned mode a manual
alternative also records `revisedTargetAuthorization=true` and references the
actual revised user decision. Never relabel the original UNKNOWN as compatible.
Add approval only when existing authority does not cover the concrete proposal.

Index required execution deliverables once in `deliverables`: each entry has
`path`, `requiredBy` (the task/workflow reference), and `status=PRESENT|MISSING|NOT_VERIFIED`.
For example, point to the required developer upgrade guide and migration report
instead of copying their contents into JSON. Missing required delivery remains
visible and cannot support an overall `PASSED` outcome. The validator checks the
record's consistency; the caller must actually inspect the referenced files.

For every changed declaration record `file`, `section`, `package`, `from`, `to`
and `decisionId`. Use null only for addition/removal. Count changed declarations
separately from unique names with version changes, additions or removals. A name
can occur in several workspaces; count it once per operation category. An
unchanged row is never an upgrade; do not count a whole family as one name.

## Check records and final inputs

Each executed check has `check`, `kind`, `status`, `command`, observed `exitCode`,
`startedAt`, `finishedAt` (ISO8601 with timezone), `log`, and `inputs`.

- `kind=install` binds hashes for manifests, lockfile and environment. Record
  `installMode=clean|frozen|incremental` from the actual command; only clean or
  frozen installation can satisfy final-install acceptance.
- `kind=checks|runtime|policy` also binds relevant source/config hashes.
- Use the hashing method and covered paths recorded in preflight; exclude
  generated files and node_modules from source identity. Unknown hashes stay
  null and cannot support a final-state PASS claim.
- A policy check additionally has `scopeComplete=true` only when the whole
  intended final installed scope and requested criterion were actually checked.
- Retain attempts in the record. A historical PASSED remains historically true
  after an input change, but it does not satisfy final acceptance. The latest
  executed attempt for an accepted check determines its current result.
- `results.install/checks/runtime/policy` are independent. A successful build or
  unit suite cannot supply runtime or policy evidence. Missing criteria remain
  `NOT_VERIFIED`, `NOT_RUN` or `NOT_AVAILABLE` as applicable.

Record baseline and final source/manifest/lock identities, actual installed
versions, diff path, workspace coverage, exclusions, skipped/blocked packages,
warning attribution and remaining unknowns. Do not fill a missing log, timestamp,
exit code or duration from memory, a neighboring run, or a copied result.

## Timing, usage and isolation

Use first user message to original final response for session wall time; show
instrumented command bounds separately. Preserve confirmed operator waits and
infrastructure intervals, using interval unions when they overlap. Unknown gaps
remain unknown; a long tool wall time is not proof of slow MCP/server work.

Keep unique message token counters once, with input/output/reasoning/cache-read/
cache-write separate. Do not add overlapping counters or convert a proxy zero
cost to a monetary claim. Retain the original cutoff and separate later repairs.
In blinded evaluation, use only the assigned slot and frozen shared inputs;
reading another arm's versions, reports or metrics breaks that isolation.

## User-facing summary

State what changed and why, including planner/manual target origin and remaining
work. Show separate install, project-check, runtime and policy results, the
important failures/limitations, and where the diff/evidence live. Report source,
installed and actually loaded Skill identity separately. A completed session is
not by itself a verified migration or a proven policy improvement.
