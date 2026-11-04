# Read-only npm workspace inventory v1

`observe_workspace_inventory_v1` observes an explicitly selected npm root with a
root package-lock v3. It never installs dependencies or runs project commands.
The existing goal, repair, apply and checkpoint routes still reject workspaces.
This inventory is not monorepo execution support.

The bounded contract supports relative literal workspace paths and single-segment
`*` patterns. Globstar, braces, negation, alternate lock owners, local links to
non-members, unsafe filesystem links and ambiguous package names are rejected.
Caps and timeouts produce PARTIAL; unreadable input produces UNKNOWN. No truncated
inventory is promoted to a complete proposal. The tool runs in a caller-owned
subprocess with a 5–30 second deadline. Directory, file, owner and output limits
are independent of that deadline.

Each declaration has an `owner` (root is `.`), `section`, `name` and
`declaredSpec`. The same package in two owners or two sections remains separate.
Internal workspace names and root-lock link targets are reconciled without
reading physical node_modules links. Root overrides and peer metadata remain
hash-bound inputs; inventory does not evaluate their semantic effects.

Worker startup uses Python isolated mode, an explicit trusted package path and
UTF-8 mode. Input files are opened through checked handles. On Windows, directory
handles use GENERIC_READ and deny write/delete sharing while their namespace is
used; metadata-only handles would not prevent directory replacement. On POSIX,
opening and enumeration use no-follow descriptors relative to the held directory.
Handle identity and final Windows path are checked before reading. Busy or
unreadable inputs fail closed. This observation still does not authorize execution.

Only plain version/range/tag text is disclosed in declaration specs. URL, path,
alias/protocol or credential-bearing specs are rejected without echoing them.
Proposal targets receive this validation before constructing worker arguments.

The returned `inventoryDigest` binds the bytes of the root manifest, root lock,
member manifests, membership and supported pattern list. Inputs and discovery
are read twice to reject observed changes. This is a dependency-input observation,
not an atomic full-source snapshot or an execution continuity proof. No toolchain,
registry availability, solver optimality, placement, installation, health or
runtime checks have been performed.

An optional proposal requires `expected_inventory_digest` and `targets` entries:

```json
{
  "owner": "apps/b",
  "section": "devDependencies",
  "name": "is-number",
  "fromSpec": "6.0.0",
  "toVersion": "7.0.0"
}
```

The tool reobserves current inputs, rejects a stale digest or declaration, and
returns intent plus hashes/counts of held declarations by owner. It does not
convert held declarations into globally immutable installed instances. Internal
workspace package targets need a later dedicated version/link contract and are
currently rejected. A proposal grants no permission to edit any declaration.

Every successful result retains `executionReady=false`, `NOT_VERIFIED` and
`jointAssignment=NOT_COMPUTED`. Full workspace solving and execution still require
owner-qualified constraints, authorized lock/source changes, safe materialization,
physical dependency verification and independent checkpoint/current/resume checks.

## Targeted workspace plans through the main API

Call `get_upgrade_plan(project_path=..., workspace_request=...)` in Python or pass the same fields to the MCP tool. The workspace envelope uses `schemaVersion: 2` and `goalKind: TARGETED_UPGRADE`. The existing flat-project parameters retain their defaults when the workspace envelope is present.

1. Call `observe_workspace_inventory_v1` on the npm lockfile owner and reuse its `inventoryDigest`.
2. Supply `targets` with one exact `selector: {owner, section, name}` per declaration. The root owner is `.`. Each target has `approvedLines`, an ordered finite list of stable `versions` in those major lines, and an `exactTarget` from that list or `null`.
3. Supply `packuments` for the frozen candidate evidence, `environment` with the modeled Node and npm versions, and `topology`. Use `null` to model the observed layout when it can be proven. An explicit layout uses `instanceGroups` and `providers`, with owner-qualified declaration triples.
4. For a pure plan, omit `preflight`. For host-authorized lock-only validation, set `preflight: {reauthor: null}`. The host must have configured its isolated offline npm cache, registry and artifact directory. Optional `immutableInstances` pins exact lock paths and `candidateLimit` sets a maximum from one to three within the shared 60-second budget.

Call `get_workspace_plan_details` with the issued `details.planId`, `requestId`, nullable
`candidateId` and `details.snapshotSha256`, mapped to the snake-case tool parameters.
Choose an owner and section from the selected targets, then follow `page.nextOffset`.
Details are available in the originating server or Python process for up to 15 minutes
and may be evicted after 16 plans. The ID is a read capability. External rule
endpoints are redacted. The response includes shared native status and command
digests, without exposing raw logs or reading a path supplied by the client.

The [workspace response schema](../contracts/workspace-upgrade-blueprint.schema.json) separates the modeled assignment, native npm validation, installation, checks, runtime, acceptance and policy. The response is planning-only. A matching candidate lock can be reviewed for an authorized isolated trial.

## Whole-workspace policy planning

Use `goalKind: MONOREPO_TO_POLICY` with `policy: {targetLevel: "yellow"}` or
`policy: {targetLevel: "green"}`. Omit `packuments`. Core uses the host's HTTPS
`DEPLOOM_WORKSPACE_POLICY_REGISTRY` and only sends exact names in
`DEPLOOM_WORKSPACE_POLICY_OSV_PACKAGES` (a comma-separated operator allowlist)
to OSV. Authorize the inventory for the selected registry before enabling it.
Missing host configuration, unavailable evidence or a name omitted from the
OSV allowlist can leave the result unknown. The opt-in
`DEPLOOM_WORKSPACE_POLICY_SYSTEM_PROXY=1` uses the existing system proxy.
Native preflight, if requested, requires the same registry identity.

Targets control mutable declarations. An empty target list holds every
declaration. Held external declarations still count toward the lag denominator.
Workspace links are classified separately and remain in the compatibility
model. Green requires 100 percent confirmed lag and zero Critical/High.
The default M/L rule for Green is the existing combined cap of 20. Explicit
per-severity caps use the corresponding configured values. The response
identifies the modeled scope, evidence coverage and policy limits. Final
audit, install and runtime have separate gates.

## Audit the exact resolver candidate

After materializing the candidate manifests and lock into the authorized
isolated project, the host can run:

```text
python -m deploom_mcp.workspace_acceptance audit-candidate <project>
  --baseline-lock <original-package-lock>
  --resolver-dir <owned-resolver-artifact>
  --expected-lock-sha256 <candidate-lock-sha256>
  --expected-resolver-identity-sha256 <resolver-identity-sha256>
  --registry <the-resolver-registry>
  --output-dir <separate-local-evidence-directory>
```

Join the lines as one command or use the shell's line continuation. The host
supplies the identities from the previously approved resolver result. The
command checks the artifact, root lock and each member manifest, captures
that exact input for npm audit, and rechecks it afterward. It uses the
explicit registry and isolated npm config. The registry receives dependency
inventory only when the host authorizes that destination. The saved audit
report contains the lock, registry and resolver identity hashes.

`BOUND_SECURITY_AUDIT_COMPLETE` describes collection of known-advisory
evidence. The findings verdict may still be `FINDINGS`.

Add `--policy-json <approved-policy.json>` to assess Yellow or Green on the
same candidate. The policy file uses the options above, such as
`{"targetLevel":"yellow","minLagOkPct":80,"lagPolicyMonths":12}`.
The audit resolves each declaration from its owner in the exact lock and
collects fresh publication metadata from the same registry. The report includes
the normalized policy hash, counts and per-owner lag evidence. Missing facts
can yield `UNKNOWN`. With a policy file, exit zero requires a policy pass.
Physical installation, project checks and runtime require separate evidence.
