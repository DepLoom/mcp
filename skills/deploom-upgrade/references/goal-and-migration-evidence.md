# Explicit goals and optional migration evidence

Load for the explicit goal route or when official migration references are needed;
do not load every reference for an ordinary health-plan request.

## Choose the existing planning route

- `get_upgrade_plan` remains the health/policy route. Keep its Yellow/Green,
  target authority, exact pins, exclusions and required health evidence.
- Use `get_goal_plan(project_path, goal, deadline_seconds)` for an explicitly
  agreed functional-maintenance goal, or a finite coupled choice supported by
  the installed goal schema. It is not a shortcut around a blocked health plan.
- Read the installed `deploom_mcp.goal_contract` schema when constructing a goal;
  use the agreed policy bytes and mandatory checks, not invented digests or IDs.
  Prefer an already registered exact goal/profile when one was provided.
  V1 binds exact targets without combinatorial solving. V2 additionally declares
  explicit source-repair paths and before hashes. V3 uses approved finite domains
  and Core/Z3; preserve the returned parent/selection/derived-intent lineage.
- Current support is standalone root npm lock v2/v3 on the validated native
  Windows toolchain. Do not infer pnpm, Yarn or monorepo support. Unsupported
  manager/workspace/lock or missing host policy stays a concrete limitation.
- `REPORT_ONLY` does not waive a health requirement or hard prohibition. A root
  assignment, resolver success, installation and project checks are distinct.
  Every goal proposal remains PLANNING_ONLY/NOT_VERIFIED/executionReady=false.
  Source-repair scope or a policy digest alone grants no execution authority.

On the goal route, `verify_goal_candidate` needs an existing host registration,
run/candidate IDs, binding and explicit verify operation; caller prose cannot
create that authority. `apply_goal_candidate` additionally needs the separately
pinned final capability. `get_goal_checkpoint` defaults to `mode="current"`,
which retains strict metadata freshness and current source continuity checks.
Use explicit `mode="historical"` only to inspect a terminal owner checkpoint:
`HISTORICAL_RECORD_VERIFIED` verifies stored record integrity and selected-profile
bindings, not current state or mathematical optimality. It permits expired
metadata for this read only, exposes missing legacy issuance timestamps, and
never renews execution permission. Do not fall back to historical success to
accept a failed current check or resume writes. `get_goal_report` keeps imported data
CLIENT_REPORTED/NOT_VERIFIED. Without the required host records, leave the goal
unverified; do not turn ordinary client checks into an accepted goal checkpoint.

## Decide whether references are needed

Omit `migration_evidence_request` by default. Include it only for an explicit
user request or an already authorized need for official migration guidance,
verified current/selected exact versions, a supported pair and enough remaining
budget. Do not use package count or major/minor labels as proof of safety.

Do not request it for no transition, unknown source/target identity, unsupported
pairs, an exhausted/insufficient deadline, or when existing fresh pinned evidence
already answers the question. Never repeat an unchanged request merely because
it returned UNKNOWN/PARTIAL. Explain missing coverage instead of inventing it.
The evidence budget is part of the tool deadline, not an extra retry allowance.

If the exact pair is already known, combine the request with the first relevant
goal call. If coupled selection must happen first, do not preselect a target to
fit the evidence provider. Reuse adequate prior evidence. A genuinely necessary
second call has a real planning cost; record it and preserve the original budget
and lineage rather than claiming the plan was free or silently rebinding it.

The initial official_migration_evidence.v1 catalog covers only:

- strip-json-comments 3.1.1 → 5.0.0;
- Vite 6.4.5 → 7.0.0, including plugin-react 4.5.2 as a reference, never an
  automatically selected companion.

Use the installed contract for any later catalog expansion, not an assumed
package-to-repository or alias mapping. Versions come from the observed lock and
selected proposal, not a manifest range or latest tag. Example optional argument
for an already matching goal (not an instruction to choose these targets):

```json
{
  "schema": "official_migration_evidence.v1",
  "mode": "ON_DEMAND",
  "transitions": [{"package": "strip-json-comments", "from": "3.1.1", "to": "5.0.0"}],
  "detail": "summary",
  "localApplicability": "OFF"
}
```

Start with summary; request full text or local syntax observations only when
needed. `detail: usage` does not itself enable local inspection: that separately
requires `localApplicability: ON` and the operator's supported host parser.
Missing parser or unsupported local syntax stays UNKNOWN. Do not install or
execute a project-owned parser/plugin/config to make the observation succeed.

## Interpret without changing authority

`officialMigrationEvidence` is REFERENCE_ONLY. Preserve commit URLs, content
hashes, pin freshness, coverage and conflict status. Verify which source supports
a claim: package metadata, source literal, documented default and keyword hint
are different kinds of evidence. LABELLED_KEYWORD_HINTS are not proven breaking
behavior or Core constraints. A verified document does not establish that an API
is used locally or that an application will work after repair.

CONFLICT remains visible; for example, the documented trailingCommas default in
types can differ from source/README. Do not silently choose one as runtime truth.
UNKNOWN/PARTIAL/unsupported/stale evidence is not compatibility or zero risk.
Local AST findings are OBSERVED_SYNTAX_ONLY with semanticApplicability UNKNOWN;
NOT_OBSERVED_SCOPED means only the inspected JS/TS scope. Unreadable, dynamic,
alias, unsupported or capped coverage is not an unused-API verdict. Parser caps
limit this observation, not the authorized migration or required-check scope.

Keep original goal/binding/assignment, policy, permissions, required checks and
checkpoint status. References cannot grant repair or execution, assert a passed
install/runtime, weaken immutable scope, or override an independently observed
failure. Apply authorized changes and verify them through the existing owners.
No project source, lock, secrets, paths or local observations go to providers.

## Record the actually consumed Skill

Record installed version/bundle and the main/reference hashes separately from
actual client discovery and full-content reading. An enabled metadata entry or
matching install receipt is not proof a model loaded the body. Use the exact
selected path when multiple same-name versions exist. Read this reference only
when its branch is needed; do not claim it was consumed based on SKILL.md alone.
A deterministic SDK replay establishes request/response wiring, not autonomous
Skill selection, model behavior, token savings or migration success.
