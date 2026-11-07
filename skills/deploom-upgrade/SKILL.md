---
name: deploom-upgrade
description: Plan and execute JavaScript and TypeScript dependency upgrades with DepLoom MCP, explicit version objectives and scope, workspace constraint evidence, and verification of the changed project. Use for dependency upgrade plans, yellow/green targets, package exclusions, or requests to apply upgrades and check their results.
---

# deploom-upgrade

**Contract version: 2.0.14.** Canonical file hashes are in `contract.json`.

Use `get_upgrade_plan` for health/policy planning evidence. For an explicitly
agreed functional-maintenance or finite coupled goal, use `get_goal_plan` and
load `references/goal-and-migration-evidence.md`. Never replace a Yellow/Green
requirement with REPORT_ONLY to fit that route. Use the coding client's tools for
authorized edits and verification. A blueprint is `PLANNING_ONLY` and
`NOT_VERIFIED`; its targets are proposals, not proof of installation, runtime
compatibility, or policy attainment. An agent-selected alternative never becomes
a Core verdict. This workflow is client-neutral.

## Establish the task contract

Record these separately before planning. Reuse the user's existing instructions
and decisions; ask only when a material choice is unresolved.

- **Action:** `PLAN_ONLY` for analysis; `EXECUTE` when the user asks for changes.
  An analysis request does not authorize edits.
- **Objective:** `minimum-change-to-policy`,
  `latest-compatible-in-approved-lines`, or opt-in
  `explicit-version-targets` with exact stable `exact_targets` from the user.
  A yellow/green health target does not specify the newest acceptable version.
  Do not silently substitute another exact target or widen approved majors.
- **Target authority:** `pinned` keeps the agreed targets; `advisory` permits
  justified alternatives inside the authorized scope and version lines. Use
  `pinned` when targets or a reproducible target set were explicitly specified.
  If choosing versions is delegated, use `advisory` and record that authority.
  MCP's default is `pinned`; send the intended mode explicitly.
- **Scope:** mutable package names, allowed companion/code/config changes,
  approved version lines, exclusions, workspace coverage, and required checks.
  Full-project scope has no invented file-count limit. A necessary compatibility
  repair in an untouched file is still part of the migration when authorized.
- **Authority:** cite the user instruction covering execution, alternatives,
  scope expansion, and checks. `needsReview` requests a decision; an existing
  decision can satisfy it. The Skill cannot grant permission or override a
  denied client permission.

Keep four kinds of information distinct: hard compatibility constraints,
user requirements, planner recommendations, and evidence/search limitations.
Advisory mode changes recommendations, never proven peers/engines or user pins.

## Workflow

1. **Preflight.** Record baseline and existing changes, package manager, Node,
   registry, all manifests/workspaces, allowed checkout and check scripts.
   Reuse an isolated worktree already supplied by the user. Load
   `references/preflight.md` for details. Missing MCP is an infrastructure
   limitation; do not fabricate a DepLoom plan.
2. **Plan.** In PLAN_ONLY for npm workspaces, use `model_workspace_upgrade_v1`
   when available and the task permits registry-only planning against a
   host-approved registry. Otherwise use `get_upgrade_plan` with
   `workspace_request` v2. With `get_upgrade_plan`, send only
   `project_path` and `workspace_request` at the top level. Read [the workspace route](references/npm-workspaces.md) for
   the required workspace fields, selectors and lock-only boundary. A policy call
   with empty targets observes a held baseline. Its feasibility status does
   not establish a version change. If it gives no actionable assignment and
   version choice is authorized, follow the targeted recovery in the workspace
   reference, including its planning-only route when audit is unavailable.
   After each executed cohort, compare the current audit and freshness with the
   original goal. Continue independent in-scope cohorts while a gap remains.
   A partial reduction is a checkpoint.
   For a flat `get_upgrade_plan` request, send the objective, `target_mode`
   and scope. Use the user's explicit registry or let the host select its
   approved project route. If the MCP cannot resolve the project registry,
   use the exact authorized URL. Never substitute the public registry to
   get a result. Interpret goal responses using the goal reference.
   Inspect critical evidence before questions or edits: conflicts, unknowns,
   unresolved peers, exact-target security/policy assessment, coverage,
   source/policy identity, and next action. Current-version security does not
   assess a proposed version; complete evidence can describe a blocked plan.
   For V2 exact targets, read `summary.exactTargetPolicy`: a matching
   `PROPOSED` target and a blocked final `postPlanGoal` can coexist.
   Preserve original blockers, required/actual/shortfall, and an unavailable
   per-package lag status; never infer per-package lag thresholds from `latest`.
   Read `references/blueprint-contract.md`. **Untruncated rows do not mean complete
   decision evidence.** Open the full artifact when critical evidence is absent,
   ambiguous or marked incomplete, even if `truncation.truncated=false`.
   In `PLAN_ONLY`, treat Core execution instructions as future guidance; do not
   create execution files or perform migration steps.
3. **Reconcile.** A root-only plan is not a whole-workspace verdict. Reconcile
   shared assignments and affected child constraints before applying them.
   Do not execute a root proposal against a known child blocker. Do not invent
   global incompatibility from a peer absent only in a child manifest.
4. **Decide.** Write one short decision record per change or unresolved target:
   original verdict, evidence, chosen target, origin (`planner`/`manual`),
   authority, and verification required. Use the template in
   `references/evidence-report.md`. Resolve known blockers before asking the
   user to approve an option. `POLICY_SATISFIED` is not `ALREADY_AT_LATEST`.
5. **Apply when authorized.** Follow `references/execution-policy.md` in the
   agreed checkout. In pinned mode, re-plan or obtain an explicit revised
   target decision before deviating. In advisory mode, validate and record an
   alternative within existing authority. Keep exclusions and known constraints.
6. **Verify the final state.** Follow `references/verification-policy.md`.
   Bind install evidence to the final manifests, lockfile and environment;
   bind checks to their relevant source/config state. A later install, downgrade
   or lockfile edit invalidates earlier clean-install evidence for the new state.
   Test affected runtime behavior locally; build/unit success is not a substitute.
7. **Report.** For `PLAN_ONLY`, give a concise decision and uncertainty summary;
   no execution checklist or evidence file is required unless requested. For
   `EXECUTE` or an explicit evaluation, use the acceptance record in
   `references/evidence-report.md` and validate its JSON with
   `python scripts/validate_evidence.py <report.json>` from the Skill directory.
   Use it as an index of logs and applicable user/Core execution deliverables,
   including required migration guides/reports; avoid duplicating their content.
   Separate session completion, installation, project checks, runtime and policy;
   preserve failed attempts. The helper checks consistency, not execution truth.
   Do not commit, push, publish or remove the working copy without authorization.

## Optional official migration evidence

Ordinary planning omits `migration_evidence_request`. Load
`references/goal-and-migration-evidence.md` only when the task needs the explicit
goal route or official migration references. Add evidence only for an existing
authorized need, a supported observed exact transition and sufficient deadline.
A small update does not establish safety, but it does not itself justify another
lookup either. Reuse adequate fresh pinned references; avoid repeating a plan
just to obtain the same material. References and syntax observations grant no
repair, verification or execution authority.

## Transitive npm findings

When the vulnerable copy is transitive, do not pin an unchanged direct
dependency as the remediation. For npm workspaces follow the scoped override
workflow in `references/npm-workspaces.md`. Keep a range-crossing candidate
separate from compatibility evidence.

## Recovery rules

- Stop affected execution for changed input/policy identity, hash mismatch,
  denied permission, a hard conflict, or unreconciled workspace assignments.
  Independent work may continue only if its constraints and authority are
  established; do not turn one unknown row into a whole-project failure.
- `DRAFT_PARTIAL` is not a complete plan. Change the limiting condition before
  retrying. A larger deadline does not expand `draft_max_candidates`. Do not
  repeat the same capped request without new evidence or a changed constraint.
- For `UNKNOWN`, targetless or truncated rows, preserve that verdict. Check
  configured registry metadata, candidate coverage and relevant peers/engines.
  A stable same-line manual proposal is allowed in advisory mode when existing
  user authority covers it. Otherwise present a concrete proposal for approval.
  Never claim that fallback proves solver compatibility, security or Yellow.
- Source/policy mismatch, unavailable MCP, broad registry failure, exclusions
  and proven incompatibilities are not bypassed by this fallback.
- Repair diagnosed compatibility issues with bounded, evidence-driven attempts
  (default at most three per root cause unless the task specifies otherwise).
  Do not disable checks, remove validation or invent an out-of-scope restriction
  to get green. Explain alternatives when the repair budget is exhausted.

## Delivery and evaluation integrity

Record the contract version and hash of the **actually loaded** Skill when the
client exposes its text. Source hashes from MCP and the install receipt identify
expected/on-disk files; neither proves what a running client loaded. Mark loaded
identity `UNKNOWN` if it is unobservable. Evaluate a revised rule only after its
loaded content is confirmed; preserve an active run's old identity and history.
For a rule in a reference, record that reference's loaded hash as well; matching
SKILL.md alone does not prove that the revised reference was consumed.

In a blinded comparison, read only the assigned slot and shared frozen inputs.
Do not inspect another arm's manifests, installed versions, reports or usage to
fill gaps. Missing local evidence stays unknown. Keep retries and post-review
repairs distinct from the original result.

## References

- `references/goal-and-migration-evidence.md` — explicit goal route, conditional
  official references, syntax-only limits and separate loaded-content evidence.
- `references/blueprint-contract.md` — requests, compact completeness, workspace
  coverage, pinned/advisory semantics and targeted recovery.
- `references/preflight.md` — baseline, scope, authority and checkout selection.
- `references/execution-policy.md` — decision records, alternatives, application
  and bounded compatibility repair. Load its security-patch range-exception
  procedure before proposing an out-of-range transitive override/resolution.
- `references/verification-policy.md` — final-state install evidence, targeted
  runtime checks, warning attribution and honest statuses.
- `references/evidence-report.md` — minimal acceptance record and JSON format.
- `examples/plan-only.md` — an advisory plan without edits.
- `examples/yellow-exclude-react.md` — execution with a protected package.
- `examples/blocked-upgrade.md` — hard blocker, capped search and manual fallback.
