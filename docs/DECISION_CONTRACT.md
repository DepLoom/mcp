# Planning decision contract (MCP 0.2)

The tool returns a bounded planning decision and references to its complete
evidence. It does not install dependencies, verify a migration, prove a joint
workspace assignment, or authorize new work. Blueprint schema version 2 gains
additive fields; existing planning-only statuses retain their meaning.

## Request and applied policy

`get_upgrade_plan` accepts the existing health policy and these parameters:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `target_mode` | `"pinned"` | `pinned` requires replanning before changing a proposed assignment. `advisory` permits a documented alternative within existing user authority, with independent compatibility checks. Neither mode authorizes execution. |
| `objective` | `"minimum-change-to-policy"` | Preserve the existing minimum-change health-policy behavior, or request `latest-compatible-in-approved-lines`. |
| `mutable_packages` | `null` | `null` permits all selected declarations to move; `[]` permits none. Packages outside the set remain constraints and remain in health accounting. This is different from `exclude`. |
| `approved_version_lines` | `null` | Map package names to allowed nonnegative integer major numbers. For the latest-compatible objective, an absent entry limits new candidates to the current major. An explicit empty array permits no new major; the current assignment remains available as a constraint. |
| `draft_max_candidates` | `3` | Positive integer in `1..128` controlling the Core security-evidence candidate search. It is not the static solver's domain limit and is independent of the deadline. |
| `previous_evidence_digest` | `null` | Optional lowercase SHA-256 from a previous response, for diagnosing unchanged published evidence. It is not a server-side cache key. |

The boundary validates these parameters before starting Core. The normalized
contract travels through `BlueprintPolicy.intent` / the existing
`DEPLOOM_BASELINE_INTENT_JSON` channel, using `targetMode`, `objective`,
`mutablePackages` and `approvedVersionLines`. Core publishes the applied
`decisionContract`; MCP checks its normalized value against the request.
Missing support for advisory mode, a new objective, mutable scope or approved
lines fails closed with `decision-contract-unsupported`. A differing echo is
`decision-contract-mismatch`. Default pinned legacy planning remains supported
and is explicitly marked `policy.coreContractSupport="LEGACY"`.

The Python boundary preserves legacy intent fields. In particular,
`policies: {package: "required"}` requires movement from current; it does not
name an exact target. Requiring movement outside an explicit mutable set is an
invalid intent. `exclude` remains an explicit health-scope exclusion.

`POLICY_SATISFIED` means the existing health objective is satisfied. It does not
mean a dependency is the freshest compatible candidate. The latest-compatible
objective remains a candidate-search policy, reported by
`objectiveEvidence.freshnessOptimality="NOT_PROVEN"` and
`physicalCompatibility="UNKNOWN"`. No change in objective turns a draft into
an installation or a runtime proof.

## Decision evidence and bounded projection

Full blueprints retain `conflicts`, `unknowns`, `unresolved`, migration notes
and solver artifacts. `decisionEvidence` adds typed records with `code`,
`severity`, `package`, `message`, `nextAction`, `evidenceRef` and `lifecycle`.
References are JSON pointers into the full Blueprint. Dependency rows retain
their reason, evidence flags, declaration identity and next action inline.

`CURRENT_ROW` identifies current row evidence. An explicitly active final
conflict has `ACTIVE_FINAL`; an old untyped conflict note has
`UNRESOLVED_LEGACY` and requires review. A legacy `COHORT_BLOCKED` string alone
does not prove an active blocker. `TYPE_STUB_REMOVAL` is a removal-proof review;
`PEER_FALLBACK` is a resolved-assignment review. Neither is silently converted
into an active peer blocker. Breaking changes and migration notes also set
`needsReview`; review can be completed under existing user authority. An
`EXCLUDED` declaration remains a resolved scope decision with
`needsReview=false` and `nextAction=KEEP_EXCLUDED_CURRENT`. Its associated
reasons remain visible. Active peer blockers and unknown evidence still affect
the overall plan; reconcile other allowed targets without silently reopening
the excluded declaration.

The compact response includes up to 100 rows, 50 groups, 50 package names per
group and 50 critical evidence records. Evidence is ordered with blockers
first. Long details are clipped at 512 characters with an explicit artifact
read marker. The serialized JSON payload is limited to 512 KiB; truthful
counts and artifact references survive list clipping. If the minimum payload
cannot fit, the tool returns `response-too-large`.

These fields answer different questions:

| Field | Question answered |
| --- | --- |
| `truncation.truncated` | Did the transport projection omit display content? |
| `decisionCompleteness.projectionComplete` | Are all decision rows, critical reasons and coverage references represented without clipping? Display-group clipping alone is not decision incompleteness. |
| `decisionCompleteness.requiresArtifactRead` | Must the caller read complete decision evidence before relying on the projection? |
| `decisionCompleteness.planningComplete` | Did Core publish `DRAFT_READY`, rather than a partial draft? |
| `decisionCompleteness.evidenceComplete` | Are metadata/security coverage counters known and complete, without reported candidate caps, unresolved evidence or legacy note uncertainty? This is completeness of reported planning evidence, not a physical compatibility proof. |
| `decisionCompleteness.blockerCount` | How many current/explicitly active blockers exist, including those omitted inline? |
| `decisionCompleteness.nextAction` | Read omitted evidence, reconcile workspace plans, resolve partial planning, reconcile constraints, review evidence or verify the assignment. |

An untruncated `DRAFT_READY` response may still have incomplete evidence.
Likewise, complete reported evidence does not prove installation, project tests
or runtime behavior. Callers must not treat `truncation=false` as a shortcut
around conflicts or review requirements.

## Workspace coverage

`workspaceCoverage` describes the actual planning scope separately from Core
input fingerprints. The current boundary requests one project manifest.
Bounded discovery recognizes package.json workspaces and Lerna package
patterns, including component-aware `*` and `**` patterns and exclusions. It
lists discovered, evaluated and unevaluated manifests. Malformed/unsupported
configuration, unreadable workspace subtrees, skipped symlinks or discovery
limits make discovery incomplete. Filesystem traversal errors are recorded in
`discoveryErrors`; an inaccessible directory cannot silently disappear from a
complete-coverage claim.

`mode="SINGLE_MANIFEST_PLANNING"` and `jointAssignment="NOT_COMPUTED"` are
explicit. A discovered workspace or incomplete discovery sets
`reconciliationRequired=true`, `workspaceGraphCoverage="PARTIAL"`, and the
next action `RECONCILE_WORKSPACE_PLANS`. Reading root/member manifests to hash
inputs does not mean their constraints were jointly solved. Separate root and
child plans must be reconciled before executing a shared-lockfile assignment;
a successful child plan is not evidence for an entire workspace.

Discovery is bounded at 2,048 visited directories and 256 manifests. It does
not follow directory symlinks, evaluate every package-manager configuration or
provide a complete transitive dependency graph. Paths are observations; the
identity layer converts selected manifest paths to logical workspace-relative
names. Artifact write guards remain scoped to the requested project boundary.

## Identities and actual provenance

| Field | Identity |
| --- | --- |
| `run.runId` | A particular execution, independent of its semantic result. |
| `hashes.plan` / `hashes.prompt` | Exact artifact bytes from Core. They may contain run-specific fields. |
| `source.semanticInputHash` | Core's normalized local-input snapshot, with `semanticInputsByProject` and `inputCoverage` retained in the full Blueprint. A legacy Core may return no semantic hash. |
| `identities.policyDigest` | Normalized health policy, exclusions, decision contract, acceptance policy and remaining legacy intent. |
| `identities.evidenceDigest` | Normalized published row evidence, reasons, known security/metadata coverage and per-project health. `evidenceScope="PUBLISHED_PLANNING_EVIDENCE"`; it is not a frozen registry/OSV snapshot. |
| `identities.decisionDigest` | Assignment, declaration identities, normalized reasons, policy, logical coverage and semantic-input identity, excluding run directories and clocks. |

`declarationId` includes logical manifest path, dependency kind and package
name. Different declarations of the same package are not the same identity.
Changing an output directory alone does not imply semantic drift. Equal
digests also do not prove deterministic replay: registry/security snapshots,
solver domains and runtime verification remain separate evidence.

`runtimeProvenance` reports the actual generator's SHA-256, Core Git revision
only when it belongs to that generator checkout, actual MCP version/code
digest and available bundle content hashes. The release manifest's expected
revision is kept under `expectedRelease`; it is never substituted for an
override generator's identity. Dirty worktrees are marked where Git can
observe them.

Skill source integrity is computed over the canonical manifest's listed
files, excluding incidental receipts and Python caches. It reports the source
bundle hash, declared hash, contract version and comparison. The server
cannot observe which Skill text an agent actually loaded:
`runtimeProvenance.skill.loadedClientContent="UNVERIFIED"`. Client load
receipts or captured loaded content are still necessary for that claim.

## Recovery from incomplete evidence

`planningOptions` separates the requested/effective candidate cap from the
deadline, identifies whether the cap came from Core's published manifest and
retains its evidence-search scope. `recovery` counts candidate-cap evidence,
suggests a bounded larger cap where possible, and compares a supplied previous
evidence digest as `NOT_PROVIDED`, `UNCHANGED_EVIDENCE` or `EVIDENCE_CHANGED`.

Increasing the deadline does not expand the candidate cap. An unchanged
evidence digest reports unchanged published evidence; it does not prove the
underlying registry was unchanged. Repeat planning when the relevant inputs,
policy, candidate cap or evidence changed. A missing peer, a workspace
reconciliation gap and a candidate evidence cap need different next actions.
At the public cap limit, inspect missing evidence instead of issuing an
unbounded retry. Full registry/OSV replay and joint workspace solving remain
outside this contract.

## Regression coverage

`tests/test_decision_contract.py` uses synthetic manifests and a fake Core,
with no real project migrations or registry access. It checks visible peer
evidence without row clipping, explicit legacy lifecycle, cap versus deadline,
transport bounds, source/declaration identities, provenance overrides,
workspace coverage, intent validation and the applied-contract handshake.
Existing boundary, schema and compact protocol suites preserve the earlier
isolation, cancellation, status and byte-counting contracts. Distribution and
real Core integration are checked separately.
