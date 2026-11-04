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

The [workspace response schema](../contracts/workspace-upgrade-blueprint.schema.json) separates the modeled assignment, native npm validation, installation, checks, runtime, acceptance and policy. The response is planning-only. A matching candidate lock can be reviewed for an authorized isolated trial. `MONOREPO_TO_POLICY` returns an explicit unsupported-goal error on this route.
