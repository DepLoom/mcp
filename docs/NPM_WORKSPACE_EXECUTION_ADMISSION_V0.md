# npm workspace execution admission — v0 design only

Status: ADMISSION_UNSUPPORTED. This document grants no execution capability.
The read-only inventory and declaration proposal keep executionReady=false.
Existing root-only goal APIs retain their workspace rejection. Implementing this
contract requires a separate bounded engineering stage and installed-package tests.

## Proposed transaction

A host-controlled immutable admission record must bind all of the following:

- Full source snapshot and source policy, including every member's relevant
  source/config, root and member manifests, one root lock v3, implementation and
  toolchain identities. The inventory digest alone is insufficient.
- Exact authorized changes as owner/section/name/fromSpec/toSpec tuples. Every
  other direct declaration of every owner is held. No automatic mutation of all
  owners sharing a package name is permitted.
- Globally immutable physical instances as a separate explicit lock-path contract
  with versions, integrity and identity. Holding a direct declaration does not
  forbid a necessary nested duplicate or unrelated placement by itself.
- Complete workspace membership and canonical internal link-to-owner mappings.
  Versioning an internal package or changing its consumers needs its own explicit
  scope. External local links, ambiguous owners, overrides and alternate lock
  owners remain unsupported in the initial execution profile.
- Host-owned environment and check profile: exact working directories, commands,
  oracle/check bindings, original baseline/known-failure semantics, registry/cache
  contract, budgets, authorization and applicable project instructions.

## Required boundaries

1. Reconcile declared constraints and exact intent without treating a root-only
   solver result as npm placement. Resolver failures/timeouts are not global UNSAT.
2. Prepare changes in an isolated owned source copy. Compare all owner manifests
   against the authorized delta and hold every other declaration exactly.
3. Reconcile a complete proposed root lock and all member/link records. Preserve
   engine/peer checks. No force, legacy-peer workaround or silent scope expansion.
4. Materialize internal links only to the corresponding sealed workspace owners;
   do not disable the ordinary reparse/escape guard globally. Inspect actual links
   and the physical dependency tree independently of lock declarations.
5. Execute the complete authorized validation profile against those exact bytes.
   Record installation, project checks and runtime separately; missing evidence
   blocks acceptance. Respect local restrictions on automated project commands.
6. Reopen source, lock, toolchain, permission and proof identities at final apply.
   Publish a checkpoint only for that verified state. Current and resume must
   reject stale or foreign owner/link/lock/proof state without spending a new
   attempt or abandoning the last verified checkpoint.

STOP before source mutation if any identity, owner, held constraint, link,
materialization, authorization, unsupported feature or required check is unknown.
A finite search incumbent or successful npm resolver does not override this STOP.

## First future acceptance fixture

Use the existing isolated npm-generated four-owner fixture with is-number 6/7
and an internal shared workspace. Change only apps/b devDependencies is-number
6.0.0 to 7.0.0; all other declarations stay held. Add stale member/lock/link,
foreign proof, missing check, unapproved owner/instance change and restart controls.
This fixture is a future protocol, not a completed execution acceptance. No real
project or old S2/S3 budget is implicitly authorized by this document.
