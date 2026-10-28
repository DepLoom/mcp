# Execution policy

Apply changes only in `EXECUTE` mode and the authorized working copy. Reuse a
supplied isolated checkout; otherwise prefer isolation and prove source identity.
Never discard unrelated changes or publish/delete the work without authority.

## Decide before applying

Read source/policy identity, workspace coverage, conflicts, unknowns and recovery
actions, even when all dependency rows fit in compact output. Reconcile shared
assignments across root and children; a root proposal cannot overrule a known
child constraint. Incomplete workspace coverage is not a whole-project verdict.

For each affected package, distinguish:

| Kind | Treatment |
|---|---|
| Hard constraint | Preserve proven peer/engine constraints; resolve or re-plan a contradiction. User approval does not prove compatibility. |
| User requirement | Preserve exclusions, explicit targets, version lines, scope and behavior. Ask only for a necessary new decision. |
| Planner recommendation | In pinned mode keep the agreed target; in advisory mode justify an alternative inside authority. |
| Search/evidence limitation | Retain UNKNOWN, missing coverage and candidate caps. Expand the relevant search or make a separately attributed proposal. |

Write a decision record: package and declaration scope, original verdict,
planner target, selected target, `targetOrigin`, reason, evidence references,
authority reference and required verification. `needsReview=true` does not
automatically require another question when the user already authorized this
kind of change. Breaking-change notes are evidence for necessary adaptations,
not new permission rules by themselves.

## Pinned, advisory and manual fallback

- **Pinned:** apply agreed targets exactly. A stale/contradictory target requires
  re-planning or an explicit revised user decision; never obey it blindly or
  silently pick a different version. Record the superseded plan and new target.
- **Advisory:** choose a justified target inside approved lines/scope using
  current configured-registry evidence and relevant compatibility checks.
  Record deviations from the planner. Do not claim Core validated the choice.
- For a targetless/UNKNOWN/capped row, preserve the planner status. Check stable
  candidates, peers/engines, known security evidence and API/support notes.
  A same-line fallback may use existing authority; otherwise prepare a concrete
  proposal before asking. Broad peers do not prove runtime compatibility.
- Do not infer latest from `POLICY_SATISFIED`, or incompatibility from a candidate
  cap. Do not infer mandatory family synchronization from the peers of a new
  companion when deciding whether the old companion must change. Ordinary
  dependencies can have separate nested copies; a direct-version mismatch alone
  is not proof of a peer-resolution failure.
- Source/policy mismatch, unavailable MCP, exclusions and unresolved hard
  constraints are not bypassed by fallback. Independent decisions may continue
  only when their own graph and authorization are established.

## Apply and repair

1. Retain baseline identities and the selected decision set before editing.
2. Edit permitted declarations, including justified companions. Every changed
   declaration maps to a planner or authorized manual decision, not necessarily
   to a `PROPOSED` row. Count changed declarations and unique names separately.
3. Update the lockfile with the project's package manager. Confirm installed
   versions in each relevant workspace; do not treat a root copy as every copy.
4. Preserve excluded direct declarations and their resolved versions. If the
   user also froze transitive copies, audit that stronger condition separately;
   the planner's direct exclusions do not guarantee it automatically.
5. Diagnose install/compiler errors before choosing a remedy. Required source
   annotations, exports or API adapters in previously untouched files are in
   scope when the migration authorizes them. An unmodified file is not a reason
   to label an otherwise repairable migration impossible.
6. Keep at most three evidence-driven repair attempts per root cause by default.
   Retry only when evidence or inputs changed. Do not remove validation, disable
   checks, add blanket `any`, lower thresholds or invent a new scope rule for a
   passing result. If repair cannot be completed, report the actual failure and
   a concrete alternative within the user's fallback instructions.
7. After any changed target, manifest, lockfile or environment, invalidate older
   install evidence for the final state and re-run the required clean install.
   After source/config changes, refresh affected checks. Retain earlier failures.

Re-plan when meaningful planner inputs/constraints change, when the selected
assignment conflicts across workspaces, or when a pinned decision is no longer
applicable. Do not re-plan after each expected edit in an accepted transaction;
finish its bounded application, audit the resulting state, then re-plan if the
remaining decision set is no longer valid. Increasing a timeout without changing
a candidate cap is not a recovery for truncated candidate search.
