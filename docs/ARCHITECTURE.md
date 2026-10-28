# Architecture

This repository is the **headless planning boundary** of the DepLoom ecosystem.

## Layering

```text
┌──────────────────────────────────────────────────────────────┐
│ MCP client (OpenCode / other)                                 │
│   get_upgrade_plan (one tool, stdio transport)                │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│ deploom_mcp.server  (MCP SDK server, validation, compact      │
│                       protocol, managed state dir)            │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│ deploom_mcp.blueprint  (Stage 1 boundary / adapter:           │
│   generate_upgrade_blueprint, policy+options, error mapping)  │
└───────────────────────────┬──────────────────────────────────┘
                            │ subprocess  (read-only on the project)
┌───────────────────────────▼──────────────────────────────────┐
│ upstream/deploom  (pinned planning Core, Git submodule)       │
│   Draft mode → result.json + plan.json                        │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│ Upgrade Blueprint (JSON Schema v2, contracts/)                │
│   NOT_VERIFIED / PLANNING_ONLY / UNKNOWN / UNPROVEN           │
└──────────────────────────────────────────────────────────────┘
```

## Design invariants

1. **Planning-only.** No verification, no execution, no repo writes, no
   GitLab/Renovate integration, no agent dispatch. `get_upgrade_plan` never
   verifies.
2. **Single pinned Core.** Planning = subprocess run of the pinned submodule
   (the `upstream/deploom` gitlink bound in `release-manifest.json`); the MCP server and adapter never reimplement
   solving. Updating the Core is an explicit, reviewable pin change.
3. **Read-only on the project.** The project directory is only ever read; the
   test suite proves byte-for-byte immutability across a real run.
4. **Managed state outside the project.** Packument cache and default outputs
   live under `DEPLOOM_MCP_STATE_DIR` (default `~/.deploom-mcp`); `output_dir`
   is refused when inside the project or a project ancestor.
5. **Compact to the agent, full artifacts on disk.** The MCP response is a
   compact protocol plus artifact paths; the full Blueprint is not streamed into
   the model context.
6. **Honest statuses.** Every response is `NOT_VERIFIED / PLANNING_ONLY /
   UNKNOWN / UNPROVEN`; a `PROPOSED` verdict means "can be proposed", and
   `needsReview` rows require evidence review under the existing user authorization. They do not create an additional blanket approval gate. The reserved verified statuses
   are never produced by the planning pipeline.

## Data flow of one tool call

1. `get_upgrade_plan` validates inputs (goal, deadline clamp, `output_dir`
   placement) and prepares a managed `output_dir` + cache.
2. `generate_upgrade_blueprint` resolves the pinned Core, writes a control
   workspace under `output_dir/exec`, launches the Core subprocess in Draft mode
   with policy/options, and collects `result.json` / `plan.json`.
3. The planner's Draft artifacts are transcoded into the schema-validating
   `Upgrade Blueprint` and projected into the compact protocol.
4. Errors from every layer map to machine-readable `{code, message, detail}`
   and the tool result is flagged `isError`.

See `STAGE_01_UPGRADE_BLUEPRINT.md` for the boundary contract and
`STAGE_02_MCP_SERVER.md` for the MCP server contract.

## Versioned headless package

The source gitlink remains the Core source of truth. Wheels include an explicit
hash-bound inventory of its Python modules and required data, plus the schema
and canonical Skill. Resource locators support both editable checkouts and
installed packages without Desktop. See [DISTRIBUTION.md](DISTRIBUTION.md).

The decision contract distinguishes `targetMode` from `objective`. Legacy
callers default to pinned proposals and minimum change to health policy; an
advisory request permits evidence-backed alternatives within user-approved
bounds. This contract does not grant execution or verification authority.

Workspace discovery and semantic input fingerprints are not a joint solver.
A single-manifest plan reports unevaluated workspace manifests and requires
reconciliation before workspace-wide application. Compact decision completeness
is separate from row clipping and from compatibility or policy attainment.
