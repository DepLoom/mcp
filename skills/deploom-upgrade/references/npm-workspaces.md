# npm workspace upgrades

1. Observe the lockfile root with `observe_workspace_inventory_v1`. Reuse its
   `inventoryDigest`. Select exact `{owner, section, name}` declarations, where
   `.` identifies the root. Keep same-name declarations in different owners
   separate. Keep held declarations and their locked versions as constraints.
2. In PLAN_ONLY with a host-approved registry, use `model_workspace_upgrade_v1`
   when available and registry-only planning is appropriate. Send
   `project_path`, `goal_kind`, `inventory_digest`, `registry`, `environment`
   and `targets` directly. The server uses the observed lock layout and
   allows no preflight. For MONOREPO_TO_POLICY include `policy` and use an
   empty target list to hold the baseline. For TARGETED_UPGRADE omit policy
   and supply the same owner-qualified target records as below.
   In other cases, call `get_upgrade_plan` with `workspace_request.schemaVersion: 2`,
   `goalKind: TARGETED_UPGRADE`, the inventory digest and selected targets.
   Send the approved major lines, supported finite candidate versions and any
   exact target for each selector. For a host-authorized project registry,
   pass its HTTPS URL as registry and omit packuments to let Core collect
   metadata for both selected and held declarations. Otherwise supply the
   frozen packuments. Missing metadata or topology stays unknown.
3. Omit `preflight` for a pure model. Enable isolated lock-only npm preflight
   only with the required authority and host setup. A rejected candidate
   applies to its recorded inputs. Preserve pinned versions. If the only gap
   is missing candidate peer placement and the task authorizes lock-only npm
   resolution, keep Core's UNKNOWN and collect separate npm evidence in an
   isolated copy with strict peers and engines. Use the same approved targets
   and scope. Compare the candidate lock against the held owners. Do not
   infer permission to update a held declaration from a resolver conflict.
   A resolver rejection applies to that tuple and baseline lock. If version
   choice is delegated, look for stable alternatives across supported minor
   lines that preserve the objective and held constraints. Test distinct
   tuples from the same baseline lock with exact candidate specs. Do not
   treat npm's reported conflict edge as a proven root cause or silently
   restart resolution without the held lock.
   Stop when the evidence cannot be obtained within the authorized scope.
4. Read `constraintModel` and `packageManagerValidation` separately. When a
   decision needs details, pass the issued capability, request/candidate IDs
   and snapshot hash to `get_workspace_plan_details` for the selected owner and
   section. Treat redacted external endpoints and partial rule coverage as
   explicit evidence limits. Keep plan capabilities out of shared reports.
5. Review the selected lock before any authorized isolated trial. Record
   baseline, selected, applied and installed version per declaration with its
   owner and evidence. Independently verify install, project checks and
   affected runtime. Preserve unverified gates and deferred changes.

For Yellow or Green across the workspace, use `goalKind: MONOREPO_TO_POLICY`
and `policy.targetLevel`. Omit caller packuments. Use lowercase yellow or green.
For baseline observation use targets: []. The supported optional policy fields
are minLagOkPct, lagPolicyMonths, maxKnownHigh, maxKnownModerate and maxKnownLow.
For a private workspace whose host approved registry advisories, request
`policy.securityEvidenceMode: REGISTRY_ONLY`. The host must authorize the same
registry for both metadata and advisories. Never switch to OSV or a public
registry to get around missing approval. Inspect `policy.evidence` for the
source and unknown counts. Held external declarations stay in scope. Audit the
complete final lockfile separately before claiming Yellow or Green.

For an authorized candidate with an owned resolver receipt, use the host-side
`python -m deploom_mcp.workspace_acceptance audit-candidate` with the original
lock, resolver artifact, exact lock and resolver hashes, approved registry and
`--policy-json`. Check `finalPolicy` separately from audit completion. On
`candidate-audit-egress-not-configured`, keep final policy unknown and hand off
to the host operator. The host must configure restricted npm audit egress and
a no-redirect endpoint before enabling the audit for that exact registry.
Preserve the requested health goal when evidence or host configuration is missing.
For a registry-backed targeted request, send all of these fields together.
Registry, environment and topology belong inside workspace_request. Leave
the top-level flat-project options at their defaults.

```json
{
  "project_path": "<absolute lockfile-root path>",
  "workspace_request": {
    "schemaVersion": 2,
    "goalKind": "TARGETED_UPGRADE",
    "inventoryDigest": "<digest from inventory>",
    "registry": "https://registry.example.invalid/npm",
    "environment": {"node": "22.22.0", "npm": "11.6.0"},
    "topology": null,
    "targets": [{
      "selector": {"owner": "packages/example", "section": "dependencies", "name": "example"},
      "approvedLines": [3],
      "versions": ["3.2.1"],
      "exactTarget": "3.2.1"
    }]
  }
}
```

Both workspace goals require schemaVersion, goalKind, inventoryDigest,
targets, environment and topology. Use topology: null for the observed lock
layout. Supply the observed Node and npm versions when known. If neither
is known, send environment: {} and report the limitation. Use observed
values for registry, versions and selectors. For MONOREPO_TO_POLICY, add
policy inside workspace_request and use targets: [] to hold the baseline.
Send only project_path and workspace_request at the top level. For an exact
workspace target use targets[].exactTarget; omit the flat objective,
target_mode and exact_targets fields. If the API rejects the request shape,
compare its keys with this envelope before retrying. An invalid request
supplies no compatibility verdict.

An empty targets list holds all declared versions. Treat REVIEW_HELD_BASELINE
as a review of existing versions. A blocked result from that request describes
the baseline, even when the response says UNSAT_IN_MODELED_DOMAIN.
It says nothing about authorized version combinations that were left out.

If the task authorizes version choice and the policy response has no actionable
assignment, use available evidence to identify a direct update or the direct
provider of a transitive blocker. Resolve it to an observed owner, section and
name. Use baseline audit findings when an audit was authorized and available.

In a planning-only profile, use existing approved evidence and any applicable
lag or advisory observations returned by the plan. Registry-only policy
evidence describes the modeled declarations. An aggregate lag count or an
advisory count cannot identify which declaration to upgrade. If the user gave
a specific target or the evidence identifies an in-scope cohort, plan that
cohort without preflight. If no cohort can be justified, report the held
baseline and the specific missing evidence. Do not run an audit outside the
profile or switch registries to fill that gap.

From the project's approved registry, obtain locked and candidate metadata,
including relevant dependency, peer and engine fields. Send a finite in-scope
cohort to TARGETED_UPGRADE using the same inventory digest if dependency inputs
are unchanged. Keep held constraints and continue independent authorized work
with its own evidence when possible. A targeted model result does not prove
advisory removal, installation or Yellow.

A targeted assignment only covers its declared scope. An independent audit of
the exact final lock still decides whether the original whole-workspace or
production health goal was reached.

## Scoped transitive overrides

1. Use the configured registry's npm audit to identify the affected package,
   advisory ranges and exact lock paths. Get the current
   `observe_workspace_inventory_v1` digest. Call
   `review_workspace_transitive_overrides_v1` with `inventoryDigest`,
   `dependency`, `affectedPaths`, and `advisoryRanges`. When no suitable locked
   version exists, inspect the configured registry and pass bounded stable
   `candidateVersions`. Include every relevant advisory range. If several
   consumers share a hoisted provider, pass `selectedConsumerPaths` with the
   exact authorized consumer lock paths. Repeat the same selection when
   verifying the candidate. A nonzero `remainingAffectedConsumerCount` means
   other consumers still need separate remediation.
2. Prefer an in-range stable fix that stays within the authorized scope.
   Treat `versionDirection: DOWNGRADE` as a separate decision, even when
   that version is already present in another branch of the lock.
   Inspect the candidate parent selectors and all matching consumer paths
   before merging into root `overrides`. Preserve existing rules. A blocked
   selector requires new evidence, such as an upstream parent upgrade.
   Do not broaden it to a global child override to get a passing audit.
3. An out-of-range result is a review option. Check the parent's use of the
   child, its documented API changes, candidate metadata, Node engines and
   effective peers. Keep the out-of-range decision explicit. If compatibility
   is unresolved, report that status.
4. In an authorized isolated checkout regenerate the lock with strict npm
   resolution and scripts disabled. Verify that the exact affected consumers
   bind to the new version, unrelated consumers and held declarations retain
   their prior versions, and the audit no longer reports the finding. npm may
   pin the parent's effective incoming range to the version named in the
   override selector even when the declared range and installed parent stay
   unchanged. Compare effective ranges and record any pin for separate review.
   Confirm
   clean installation and exercise the changed code path, including negative
   inputs when relevant. Recheck after any further lock or manifest changes.
   Report resolver, install, audit and affected runtime evidence separately.

A zero exit from lock-only npm can leave a scoped override bound to the old
version or leave the provider missing. Stop before installation on either
result. For an exclusively owned leaf with no dependencies, optional
dependencies or peers, use a temporary minimal manifest to let npm author
the exact replacement lock entry from the approved registry. Verify its
integrity, seed only the affected slot in a scratch copy and let strict npm
normalize it. Recheck the effective provider and every held lock binding.
Keep the failed attempt separately. If the candidate changes another binding
or npm cannot normalize it, stop and replan the parent. For multiple scoped
repairs, review each selector and then compare the entire combined lock before
a single clean installation.

When an isolated baseline copy and a separately prepared candidate are
available, pass `candidate: {projectPath, inventoryDigest, version}` with the
same advisory inputs to `review_workspace_transitive_overrides_v1`. A result
of `OVERRIDE_LOCK_BINDINGS_MATCH` means that the requested override and the
observed child bindings match, including unchanged bindings for other
consumers of that package. For a dependency-free provider, inspect
`otherLockBindings` for the additional check of every other declared lock
binding. With non-leaf providers, compare the remaining graph separately.
These checks do not prove effective npm ranges, install, advisory removal, or runtime
compatibility. Exercise the actual parent API on both success and error
paths. A generic smoke test of the child package alone cannot detect
changes in how the parent handles errors from it.
