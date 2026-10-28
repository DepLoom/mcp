# Blueprint contract

Load when calling `get_upgrade_plan` or interpreting its evidence. The runtime
schema in `contracts/upgrade-blueprint.schema.json` is authoritative. This
reference covers additive schema version 2; older responses need their full
artifact and cannot supply missing coverage or authority evidence.

## Request and applied contract

Example with delegated version selection; use the user's actual objective and
scope rather than copying these values blindly:

```json
{
  "project_path": "/absolute/path/to/project",
  "target_level": "yellow",
  "target_mode": "advisory",
  "objective": "latest-compatible-in-approved-lines",
  "mutable_packages": ["example-utility"],
  "approved_version_lines": {"example-utility": [1]},
  "exclude": ["example-renderer"],
  "deadline_seconds": 20,
  "draft_max_candidates": 8,
  "include_prerelease": false
}
```

- `target_mode` defaults to `pinned`; `objective` defaults to
  `minimum-change-to-policy`. Send both explicitly. Advisory allows justified
  alternatives under existing authority; it never relaxes peers, engines,
  exclusions or user pins.
- `mutable_packages=null` permits planning changes to all in-scope packages;
  `[]` permits none. Frozen packages still supply constraints. An exclusion
  controls declared changes, not every nested occurrence in a dependency graph.
- `approved_version_lines` maps package names to permitted major numbers. For
  the latest-compatible objective, an omitted package stays in its current
  major; an empty list authorizes no new target line. This is a search boundary,
  not proof that all releases or source migrations were checked.
- `deadline_seconds` is a time budget; `draft_max_candidates` is a separate
  bounded candidate limit. Increasing one does not increase the other.
- Optional `registry`, `registry_metadata_ttl`, `min_lag_ok_pct`, and
  `output_dir` retain their tool meanings. Use the project's configured registry.
  MCP output/state directories must be outside the project. Planning reads the
  project at call time; it does not execute an installation.
- For a meaningful follow-up, `previous_evidence_digest` may carry the previous
  `identities.evidenceDigest`. This compares published evidence, not an entire
  registry snapshot or runtime result.

Compare the response's `policy.decisionContract` (`version`, `targetMode`,
`objective`, `mutablePackages`, `approvedVersionLines`) with the task. Also check
`policy.targetLevel`, exclusions and identity. `policy.coreContractSupport=LEGACY`
means the Core did not implement the full contract; do not assume that boundary
projection made the solver enforce it. A `policyDiscrepancy` requires resolving
the mismatch before affected execution.

## Compact decision evidence

`ok=true` means a planning response was published. `status.authority` remains
`PLANNING_ONLY`, `verificationStatus=NOT_VERIFIED`, compatibility `UNKNOWN` and
parallel execution safety `UNPROVEN`.

| Field | How to use it |
|---|---|
| `dependencies` | Read `status`, target, `reason`, `evidence`, `nextAction`, `evidenceRef`, and review flags together. |
| `decisionEvidence` | Typed `code`, `severity`, package, message, next action and artifact reference. `BLOCKER` is decision-critical; `REVIEW` is unresolved evidence, not automatically a hard conflict. |
| `decisionCompleteness` | Check `projectionComplete`, `requiresArtifactRead`, `planningComplete`, `evidenceComplete`, critical evidence counts, blocker count, workspace reconciliation and next action. |
| `workspaceCoverage` | Manifest discovery/evaluation inventory, unevaluated manifests and reconciliation requirements. `SINGLE_MANIFEST_PLANNING` plus `jointAssignment=NOT_COMPUTED` is not a joint workspace solution. |
| `artifacts.blueprint` | Full evidence when compact fields are insufficient, ambiguous or truncated. Preserve the exact JSON reference with the decision. |
| `objectiveEvidence` | `freshnessOptimality=NOT_PROVEN` and `physicalCompatibility=UNKNOWN` remain limits of planning evidence. |
| `identities` | Semantic input, policy, published-evidence and decision digests; their scopes differ from raw artifact hashes and final installation checks. |
| `runtimeProvenance` | Actual generator/MCP/source Skill identity when observed. `skill.loadedClientContent=UNVERIFIED` is not upgraded by a source hash or installation receipt. |

`truncation.truncated=false` only describes displayed rows. Read full evidence
when `requiresArtifactRead=true`, critical counts differ, a reason/ref is missing,
or a contradiction cannot be resolved. `projectionComplete` does not prove
planning or evidence completeness. Conversely, a complete compact response can
be sufficient; do not open every artifact mechanically.

For conflicts with `lifecycle=UNRESOLVED_LEGACY`, inspect the referenced original
constraint and final row. A legacy note can outlive the proposal it concerned;
do not call it a current blocker without applicability evidence. Likewise, do
not dismiss an explicit current child blocker because the root target exists.
Reconcile shared root/child assignments before applying affected targets. Peer
absence in one child manifest alone does not prove workspace incompatibility.

## Status and authority

| Row status | Decision meaning |
|---|---|
| `PROPOSED` | A planner target to evaluate under the task contract; no runtime guarantee. |
| `POLICY_SATISFIED` / `NO_CHANGE` | No policy-driven change proposed; this does not establish the freshest compatible target. |
| `EXCLUDED` / `DEFERRED` | Respect the recorded exclusion/deferral and task scope. |
| `BLOCKED` | Inspect the constraint and applicability; resolve a proven conflict before affected execution. |
| `CANDIDATE_SEARCH_TRUNCATED` | Search was bounded; no inference that a suitable target cannot exist. |
| `UNKNOWN_METADATA` / `UNKNOWN_SECURITY` / `PEER_UNRESOLVED` / `NO_TARGET` / `NOT_ATTEMPTED` / `INTERRUPTED` / `UNKNOWN` | Preserve uncertainty and follow targeted recovery; do not manufacture a planner proposal. |

`needsReview` requires a recorded decision supported by evidence. Existing user
authority may already cover it; it does not mechanically require another
question. In advisory mode, an agent may propose or apply a qualified manual
alternative inside authorized bounds, retaining the original planner verdict,
evidence gaps and target origin. In pinned mode, a different target needs an
explicit revised-target decision. See `execution-policy.md`.

## Recovery and source identity

`recovery` reports the candidate limit, cap evidence count, previous-evidence
comparison and next action. `UNCHANGED_EVIDENCE` means no published evidence
changed; a longer deadline cannot fix a deterministic candidate cap. Change the
binding constraint or gather missing metadata before retrying. Cap widening is
useful only where permitted and plausibly addresses the recorded search limit.
Repeated unchanged requests do not increase confidence.

`DRAFT_PARTIAL` and `partial.replanRequired` must remain visible. Independent,
proven portions may proceed only when the task permits and their constraints do
not depend on unresolved scope. Source/policy mismatch, hash mismatch, global
registry failure, missing MCP and proven conflicts cannot be bypassed by a
manual-version fallback.

`source.inputFiles` identifies the files read; `source.inputHashes` contains Core
combined fingerprints keyed by project, not per-file SHA-256. Preserve it as
published. Record your own declared path/hash scheme for checkout identity and
final-state verification. Re-plan after meaningful changed constraints, scope,
registry or rejected targets; do not re-plan after each accepted edit merely
because applying the decision changes its inputs.

For `ok=false`, distinguish invalid request/path/policy from infrastructure,
artifact/schema integrity and generator errors. Correct the actual cause.
`generator-timeout` may justify a larger deadline only with time-budget evidence;
`generator-failed` is not automatically a timeout. Never call an error a valid
plan or infer MCP duration from a tool wrapper's wall time.
