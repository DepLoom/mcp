# Exact functional-maintenance goal boundary (v1)

For the current combined route, supported matrix and acceptance limits, read
[Goal execution support](GOAL_EXECUTION_SUPPORT.md). The v1 increment below
retains its original planning-only boundary.


`get_goal_plan` is a separate read-only proposal tool. Existing
`get_upgrade_plan` and its Yellow/health defaults are unchanged. The new tool
requires explicit `goalKind: functional-maintenance`, `healthPolicy: REPORT_ONLY`,
exact stable versions, authorized mutable/immutable/excluded names, mandatory
check identities, and a policy digest. V1 does not authorize source repair or
partial acceptance. The separately explicit `baseline-observation` profile has
no mutable packages and can only bind already-matching immutable versions; it
cannot authorize a dependency or source edit. This keeps baseline-repair
observations distinct from a migration objective. Unsupported workspace/manager/
lock inputs fail closed.

The tool computes current source identity using the pinned Core snapshot owner,
hashes manifest and npm v2 lock bytes, checks the bundled Core inventory, and
binds actual Node/npm/Python and implementation identities. A bounded cancellable
child performs this read without registry access or project mutation. The exact
assignment is predetermined: no solver or candidate search is needed to bind it.
This does **not** prove that the assignment is installable or compatible.

Every response remains `PLANNING_ONLY`, `NOT_VERIFIED`, and `executionReady:false`.
Health REPORT_ONLY is a declared requirement policy; NOT_EVALUATED separately
records the absence of audit evidence. Existing hard policy prohibitions are
not waived. No legacy VERIFIED checkpoint/promotion is created. No caller JSON,
`passed:true`, or digest alone can establish execution authority.

Pure guards reject stale bindings, modified mandatory checks, unauthorized
manifest changes, wrong exact targets and changed held root lock versions.
The root-lock guard alone is **not** a transitive proof. The separate conservative
npm-v2 instance/edge guard allows target-exclusive dependency closure changes and
reuse of unchanged shared instances; rewrites of shared non-target instances fail
closed. Named immutable/excluded packages are protected across all added, removed
and changed instances in both lock views, including scoped/nested paths. Alias or
ambiguous identity fails closed. An unchanged held root may be reused only when
no globally protected instance is removed or modified.

The candidate lock's own edges do not prove published necessity: a changed lock
returns `DECLARED_LOCK_CLOSURE_ONLY` and `necessityVerified:false`. It requires
separate authority and verification; the necessary-only contract is not silently
reinterpreted as permission for arbitrary target-closure changes. This is neither
package-manager compatibility nor project-behavior proof.

`get_goal_report` reads an operator-managed `goal-runs/<run_id>.json` under
`DEPLOOM_MCP_STATE_DIR`, not an arbitrary caller path. It validates the expected
goal digest, recomputes the current source/toolchain/implementation binding and
checks local artifact hashes. All imported observations remain CLIENT_REPORTED
and NOT_VERIFIED, including a record claiming controlled authority or all checks
PASS. Required denominator and remaining work stay explicit; missing or changed
artifacts become UNKNOWN. A controlled execution receipt and full installed
project acceptance are still required to complete the vertical. Report-store
hashes alone never attest that commands ran.

Initial declared support: npm lock v2, one JS/TS package; Node/npm executable
layout currently validated on native Windows. No pnpm/yarn/monorepo claim.
There is no runtime repair API in this increment.

## Guarded verifier entry and separate goal admission

`verify_goal_candidate` accepts only run/candidate IDs, the expected binding and
an explicit `verify-private-candidate` operation. It does not accept project
paths, commands, assignments or proof files. Host/operator configuration selects
a manifest within the configured state root and its expected SHA-256; MCP
request fields cannot choose or replace that manifest. This is local
configuration identity, not a new credential or an attestation scheme.

Imported observation IDs, closed registrations, baseline-observation goals,
stale bindings, expired deadlines and changed command profiles are rejected.
A declared hard-policy PASS is not evidence: required hard-policy admission
currently has no connected provider and remains unavailable.

The pinned Core now exposes optional code-only `invocation_observer` events from
its actual supervised command branch, independently of reusable proof caches.
With an observer, deletion of private verifier workspaces is registered at
ownership acquisition and deferred until the callback has seen a preliminary
`INVOCATION_RESULT_OBSERVED`. This event does not confirm that the outer call
returned or that cleanup succeeded. Source bindings and guards may already be
closed; `beforePrivateWorkspaceDeletion` does not attest their active lifetime.
Default callers retain immediate cleanup and unchanged publication purposes.
Events distinguish EXECUTED_FRESH from REUSED and are not reusable goal proofs.

The code-only collector validates event sequence, target-only/source/resolved
identities and command provenance against the full registered command profile.
It owns a single outer verifier call and allows finalization/publication only
after that call returns without callback, identity or cleanup exceptions.
Preliminary callback data cannot confirm its own completion; failed calls poison
the collector. Its atomic observation writer uses a separate operator namespace
and refuses engineering-control publication. These mechanics
are tested in isolation; no new authoritative real-project receipt was produced.

Full-goal evidence closure and metadata necessity are still incomplete. The MCP
entry can run a pinned, host-authorized isolated candidate as described below;
full goal admission remains NOT_READY. Callback availability or execution
permission alone cannot enable acceptance automatically.
Core changes invalidate old bindings; historical proof is not rebound by renaming
hashes. No publication or legacy checkpoint gate is bypassed.

The code-only target-map bridge and its isolated test doubles establish wiring
(strict checks, intermediate role, one authorized assignment), not npm/project
execution. A separate regression exercises the actual Core manifest updater on
an owned synthetic manifest and confirms all43 held declarations remain intact.
Actual cancellation through the new boundary terminates its owned child tree and
preserves a checkpoint sentinel. These controls are not projects in the support
matrix and do not satisfy the end-to-end migration goal.

## Exact public ordinary-edge witness: no instance acceptance

`goal_published_edges` is an internal code-only helper, not an execution permission
or a new MCP request path. It reuses Core's bounded exact-version metadata GET;
planning packument caches never supply verification facts. Per-attempt memoization
is limited by registry/transport/name/version and explicit freshness/deadline.
Conflicting metadata or transport context, stale/unavailable source, wrong lock
integrity/resolved identity, aliases, peers, optional/bundled conditions and
unsupported formats remain UNKNOWN. Optional declarations overriding the same
ordinary dependency name are explicitly unsupported, never ordinary-edge PASS.

A published-path PASS binds canonical parsed JSON (not raw HTTP wire bytes),
source, fetch time, exact package versions and declared dependency ranges. Tarball
integrity identifies the published artifact; it is not a signature of metadata.
The changed instance must have a witnessed target path in the before/after
snapshots, and unchanged shared instances remain immutable under the existing
lock-scope guard. This does not prove minimality, hoisting or necessary placement:
`instanceNecessity` stays UNKNOWN, `fullAcceptance` and `executionReady` stay false.
Injected clients are always marked TEST_DOUBLE_CONTROL_FLOW_ONLY. Real public
HTTP controls are recorded separately; unavailable network evidence is not PASS.

Observed Core calls also treat thrown guarded-clone cleanup errors and nonempty
GuardResult errors/mutations at every cleanup site as failures. The default
legacy cleanup policy remains best effort. Successful command observations
cannot override failed resource cleanup.

## Host-authorized exact delta: code-only private apply

`goal_delta_trial` binds a complete before/after file and npm-v2 instance delta
to the current goal, real Core source snapshot, toolchain/implementation binding,
profile and candidate digests. Only `package.json` and `package-lock.json` can
change in this version; source/config repair remains unsupported here.

The existing operator-managed startup manifest may separately contain a strict
`deltaAuthorization`. The matching host policy must explicitly select either
ROOT_TARGETS_ONLY or EXACT_HOST_ENUMERATED. A verify-only or necessary-only
registration does not acquire these permissions automatically. The issuer
rechecks hard manifest/immutable/excluded/shared scope before creating an opaque
one-use capability; grant-shaped caller JSON is never a capability. An explicit
host grant authorizes the enumerated delta, not global minimality or correctness.
It cannot override a protected-package instance or unrelated shared rewrite.

Before private materialization the helper creates a durable run/candidate attempt
ID. The ledger is anchored to the canonical configured host state root, and the
host approval additionally binds that root's digest. An ancestor override or
changed configured root cannot relocate the ledger for the same pinned operator
manifest. Reloading another capability cannot repeat the operation.
Existing Core source capture, continuity and private materialization are reused.
The helper checks exact candidate bytes and the entire post-apply Core inventory,
then removes only its own private container. Cleanup errors, unexpected file
writes, stale bindings or deadline expiry prevent a TRIAL_APPLIED result.

Positive controls use ENGINEERING_CONTROL host registrations and real filesystem
materialization. They are not genuine end-user approvals, npm/project verification
or accepted matrix projects. PREAPPROVED_HOST_POLICY denotes the existing trusted
operator-policy boundary, not a proof inferred from metadata or an imported JSON
report. No real user grant has been issued by these tests. Production execution
admission remains NOT_READY; the installed `verify_goal_candidate` can run only
the separately configured engineering route described below. Actual approval
setup and real-project acceptance still remain to finish the vertical.

Metadata factory transport configuration is trusted local input, separate from
injected double clients. Aggregate edge observations explicitly distinguish
ENGINEERING_CONTROL and LIVE_SOURCE_CONFIRMED; a control PASS is not live registry
evidence. No further HTTP attempts were made during the host-delta stage.

## Installed engineering candidate verification

The existing tool accepts only registered IDs, binding digest and explicit
operation. Optional host-owned `candidateFiles` select bounded manifest/lock
files under the canonical operator state root. No MCP argument can select paths,
commands, candidate bytes or a grant. Production-policy grants remain gated.

For ENGINEERING_CONTROL only, the dedicated worker consumes the exact grant,
retains private workspace ownership, captures a distinct candidate Core snapshot,
and calls the real Core `verify_assignment` with the full registered strict
profile. Only owned seeded npm cache is allowed; npm is forced offline and install
scripts are disabled. Explicit Core `lifecycle_scripts=False` changes both actual
lifecycle argv/environment and the proof-bearing environment identity; legacy
callers retain lifecycle scripts by default. There is no mock verifier or
skip-install mode. Cache miss, unknown supervision and incomplete commands fail.

Baseline source identity and candidate verified-source identity remain separate.
The approved candidate lock must match the actual resolved lock byte hash; source,
profile, implementation and toolchain are rebound after verification. Candidate
capture may not discover an unrelated ancestor Git tree. The worker uses Git's
normal process-scoped discovery ceiling plus exact capture-root equality.

The preliminary observer cannot publish a receipt. `observationComplete` must be
true independently of the engineering/test-double authority label; fresh command
PASS alone cannot override an incomplete final observer event. Core outer return, its cleanup,
post-verifier inventory, original-source continuity and the private apply owner's
cleanup must all complete before the durable engineering result is returned.
A successful stdout/exit alone cannot substitute for the matching host receipt.
Result authority is ENGINEERING_CONTROLLED_VERIFICATION_OBSERVED, always
NOT_VERIFIED/fullAcceptance=false, never production approval or goal attainment.
The check commands cover the declared synthetic profile, not all real projects.

Owned baseline and candidate SourceSnapshot scopes request Core strict_cleanup.
A silent snapshot retirement failure blocks completion; shared/reused scopes do
not acquire ownership implicitly. The private trial receipt remains pending
until the baseline scope has also closed. Only then is the overall durable
engineering status finalized with allOwnedSnapshotCleanupConfirmed=true.

## v2 source-repair engineering increment

Schema v2 adds an explicit, bounded sourceRepairAllowlist (relative path and
before SHA-256). v1 still rejects source repair. Candidate source bytes come
only from the pinned host registry, never MCP request fields. The exact proposed
source bytes and after hashes participate in candidate/delta grants and private
workspace inventory checks. Windows path aliases, device names, traversal,
node_modules, Git internals and case-colliding paths are rejected.

This is an ENGINEERING_CONTROL increment, not delegated user-goal admission.
Named-check mapping and candidate source-origin engineering observations are
specified below. Verified baseline/repair lineage, production authorization and
durable final goal checkpoint/resume remain NOT_READY.
It must never promote fullAcceptance or imply that a real project passed.
The default shared/transitive lock scope guard is unchanged: npm relocating a
held consumer's dependency is currently rejected, even when its version and
integrity match. Such a rejection is unsupported scope, not incompatibility.
The S2 Windows portability variant is separately identified; its original
baseline failure remains visible and is not replaced by the prepared result.

## Declared artifact-preservation comparison

The read-only evaluate_npm_artifact_preservation function compares one explicit
root-target-old-artifact to nested-copy mapping. A separate versioned contract
binds semantic digests of both supplied manifests, both npm v2 lock objects and
the normalized goal. The exact contextual dev before/after values are explicit.
Actual source-file bytes and implementation identity must still be bound by the
existing host source/delta owners; semantic JSON digests are not file proofs.

The evaluator checks exact layout delta, both lock views, all reachable held
consumer declarations and effective declared artifact identities. Ordinary
missing edges and affected conditional contexts fail closed. Untouched bundled
members use an explicitly declared owner/relative-member identity, not invented
member SRI. Their dependency edges are still checked. Unchanged missing optional
or peer edges remain separately counted, not marked resolved.

DECLARED_HELD_ARTIFACTS_PRESERVED_ONLY is planning evidence. It grants no apply
capability, compatibility, publisher proof, physical resolution or goal receipt.
The existing default validate_npm_lock_scope remains strict and does not call
this evaluator as an automatic fallback. A bundled member's undeclared runtime
require can change behavior while this static comparison passes; independent
physical/project checks remain required.

For controlled tests using a user-approved registry mirror, independently
verified tarball/metadata consistency is distinct from publisher authenticity.
Mirror evidence must retain its actual source classification. Official origin
may remain UNKNOWN; it is a hard acceptance gate only when the user contract
requires that guarantee. Network denial does not authorize changing proxy,
credentials or transport identity, or substituting a different locked artifact.


## Versioned named-check observations

The host profile may use schemaVersion=2 with exact checkBindings. Every required
ID has exactly one command index/digest or a registered structured Core evidence
kind. Duplicate IDs/indices, repeated identical commands, unmapped commands and
wrong digests fail before execution. A compound command remains one observation.
Legacy profiles stay explicitly unmapped and never acquire goal-proof authority.
The collector rejects extra/duplicate command events and malformed event versions,
sequences, indices or identities; timeout/cancellation must be explicitly false.
A deadline expiring after successful cleanup is recorded separately from failure
to close the baseline scope. Neither case publishes accepted goal evidence.

An optional goal verificationContext binds the complete profile, check mappings
and INITIAL_BASELINE_IDENTITY_ONLY digest (source, manifest, lock, toolchain and
policy). This is initial lineage identity, not a successful baseline, BP1 repair,
parent checkpoint or actual user delegation. Changes require a new goal binding;
reauthorizing only the profile cannot retain an old contextual goal. Both v1 and
v2 goals can carry this context without expanding their execution permissions.

Host-relative oracle paths/hashes come only from the startup-pinned registry and
must remain outside the project. Missing or changed identity blocks admission;
the collector checks again before execution and observes identity afterwards.
Before/after equality alone cannot establish the bytes used throughout execution
or semantic applicability: oracleExecutedBytes and oracleApplicability remain
NOT_VERIFIED. No caller oracle file or command is accepted through MCP JSON.

Named command observations preserve actual Core freshness, invocation, guards,
source/assignment/resolved-lock identities and supervised command completion.
Structured continuity is recorded only after actual candidate-input and resolved
lock guards; post-PM source authorization is separately NOT_VERIFIED. Owned
cleanup is observed only after all outer owned scopes close. Shell output cannot
supply either structured observation. These remain engineering observations:
goalChecksComplete=false, fullAcceptance=false and no reusable goal checkpoint.
Normal user delegation, complete baseline lineage, immutable executed oracle
closure, post-PM authorized source proof and final goal admission remain NOT_READY.


## Materialized candidate source and inline Node

The host bridge enables Core observe_materialized_source_origin for its isolated
registered candidate. Other Core callers retain the default-off route. Existing
sealed SourceSnapshot entries and prepared-source seal entries are compared in
full, with file kind/path/size/SHA/executable identity and directories retained.
The normalized digest belongs to the prepared-source schema; raw source-manifest
keys from another schema are never compared directly. Unknown reparse/alias
representations are UNPROVEN, not removed from the comparison. Existing command
clone validation, source/dependency guards and supervised execution remain required.

A self-consistent prepared tree is insufficient if PM changed approved source.
Origin failure is UNKNOWN and cannot become a dependency incompatibility nogood.
Opt-in never publishes reusable project proofs or creates command checkpoints,
even when a caller supplies proof_cache_dir and recovery_checkpoints. Existing
resolver/preparation owners retain their independent policies. Inline command
length is capped before Base64 decoding, including direct Core callers.
The receipt and digest join actual command events, resolved state/lock and exact
candidate input. Project-proof and command-checkpoint hits are not counted as
fresh commands in this opt-in route. Core preserves manifest bytes for no-op
assignments instead of silently reformatting the already approved candidate.

An oracle may use mode=INLINE_NODE_V1 with protected hostPath, sha256 and exact
bodyBytes (1..2048). The host file is the existing startup registry's catalog
entry; no new caller-provided file/code/catalog field enters MCP JSON. Registry
validates exact UTF-8 bytes, size, digest and canonical Base64 wrapper. Core
recognizes only that wrapper, builds direct Node argv without a shell, records
the body digest and checks the selected executable hash before/after invocation.
Active NODE_OPTIONS/NODE_PATH loaders are unsupported by this inline closure.

DIRECT_ARGV_BODY_OWNER_OBSERVED is historical execution input evidence. The host
catalog's later identity is reported separately: changing that file cannot
change the literal body already bound into argv. Applicability, arbitrary
external module/asset input closure and production goal authority remain
NOT_VERIFIED. Existing external-file oracles retain their identity-only mode.
These observations never set goalChecksComplete or fullAcceptance to true, and
do not create a reusable goal checkpoint or authorize applying user-source bytes.


## Normal host-authorized isolated execution

The existing startup-pinned operator registry may explicitly register
PREAPPROVED_HOST_POLICY for an exact candidate/delta/source/profile/state root.
Normal execution requires candidateFiles, profile v2 with complete checkBindings,
and the goal verificationContext. Legacy unmapped profiles remain engineering-only.
The existing opaque single-use delta capability authorizes private execution;
MCP requests still supply only IDs, expected binding and explicit operation.
Normal apply-only calls without the owning verifier action are refused.

The durable attempt is acquired before materialization. Duplicate/restart IDs,
including interrupted STARTED attempts, cannot implicitly spend another attempt.
The parent reloads current pinned registration and verifies the closed durable
receipt rather than trusting stdout success. Engineering and normal origins cannot
be interchanged. Hard health/security policy without a provider stays denied.
Execution remains offline with strict peers/engines and lifecycle scripts disabled.

HOST_AUTHORIZED_PRIVATE_VERIFICATION_OBSERVED separately reports
privateExecutionAuthorized, privateExecutionAttempted and
privateVerificationObserved. It confirms a historical isolated candidate only;
the user's workspace remains unchanged. Without the explicit scoped admission
extension below, candidateProfileAccepted=false and goalChecksComplete=false.
fullAcceptance=false and currentWorkspaceVerified=false
and checkpointStatus=NOT_PUBLISHED remain explicit. PROFILE_ADMISSION_NOT_READY
identifies the missing installed held/transitive obligations and normative oracle
applicability/input scope. No reusable goal proof or verified checkpoint is created.

A successful initial baseline is not a prerequisite to authorizing repairs or a
future full-green candidate profile. Initial identity remains distinct from
baseline execution evidence. Comparative nonregression claims still require an
independently executed, hash-bound baseline under the agreed profile. Final apply
and verification of applied bytes remain separate from private candidate checks.


## Explicit historical scoped candidate admission

An optional profile-v2 admission contract binds a normative purpose, expected
outcome and allowed source/dependency/oracle inputs for every required check ID.
Its entire bytes participate in verificationContext and host authorization.
allowedChangeScopeDigest identifies the normalized semantic goal excluding only
verificationContext, avoiding a circular profile/goal hash dependency. The exact
candidate/delta is independently authorized by the existing opaque host grant.
Profiles without this extension retain their previous non-admitted behavior.
Engineering/imported observations cannot acquire normal scoped acceptance.

Core compiles obligations from the initial and approved same-format npm-v2/v3 locks, the exact
changed-instance list and protected names BEFORE reading installed results. It
checks physical instance paths, canonical package/version identity, applicable
manifest dependency metadata and supported ordinary/peer resolution edges.
Nested instances remain separate obligations. Peer lookup starts at the parent
package context; ordinary optionalDependencies override ordinary dependencies.
An extra, missing, changed or ambiguous instance never reduces the denominator.
Missing optional packages require an explicit supported omission rule; absence
alone does not supply one. Unproven links/layouts and unsupported edge selectors
remain UNKNOWN, never solver incompatibility or permission to change scope.
The existing MCP declared-delta gate remains independent and conservative. Ordinary semver edges must also match canonical package names; an implicit alias is never inferred. Unsupported bundled records and malformed link/bundle flags fail closed.

For npm-v3 only, an explicit host profile may select
`NPM_V3_SRI_PLATFORM_ABSENCE_V1`. The compiler and physical owner independently
verify the actual Node platform/architecture/executable hash and each supplied
archive against the exact lock SRI. Present lock platform declarations must
match the archive; absent fields may be enriched from its package manifest.
The initial supported host is Windows. OS/CPU exclusion and all incoming edges
being optional are required; malformed, contradictory or applicable selectors,
required incoming edges and unverified evidence fail closed. Every locked
instance remains in the denominator: installed plus individually proven
platform-inapplicable instances. Unrequested presence also fails. Optional peers
may be absent only under this explicit profile with exact optional peer metadata
and a valid range; present peers are always checked. Default empty rules remain
strict for both lock formats. This is boundary evidence, not continuous payload
attestation, npm full-tree resolution, project runtime or full acceptance.

Fresh scans run after preparation, immediately before/after every command, and
at the final prepared-tree boundary. The Core result and each command event bind
the same compiled plan, approved lock, source origin and observed tree digest.
This new opt-in uses the existing full-copy command materialization rather than
junctions; legacy fastpaths stay unchanged. Guards respect the attempt deadline
and fail closed at bounded instance/depth/manifest-byte limits. These are evidence
limits, not a reason to remove projects from the declared acceptance matrix.

The named isolatedCandidateSourceContinuity observation is tied structurally to
the authorized delta, baseline/candidate keys, Core origin and resolved lock. It
means PRIVATE_APPLIED_CANDIDATE_TO_PM_PREPARED_ONLY. The legacy
postPMSourceAuthorization=NOT_VERIFIED field remains unchanged; neither field
grants final workspace authority.

This is physical instance-manifest and supported-edge evidence at command
boundaries. It is not continuous mutation detection, package-payload integrity,
publisher authenticity, .bin entrypoint integrity or proof of arbitrary Node import behavior. Lock integrity
and resolved fields remain metadata bound to the approved lock. Existing source,
dependency, command-supervision and cleanup owners retain their own guards.

The trusted host selects the normative check scope; Core observes execution.
Normative scope is HOST_PINNED and fulfilled outcomes are SCOPED_OBSERVED.
Arbitrary JavaScript semantics remain NOT_AUTOMATICALLY_PROVEN and declared
external-input absence remains HOST_DECLARED_NOT_ENFORCED. The system does not
universally detect a newly approved useless test. A different always-zero oracle
cannot inherit an old command/body/profile binding. A host file hash before and
after execution is insufficient to establish executed oracle bytes; the current
new-admission oracle route requires the pinned direct-argv inline body.

After all required fresh command/boundary observations, current registry checks,
exact source/lock continuity and both owned cleanup scopes are confirmed, the
bridge and parent may return HOST_AUTHORIZED_SCOPED_CANDIDATE_PROFILE_ACCEPTED.
candidateProfileAccepted and goalChecksComplete then refer ONLY to that historical
isolated candidate under the agreed profile. fullAcceptance remains false,
currentWorkspaceVerified remains false and checkpointStatus remains NOT_PUBLISHED.
The user's original workspace is unchanged. A real final apply, verification of
applied bytes and durable verified checkpoint/resume are still required to finish
the user goal. A synthetic admitted candidate is not an accepted real-project row.


## Authorized final delivery and checkpoint

The isolated candidate boundaries described above remain unchanged. A separate
`apply_goal_candidate` operation now supports host-authorized final delivery for
ordinary Windows NTFS standalone npm-v2 projects. It requires a closed accepted
private receipt and an independently pinned final capability. MCP callers supply
only registered IDs, the binding digest and the explicit operation; commands,
paths and proof claims remain host configuration.

The final route retains source preimages and existing `node_modules`, stages an
offline frozen install with lifecycle scripts disabled, observes physical
obligations and executes the pinned checks in the registered workspace. Only
successful live checks, physical durable-source opening and pointer publication
produce `VERIFIED_PUBLISHED` for the selected profile. This does not establish
full-goal completion, arbitrary JavaScript semantics or payload authenticity.

`get_goal_checkpoint` distinguishes historical verification from current source,
dependency-tree, toolchain and environment continuity. The current incumbent
must match the complete typed publication target, including its generation and
attempt identity; matching only checkpoint ID and digest is insufficient.
Reading a completed checkpoint does not execute project commands or renew its
execution budget. A subsequent failed attempt preserves the previous checkpoint
and pointer; it does not automatically roll back partially delivered live files.

## Exact shared-instance authorization

Shared lock rewrites remain rejected by default. The pinned operator policy may
now enumerate an exact npm-v3 top-level shared-instance transition in
`sharedInstanceTransitions`; it is not an MCP request parameter. The goal binds
the actual host policy digest. Core independently compares complete before/after
lock identities, record/artifact digests, incoming edges and unchanged affected
owners. A changed exact target must introduce a range excluding the old version.
Held direct roots and global immutable/excluded names cannot be overridden.

Every transition requires a named mandatory pinned inline impact check covering
its package and held owners. Both proposal construction and grant issuance rerun
these checks. The comparison only admits declared scope: it does not establish
published metadata, physical installation or runtime compatibility. Those owners
and fresh profile observations remain required. Current support is deliberately
limited to v3 top-level non-alias shared instances and unchanged held consumers;
other layouts fail closed. No result from this comparison publishes a checkpoint.

## One-install frozen private verification

A host-pinned policy may explicitly select
`privateVerificationRoute=FROZEN_EXACT_SINGLE_MATERIALIZATION_V1`. It requires
normal host authorization and a complete exact candidate/validation profile.
The existing private delta owner still checks source, policy, root/shared scope
and approved bytes. The existing Core workspace verifier owns one strict offline
scripts-OFF npm-ci in the empty private copy, then the full physical graph and
fresh commands. It does not call the assignment resolver or Z3 to select versions.

The receipt preserves raw Core CURRENT_WORKSPACE observation semantics inside
an explicitly private candidate scope. No legacy assignment-verifier events or
final/current-workspace success are fabricated. Both the parent and final grant
owner validate the typed receipt, named checks, profile, identities and cleanup.
Historical private profile acceptance is separate from final delivery/checkpoint.

Physical scanning classifies only actually empty, unlocked, unscoped dot
folders directly inside a node_modules directory as auxiliary non-packages.
Scoped slots, ordinary empty package slots, nonempty folders, links/reparse
objects and missing expected instances remain unproven. Actual auxiliary
filesystem observations are recorded separately from the package graph; they do
not change its denominator or imply arbitrary JavaScript filesystem equivalence.
Source dist remains guarded. Package payload and .bin integrity remain unattested.


## Receipt-observed auxiliary preparation before final payload seal

An optional pinned host policy may set `finalAuxiliaryPreparation` to schema 1,
mode `PRESEAL_OBSERVED_EMPTY_AUX_V1` and an explicit bounded list of paths.
This is preparation authority, never an automatic allowlist of all observed
folders. The final owner requires a newly accepted frozen private receipt bound
to the same implementation, source lineage, graph, policy and check profile.
Only observed empty unscoped non-package leaf directories may be created in the
owned dependency staging tree, after strict install/physical verification and
before its first full payload seal. Existing parents must be ordinary contained
directories; locked slots, links/reparse, traversal and nonempty entries fail.

The preparation is recorded as a filesystem mutation, separate from package
graph identity. Full tree digest/switch checks remain unchanged. Actual current
commands and physical/source checks establish their own result, including the
prepared empty layout and the exact permitted observed auxiliary set. There is
no post-command reseal or blanket cache exclusion. Pre-existence can affect
arbitrary JavaScript, so private/final behavioural equivalence is not claimed.

## Versioned host-owned project execution environment

An explicit pinned policy may select
`projectExecutionEnvironment=HOST_OWNED_PROJECT_EXECUTION_ENV_V1` with the frozen
private owner. The shared Core boundary creates an immutable effective child
mapping and removes exactly `CODEX_TOOL_CALL_ID` before owner-controlled commands
receive it. The host process environment is not modified. All other names and
values remain identity-bearing in the v2 proof, including other CODEX names,
PATH, loader, registry/proxy/cache settings and missing versus empty values.
Windows name collisions fail closed; explicit owner overrides are normalized.

`project_execution_environment_v2` fingerprints the effective mapping and tool
implementation, and records explicit-child-mapping scope. Current queries derive
the same mapping from their current host inputs, never load old secret values.
Legacy v1 receipts retain their original semantics and cannot be promoted by a
v2 policy. The physical/source/check/lifecycle requirements are unchanged.

The actual v2 workspace owner currently supports local Windows paths only.
POSIX supervision injects a separate process token, and UNC launch adaptation
can inject variables; those boundaries need their own evidence before v2 support.
Native Windows Job/PID ownership and timeout closure are independently exercised
without the removed transport ID. This is not an arbitrary descendant/lifecycle
environment-closure guarantee. Neither raw environments nor per-secret hashes
are persisted or reported. Historical currentness failures remain unchanged.

## Exact held-artifact relocation permission

The trusted execution policy may opt into `heldArtifactPreservation` for one
explicit npm-v2 root-target artifact copied to a held consumer's nested path.
The goal binds the policy; exact before/after lock digests, both lock views,
unchanged held artifacts/declarations, enumerated context flag changes and the
complete held resolution mapping must agree. Default scope rejection remains.
Only the normal frozen verification route with a bound full-tree admission
profile and a hash-pinned impact oracle is eligible. Shared-instance transition
permissions cannot be combined with this narrow copy permission.

The return remains `DECLARED_LOCK_CLOSURE_ONLY`, compatibility NOT_VERIFIED and
necessity unproven. This permission does not establish published metadata,
physical resolution, installation, runtime success or current checkpoint status.
The existing owner must still bind exact source/manifest/lock bytes, execute all
required checks, verify consumer-relative Node resolution and complete cleanup.
Unsupported bundled or platform obligations still block execution; a preserved
bundle declaration is not physical bundle proof. Unknown evidence is not PASS.

## Unchanged bundled owner and explicit npm-v2 platform evidence

An optional normative `bundledOwnerRules` input is supported only for one exact,
unchanged npm-v2 bundled owner. The baseline lock and complete subtree digest,
owner/member records in both lock views, bounded safe SRI-matched archive and
member manifests are independently checked. Ordinary nested registry packages
require their own explicitly mapped archives; they cannot inherit parent SRI.
Generated .bin bytes remain in preservation scope, with origin NOT_ATTESTED.
Every member remains an instance/edge obligation. Compiler success is not an
install, runtime, source continuity or current-checkpoint receipt.

Explicit `NPM_V2_SRI_PLATFORM_ABSENCE_V1` rules additionally require agreement
with the legacy record and incoming requirements. Their kind must match the
admission lock format; archive SRI/platform selectors and the actual Windows
host are checked. Optional records do not disappear from the denominator.
The default bundle/unknown omission rejection remains unchanged.

Coverage output currently has no automatic source-identity waiver. A project
command returning zero can still fail the protected-input guard when it creates
unregistered reports. Any output relocation/configuration requires its own
pinned input contract; disabling coverage or ignoring entire report directories
is not a verification fix.

### Typed TAP/NYC external input (opt-in)

Admission requires the Core-owned `TRUSTED_PRE_POPEN_GUARD_V1` witness.
The code-only host guard executes after launch adaptation on the actual immutable
Popen arguments. Immediately before the pinned CI command it rechecks held input
bytes/IDs, actual env/CWD and empty output directories after installation.
The guarantee is `EMPTY_OBSERVED_AT_PRE_POPEN`, not exclusion of all possible
concurrent writes after that point. Only a successful native spawn can supply
the witness; missing, duplicate or mismatched witnesses fail closed. This
mechanism is fixture-tested; real S2 acceptance still needs its separate trial.

`PINNED_TAP_RCFILE_YAML_RESETTABLE_CHILD_V2` (policy/receipt schema 2) is one
Windows/NTFS input contract for a sealed
`npm run ci` command. It requires a normal preapproved host policy, the frozen
verification route and the explicit V2 child environment. The normative CI row
binds `{kind, recipeHash}` as its external input; other rows retain
`NONE_DECLARED_BY_HOST`. This does not enforce arbitrary JavaScript input closure.

The host pins original `.taprc.yaml` bytes and an append-only recipe of exactly
three output-only NYC arguments (temp/report/cache). Coverage, map, reporter,
selection, script and actual project CWD remain source-bound. Two separately
reserved private/final YAML and output sets bind concrete file/directory IDs.
The stable temp container is held throughout; NYC receives its fixed `data`
child, which must be absent at registration and native launch (even an empty
existing child is rejected). NYC may reset that child, never the container.
Report/cache roots and held ancestors span the actual Core verifier call.
After the supervised tree closes, final capture holds all output files and
directories through sealing and a second walk. A bounded complete manifest
binds relative paths, types, NTFS IDs, sizes and content hashes; reparse points,
hardlinks, missing/unstable objects and unexpected temp-container siblings fail
closed. Currentness physically reopens the same objects; identical bytes at a
new ID do not match. V1 policies/receipts are rejected, never promoted.
This attests the supervised command, not independent per-file writer provenance
or hostile concurrent substitution before first capture.
The actual native child environment/CWD and every required operation must match
the role-specific binding. Raw Core observations are kept unchanged; the host
input/output receipt is stored separately and cannot assert acceptance itself.

Each role has a single-use consumed marker, created before entering the verifier
and held during it. Exceptions consume the slot; resumption cannot regenerate
reports/config or silently retry that role. Missing/invalid LCOV, changed bytes,
file identities, ancestor identities, nonempty/shared output slots or incomplete
process closure fail closed. Checkpoint identity additionally hashes the host
receipt. Currentness reopens the real config/marker/output trees and checks their
physical bytes/IDs rather than comparing stored digests alone. Existing source,
physical dependency tree, scoped checks and supervision gates still apply.

This input mechanism and generated-fixture tests do not establish real S2
acceptance. The installed real-project trial remains separately budgeted and
must prove that pinned TAP/NYC actually consumed this configuration and that all
selected checks, impact guards and checkpoint/resume checks passed.


### Pinned executable tool-cache input (opt-in schema 3)

`toolCache.kind=PINNED_FIND_CACHE_DIR_TOOL_CACHE_V1` is a separate schema-3
policy, normative recipe and outer receipt. TAP/NYC V2 retains its meanings;
old receipts cannot be promoted. CACHE_DIR is injected into only the sealed CI
command's native subtree, through a narrow Core command-index mapping. Install
and other commands retain the base mapping. Native operation observations must
match the started Core CI witness and complete host input/recipe binding; a
claimed digest map alone is insufficient. Historical receipts retain the actual
mapping witnessed at execution. Currentness separately compares the fresh host
mapping, including changes to the operator registry. Ambient CACHE_DIR and conflicting/fallback values fail closed.

The existing held per-role NYC cache root is reused. It must start empty and
remain link-free. Supported importer/helper package versions, relative component
paths, package/entry/cache/transform source hashes and helper resolution are
pinned; actual read-only consumer handles span execution/sealing. Frozen source,
resolved dependency chain and independent physical tree checks still apply.
The complete output manifest includes executable generated JS. Only the pinned
`import-jsx/<32hex>.js` namespace and flat NYC `<basename>-<64hex>.js` shapes are
admitted; unknown namespaces are incomplete, never blanket .cache permission.
Currentness reopens consumer identities and output bytes; resume does not reseal.

This covers trusted pinned tools with an initially empty isolated cache. It
neither attests each intermediate cached-code read nor independent file-writer
provenance. The project node_modules/.cache is not waived. Actual helper/JSX
boundary fixtures are not real project acceptance or coverage evidence.

### Operator-bounded private execution time

An optional host policy `privateVerificationBudgetSeconds` permits integer
120..300 only for preapproved frozen scoped execution. Absence retains Core120s
and worker180s. Explicit values select the Core cap and worker cap+120s for
planning/cleanup; both remain bounded by the registered absolute deadline.
The effective Core start/deadline and configured caps are recorded as operational
metadata and must match the pinned policy. They are not correctness evidence.
A timed-out candidate worker cannot assert that the verifier never ran: its
invocation status is unknown, acceptance is false, and no retry is issued.
This allowance never extends an existing attempt, resets a consumed slot,
changes required checks or creates new execution authorization.

### Host-owned current proof resources

New host registrations may explicitly pin the versioned [current physical proof budget](CURRENT_PROOF_BUDGET.md). This resource allowance changes no project scope or acceptance checks. Legacy registrations remain unchanged. A selected current read uses a fixed killable read-only process; it is not an apply worker or a renewed execution grant.

### Terminal completed replay

An explicitly authorized repeat of the same sealed TERMINAL_PASS attempt uses
FINAL_TERMINAL_PASS_READONLY_REPLAY_V1 before worker creation or execution budget
checks. The existing authoritative completed reader verifies the exact grant,
checkpoint and fresh physical currentness. A stale or foreign result does not
fall back to execution. Reading never extends the original execution deadline,
creates a receipt, resets a slot or mutates the terminal journal. Missing or
in-flight attempts keep the ordinary permission and deadline checks.

A recovery reader and the original execution producer have separate provenance.
The original installed package, actual execution environment (including
PYTHONPATH), Core witnesses and receipts remain pinned. An explicitly inspected
read-only entrypoint may load a new dispatch while retaining the original
producer/authority package; its own hash must be recorded separately. It must
forbid normal execution and worker launch. This is not proof that a changed
full implementation matches the original execution identity, and never promotes
an old incomplete stage retroactively.
