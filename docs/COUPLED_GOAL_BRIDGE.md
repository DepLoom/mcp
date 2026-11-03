# Finite coupled-goal bridge v3

This is an opt-in extension of `get_goal_plan`. Exact v1/v2 remains a cheap
identity-binding route with no combinatorial solve. A v3 parent uses
`goalKind: coupled-maintenance`, explicit stable-version `candidateDomains`,
`nodeVersion`, and objective `PRIMARY_VERSION_THEN_MIN_CHANGES_V1` with
`primaryPackage` and `maxMajorChanges`. Partial acceptance is unsupported.
All existing checks, verificationContext, immutable/excluded scope and optional
hash-bound source repair allowlist remain part of the parent and derived child.

## Supported model and objective

The Core-owned `peer_solver_goal.py` calls existing production peer/environment
extraction and the unchanged Z3 backend. This opt-in route checks current/current
peers and Node engines too; it does not inherit the legacy grandfather exemption
or ambient baseline-intent globals. Every declared root is included. Roots outside
the explicit mutable domain have singleton current-version domains.

The objective maximizes the primary package's SemVer rank, then minimizes changed
root count, then prefers lower versions in sorted package-name order to break ties.
It is a separately versioned goal objective, not the health planner's eight-tier
objective. The major-change budget compiles into generic forbidden conjunctions
inside the Core model BEFORE solving. It is not a post-filter of an unconstrained
claimed optimum. Limits: 32 mutable names, 256 total roots, 32 stable versions per
domain, 4096 budget conjunctions, 10-second solve. Unsupported metadata/ranges or
limits fail closed. Only a complete `optimal` Z3 result with consistent assignment
and score can yield a derived intent. Timeout/UNKNOWN/UNSAT produce no intent.

## Input, selection and execution identities

The operator places `HOST_PINNED_ROOT_METADATA_V1` JSON bytes at
`DEPLOOM_MCP_STATE_DIR/choice-inputs/<sha256>.json`. The parent supplies only that
digest, never an arbitrary path or metadata/proof JSON. The artifact contains
schemaVersion, kind, createdUTC, expiresUTC, basePolicy and records by package and
exact version. Maximum freshness window is 24 hours; all domain versions and held
roots require name/version-matching metadata. There is no network acquisition in
this increment. The operator must establish source provenance separately.

The worker reobserves source/manifest/lock/toolchain/implementation around solving,
then writes a content-addressed `HOST_PRODUCED_ROOT_CHOICE_V1` receipt under
`choice-results`. It contains the parent binding, current root inventory, complete
assignment, score and model digest. `derivedExactIntent` preserves the parent goal
and selection digests through a versioned `policy.coupledChoice` link. Its child is
an ordinary exact v1/v2 goal with a NEW policy digest that binds that link.

A separate host-owned `choice-issuance/<selectionDigest>.json` ledger records the
actual Core invocation result and operation ID, bound to parentBinding, model,
assignment, score and implementation. Its digest also enters `policy.coupledChoice`.
The worker alone issues it after a validated OPTIMAL result. Missing/mismatched
issuance blocks admission even for a schema-valid receipt; imported JSON cannot
supply provenance by declaring its own HOST_PRODUCED kind. A repeat preserves the
original issuance record. This remains the existing trusted-filesystem boundary,
not cryptographic hostile-writer attestation.

The central host registry reconstructs the model, checks membership, all original
constraints, held roots, score, parent binding and exact derivation before any
materialization. It consumes an optimal receipt produced by the trusted local
worker; it does not rerun Z3 at every admission. The configured state filesystem
and execution manifest are the existing trusted operator boundary. These hashes
are NOT signatures or hostile-writer attestation. Imported reports or client PASS
fields must never be installed as host selection receipts. A new independently
authorized exact goal is distinct from a derived coupled intent.

The policy and child binding already flow through candidate/final authorization.
Coupled checkpoints additionally retain `selectionLineage`. Legacy exact
checkpoint payloads remain unchanged. A derived intent itself grants no execution
capability: the separately pinned host manifest, exact candidate/profile,
permissions, delta admission and actual independent checks are still required.

## Proof boundary and current acceptance

`OPTIMAL_DECLARED_ROOT_IR_ONLY` is NOT full npm transitive placement, install,
health/security evidence, build, source compatibility or runtime verification.
Responses remain `PLANNING_ONLY`, `NOT_VERIFIED`, `executionReady:false`; full-tree
status is NOT_EVALUATED and installation/runtime are NOT_RUN. The receipt does not
replace those checks or lower Yellow/security policy.

Engineering acceptance uses a synthetic three-root fixture with six candidate
combinations, four model-feasible combinations, and an independently enumerated
expected optimum. It includes real bundled Core/Z3, installed-wheel stdio MCP,
central host-registry admission and negatives for metadata, partial/UNKNOWN,
retargeting and dropped lineage. No real-project coupled acceptance or speedup is
claimed here. A separately preregistered real S3-copy test is still required.
## Named and normative profiles

When the parent includes verificationContext, the host must also place canonical
profile JSON at `choice-profiles/<profileDigest>.json`. It is separate from the
metadata artifact to avoid a scope/metadata/profile hash cycle. The parent profile
is validated against its original parent policy and baseline identity.

The derived intent returns executionProfile. It preserves all commands, mappings,
required outcomes, normative inputs and permissions. Only the admission's
allowedChangeScopeDigest is rebound to the derived exact scope; verificationContext
is recomputed for the derived policy identity. Admission reconstructs this exact
transformation from the separately pinned original profile and rejects any other
change. The operator must pin the returned exact goal AND executionProfile in the
normal execution registry; returning them does not authorize execution. Missing
original profile fails closed. Normative-profile rebind and changed-command
negatives are engineering tested; real coupled execution remains a separate stage.
