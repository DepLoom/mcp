# Stage 2: DepLoom MCP Server

Status: **implemented (v0.1.0)**. Boundary reviewed — see `STAGE_01_UPGRADE_BLUEPRINT.md`.

## What the server is

`deploom_mcp.server` exposes exactly one MCP tool — `get_upgrade_plan` — over
an MCP **stdio** transport, built on the official MCP SDK (`mcp>=2,<3`,
`MCPServer`). The server does **not** implement planning: it calls the Stage 1
boundary (`generate_upgrade_blueprint` in `deploom_mcp.blueprint`), which runs
the pinned Core (`upstream/deploom` submodule) as an isolated, read-only
subprocess.

Entry points, both equivalent:

```bash
python -m deploom_mcp
deploom-mcp                 # console script after `pip install -e .`
```

## Tool contract

`get_upgrade_plan(project_path, target_level="yellow", min_lag_ok_pct=80,
deadline_seconds=20.0, include_prerelease=False, registry=None,
registry_metadata_ttl=3600.0, output_dir=None)`

* `project_path` (required, absolute) — the analysed project. Only ever read:
  the test suite asserts a byte-for-byte project snapshot before/after.
* `target_level` — `"yellow" | "green"`; anything else →
  `invalid-goal`.
* `deadline_seconds` — draft planning budget; default `20.0`, **clamped to
  `[5, 600]`**. Non-numeric / non-positive → `invalid-deadline`.
* `include_prerelease` — allow prerelease targets (forwarded to the planner).
* `registry` — optional npm registry override.
* `registry_metadata_ttl` — packument TTL (seconds) for the shared cache.
* `output_dir` — optional artifact root; **MUST be outside the project**,
  otherwise `output-inside-project` (both "inside the project" and "a parent
  of the project" orders are rejected).

### Managed state

Planning metadata (packument cache) lives in a managed directory shared between
calls: `$DEPLOOM_MCP_STATE_DIR` if set, else `~/.deploom-mcp`. Default output
roots fall under `<managed>/artifacts/<run-id>`. Nothing is written into the
analysed project.

## Response (compact protocol)

On success the tool returns a compact, agent-friendly payload
(`ok: true, protocol: "upgrade-blueprint", ...`) with:

* `run` — `runId`, `projectId`, project name;
* `status` — `draftStatus`, `verificationStatus`, `authority`,
  `compatibility`, `parallelExecutionSafety`, `partialReason`;
* `summary` — `totalDependencies`, `proposed`, `needsReview`, `statusCounts`;
* `recommendationGroups` — display-priority groups (NOT a parallel-proof order);
* `dependencies` — compact rows (`package`, `status`, `current`, `target`,
  `group`, `needsReview`);
* `artifacts` — paths to the full Blueprint, manifest, plan, prompt, summary
  and solver components, so an agent can read the detail with its own tools;
* `honesty` / `boundaryNote` — the planning-only disclaimer.

The full JSON Blueprint is **not** streamed into the model context; only the
paths are returned.

## Honesty contract (mandatory)

Every response carries:

```
verificationStatus = NOT_VERIFIED
authority          = PLANNING_ONLY
compatibility      = UNKNOWN
parallelExecutionSafety = UNPROVEN
```

Stable-meaning semantics:

* `PROPOSED` — "this upgrade can be proposed", NOT "already updated".
* `needsReview === true` — do NOT act on this row until the project owner
  confirms the migration (breaks, minimum versions, security trade-offs).
* The reserved verified statuses `CONSTRAINT_SATISFIED`, `MATERIALIZED`,
  `PROJECT_VERIFIED` never appear in a planning response (unit-tested).
* `recommendationGroups` order is display priority, not a proof that groups can
  be migrated in parallel.

## Errors

Always machine-readable as `{"ok": false, "error": {"code", "message",
"detail"}}`; the tool result is flagged `isError`:

* `invalid-args` — `project_path` not a non-empty string.
* `invalid-goal` — `target_level` not `yellow`/`green`.
* `invalid-deadline` — deadline non-numeric or non-positive.
* `output-inside-project` — `output_dir` inside the project or a project parent.
* `project-not-found` — project dir absent.
* `invalid-project` — no `package.json`.
* `core-not-found` — pinned submodule not initialized.
* `generator-failed` / `generator-timeout` — the Core subprocess failed or hit
  its (deadline-derived) subprocess budget.
* `manifest-missing` / `manifest-invalid` — Core produced no/ill-formed
  `result.json`.
* `cancelled` — the client cancelled before planning started.

## Tests

`tests/test_mcp_server.py` covers (a) fast validation paths without the
planner, (b) the compact protocol projection, and (c) a **real stdio round-trip**
through the official MCP client SDK, including a real planning run that asserts
the project directory is untouched and the payload carries planning-only
statuses. See README "Tests".
