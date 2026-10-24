# Blueprint contract

Reference for the DepLoom MCP tool contract. Load when you are about to call
`get_upgrade_plan` or interpret its output. The source of truth is
`contracts/upgrade-blueprint.schema.json` and `docs/STAGE_01/02` in the
DepLoom/mcp repository; this file is the agent-facing digest.

## The single public tool

```jsonc
{
  "project_path": "/abs/path/to/project",   // read-only; a package.json is required
  "target_level": "yellow",                  // "yellow" | "green" (default "yellow")
  "min_lag_ok_pct": 80,                      // min registry-lag % for a clean proposal
  "deadline_seconds": 20,                    // default 20, clamped to [5, 600]
  "include_prerelease": false,
  "registry": null,                          // optional npm registry URL (http/https)
  "registry_metadata_ttl": 3600,             // packument cache TTL seconds
  "output_dir": null,                        // optional artifact dir; MUST be outside the project
  "exclude": null                            // optional [pkg, ...]; enforced BEFORE planning
}
```

- Input validation is fail-closed: invalid args/goal/policy/deadline/registry/
  exclude are rejected before any work (`invalid-*` error codes).
- The plan is built from the project state **at call time** inside a managed
  run directory; the project itself is only ever read.
- `exclude` is passed to the planner as `--exclude-dependency <pkg>|reason`
  BEFORE planning — it is planning scope, never a post-filter.

## Success response (compact protocol)

Top-level: `{"ok": true, ...compact payload}`. Key blocks:

- `run.runId` — identifier to carry into the evidence report and logs.
- `status` — `draftStatus` (`DRAFT_READY` | `DRAFT_PARTIAL`), `verificationStatus`
  (= `NOT_VERIFIED`), `authority` (= `PLANNING_ONLY`), `compatibility` (= `UNKNOWN`),
  `parallelExecutionSafety` (= `UNPROVEN`), `partialReason`.
- `partial` — `partial`, `partialReason`, `processed`, `processedTotal`,
  `replanRequired` (= true ⇔ `DRAFT_PARTIAL`).
- `policy` — the policy that was **applied**: `targetLevel`, `minLagOkPct`,
  `exclusions`, `policyHash`.
- `policyDiscrepancy` — present only when request and applied policy differ
  (`requested`, `applied`, `mismatchedFields`, `message`). **Present ⇒ STOP.**
- `summary` — `totalDependencies`, `proposed`, `needsReview`, `statusCounts`.
- `recommendationGroups` — display-priority groups with `packages` (parallel
  safety is always `UNPROVEN`, treat as advisory ordering only).
- `dependencies` — compact rows: `package`, `status`, `current`, `target`,
  `group`, `needsReview`.
- `artifacts` — file paths: `blueprint` (full blueprint JSON) and `plan`,
  `prompt`, `summary`, `solverComponents`, `manifest` when published. Read the
  full blueprint from these files when `truncation` clipped inline rows.
- `honesty` / `boundaryNote` — planning-only statements; quoted verbatim in the
  response, treat as authoritative.

## Failure response

`{"ok": false, "runId": ..., "error": {"code", "message", "detail"}}`.
Common codes and how the Skill reacts:

| code | meaning / action |
|---|---|
| `invalid-args` … `invalid-exclude` | caller bug — fix the call, do not retry blindly |
| `project-not-found` / `invalid-project` | wrong path or no `package.json` |
| `output-inside-project` / `state-inside-project` | write-boundary guard — do not pass an in-project output_dir |
| `core-not-found` | pinned submodule not initialized — infra, tell the user |
| `generator-failed` / `generator-timeout` | planner subprocess failed or hit its budget → re-plan with more `deadline_seconds` or report infra |
| `manifest-missing` / `plan-missing` / `plan-invalid` | bad planner output — infra, report |
| `contract-violation` | planner produced a non-planning status — refused by the boundary, report |
| `hash-mismatch` | published artifact does not match its declared hash — trust nothing, report |
| `schema-invalid` | blueprint violates the JSON Schema — report |
| `cancelled` | the request was cancelled before planning started — ask the user to retry |
| `response-too-large` | even the minimal payload cannot fit the byte budget — infra/scale, report |

## Dependency statuses

Rows carry one of these statuses (planning-only; reserved verified-pipeline
statuses never appear):

- **`PROPOSED`** — the planner proposes upgrading `package` to `target`. This is
  a proposal, not a guarantee: act on it only after gates pass and, for
  `needsReview` rows, user confirmation. `current` → `target` is the intended
  change.
- **`NO_CHANGE` / `POLICY_SATISFIED` / `DEFERRED`** — do nothing now; not
  actionable in this run.
- **`EXCLUDED`** — excluded by policy (e.g. "don't touch React"). Skip and keep
  excluded; never edit.
- **`BLOCKED` / `CANDIDATE_SEARCH_TRUNCATED`** — the planner could not produce a
  safe proposal; do not hand-pick a version.
- **`UNKNOWN_METADATA` / `UNKNOWN_SECURITY` / `PEER_UNRESOLVED` / `NO_TARGET` /
  `NOT_ATTEMPTED` / `INTERRUPTED` / `UNKNOWN`** — insufficient or unresolved
  evidence. Do not act as if it were proposed; report the reason and, if the
  user wants, re-plan.
- `needsReview: true` (or `breakingChanges` / `migrationNotes` present) — human
  confirmation required before touching that dependency.

## Source identity (what the plan was built from)

- `source.project.name` / `source.project.path` — the analysed project.
- `source.inputFiles` — **absolute paths** of the project input files the draft
  was read from (manifest, lockfile, settings). The boundary resolved these
  from the Core's `inputFilesByProject`.
- `source.inputHashes` — a map of **project name → combined fingerprint** as
  published by the Core (byte-lockstep over the project's input files). This is
  **not** a per-file SHA-256 map. Do not "verify" it yourself — instead verify
  identity by comparing `inputFiles` content between the plan and the checkout
  you intend to edit, and **re-plan when inputs changed**. Never present
  unconfirmed source identity as proven.

## When to re-plan

Re-plan (`get_upgrade_plan` again) whenever the project's meaningful inputs
(changed after planning before execution) — manifest, lockfile, or settings that
feed the draft. The blueprint is only valid for the project state it was built
from.
