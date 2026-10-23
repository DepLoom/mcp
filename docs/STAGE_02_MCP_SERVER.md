# Stage 2: DepLoom MCP Server

Status: **implemented (v0.1.0)**. Boundary reviewed — see `STAGE_01_UPGRADE_BLUEPRINT.md`.

## What the server is

`deploom_mcp.server` exposes exactly one MCP tool — `get_upgrade_plan` — over
an MCP **stdio** transport, built on the official MCP SDK (`mcp>=2,<3`,
`MCPServer`). The tool is an **async** MCP tool: it awaits the Stage 1 boundary
(`generate_upgrade_blueprint_async` in `deploom_mcp.blueprint`), which runs
the pinned Core (`upstream/deploom` submodule) as an isolated, read-only
subprocess. The same handler also exposes the **synchronous** wrapper
`get_upgrade_plan` (kept as the internal entry for process-local tests).

Entry points, both equivalent:

```bash
python -m deploom_mcp
deploom-mcp                 # console script after `pip install -e .`
```

## Tool contract

`get_upgrade_plan(project_path, target_level="yellow", min_lag_ok_pct=80,
deadline_seconds=20.0, include_prerelease=False, registry=None,
registry_metadata_ttl=3600.0, output_dir=None, exclude=None)`

* `project_path` (required, absolute) — the analysed project. Only ever read:
  the test suite asserts a byte-for-byte project snapshot before/after.
* `target_level` — `"yellow" | "green"`; anything else →
  `invalid-goal`.
* `min_lag_ok_pct` — minimum registry-lag % for a clean proposal; must be an
  integer in `[0, 100]`, otherwise `invalid-policy`.
* `deadline_seconds` — draft planning budget; default `20.0`, **clamped to
  `[5, 600]`**. `NaN`, `±Inf` or any non-finite value → `invalid-deadline`.
* `include_prerelease` — allow prerelease targets (forwarded to the planner).
* `registry` — optional npm registry override; must be an `http(s)` URL **with
  no embedded credentials**, otherwise `invalid-registry`.
* `registry_metadata_ttl` — packument TTL (seconds) for the shared cache; must
  be finite and `>= 0`, otherwise `invalid-registry`.
* `output_dir` — optional artifact root; **MUST be outside the project**,
  otherwise `output-inside-project` (both "inside the project" and "a parent
  of the project" orders are rejected).
* `exclude` — optional list of packages to skip (passed to the planner as
  `--exclude-dependency <package>|reason`; an explicit reason is always
  attached). Must be a non-empty list of non-empty strings, otherwise
  `invalid-exclude`.

### Cancellation

Because the tool is async, client cancellation propagates as task
cancellation. The boundary responds to a `cancel_event` by killing the whole
Core process tree (Windows: `taskkill /T /F`; POSIX: `killpg`), waiting for a
bounded drain, and — if the draft was already partially produced — returns a
`DRAFT_PARTIAL` plan with `partial.replanRequired=true` instead of a spurious
failure. If planning never started, the client receives `cancelled`.

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
* `truncation` — `{truncated, inlineDependencies, totalDependencies}` when the
  dependency rows are clipped to the inline budget (default 100 rows); the
  `summary.statusCounts` stays complete regardless;
* `partial` — `{partial, replanRequired, processed, processedTotal, reason}`
  when the draft is only partially computed (deadline/cancellation);
* `artifacts` — paths to the full Blueprint, manifest, plan, prompt, summary
  and solver components, so an agent can read the detail with its own tools;
* `honesty` / `boundaryNote` — the planning-only disclaimer.

The full JSON Blueprint is **not** streamed into the model context; only the
paths are returned, and the inline rows are bounded (`truncation` reports the
clipped count).

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
* `invalid-policy` — `min_lag_ok_pct` not an integer in `[0, 100]`.
* `invalid-deadline` — deadline non-finite (`NaN`/`±Inf`) or invalid.
* `invalid-registry` — registry not an `http(s)` URL, carries credentials, or
  the TTL is finite-negative/`NaN`.
* `invalid-exclude` — `exclude` is not a non-empty list of non-empty strings.
* `output-inside-project` — `output_dir` inside the project or a project parent.
* `output-conflict` — the resolved artifact path already exists (returned
  artifacts were written by an earlier, identical run).
* `project-not-found` — project dir absent.
* `invalid-project` — no `package.json`.
* `core-not-found` — pinned submodule not initialized.
* `generator-failed` / `generator-timeout` — the Core subprocess failed or hit
  its (deadline-derived) subprocess budget.
* `manifest-missing` / `manifest-invalid` — Core produced no/ill-formed
  `result.json`.
* `plan-missing` / `plan-invalid` — the draft produced no usable `plan.json`
  (or it fails the required structure checks).
* `contract-violation` — the produced manifest status is not a planning-only
  draft (`DRAFT_READY`/`DRAFT_PARTIAL`); planning is refused (fail closed).
* `hash-mismatch` — a published artifact on disk does not match its declared
  SHA-256, so it is not returned as trustworthy.
* `schema-missing` / `schema-validator-unavailable` / `schema-invalid` — the
  runtime JSON Schema check could not run (schema file absent / `jsonschema`
  not installed / blueprint violates the schema).
* `cancelled` — the client cancelled before planning started.

## Tests

`tests/test_mcp_server.py` covers (a) fast validation paths without the
planner (including the fail-closed hardening cases: `NaN`/`±Inf` deadline,
`min_lag_ok_pct` ranges, registry URL / TTL, `exclude` shape), (b) the compact
protocol projection **including explicit `truncation` and `partial` blocks**,
and (c) a **real stdio round-trip** through the official MCP client SDK,
including a real planning run that asserts the project directory is untouched
and the payload carries planning-only statuses.

`tests/test_blueprint_hardening.py` adds 17 deterministic boundary tests that
run the boundary against a **fake planner core**
(`tests/fixtures/fake_core/dependency_live_roadmap_generator.py`) — no network,
no real solver — covering the contract validator, mandatory `plan.json`, hash
verification, runtime JSON Schema validation, filesystem write guards, unique
run directories and exclusions. `tests/test_process_runner.py` verifies the
subprocess runner lifecycle (timeout kill of the whole tree, cancellation,
bounded output). A CI suite on the real pinned Core runs over the network.
`scripts/smoke_stdio.py` boots `python -m deploom_mcp` over stdio and asserts a
clean, protocol-only stdout. See README "Tests".
