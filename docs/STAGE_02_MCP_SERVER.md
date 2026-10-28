# Stage 2: DepLoom MCP Server

Status: **implemented (v0.2.0)**. Decision contract and limitations:
[`DECISION_CONTRACT.md`](DECISION_CONTRACT.md). Historical boundary review:
`STAGE_01_UPGRADE_BLUEPRINT.md`.

## What the server is

`deploom_mcp.server` exposes exactly one MCP tool — `get_upgrade_plan` — over
an MCP **stdio** transport, built on the official MCP SDK (`mcp>=2,<3`,
`MCPServer`). The tool is an **async** MCP tool: it awaits the Stage 1 boundary
(`generate_upgrade_blueprint_async` in `deploom_mcp.blueprint`), which runs
the pinned headless Core (bundled in wheels; `upstream/deploom` in a checkout) as an isolated, read-only
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
registry_metadata_ttl=3600.0, output_dir=None, exclude=None,
target_mode="pinned", objective="minimum-change-to-policy",
mutable_packages=None, approved_version_lines=None, draft_max_candidates=3,
previous_evidence_digest=None)`

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
  of the project" orders are rejected). Managed-state overlap is a separate
  guard: `state-inside-project` is raised before any directory is created
  (see "Managed state").
* `exclude` — optional list of packages to skip (passed to the planner as
  `--exclude-dependency <package>|reason`; an explicit reason is always
  attached). Must be a non-empty list of non-empty strings, otherwise
  `invalid-exclude`.
* `target_mode`, `objective`, `mutable_packages`, `approved_version_lines` —
  the version-selection contract. Core must acknowledge advanced requests;
  unsupported or mismatching applied contracts fail closed. See
  [`DECISION_CONTRACT.md`](DECISION_CONTRACT.md) for defaults and scope semantics.
* `draft_max_candidates` — integer `1..128`, default `3`, for security-evidence
  candidate search; independent of `deadline_seconds`.
* `previous_evidence_digest` — optional lowercase SHA-256 from a previous plan,
  to diagnose unchanged published evidence without implying a frozen registry.

`exclude` is applied by the **planner itself** (not filtered afterwards):
excluded packages run through the real plan scope and are reported as
`EXCLUDED` rows in the response (still visible, `needsReview=false`), they are
excluded from the health denominator, and the exclusion list is echoed back in
the response's `policy.exclusions`.

### Cancellation

Planning runs as a long-lived async child process, so four distinct ways a run
can end without a final `DRAFT_READY` plan must not be confused:

* **DRAFT_PARTIAL is a planner-produced result**, not a cancellation artifact.
  It appears only when the Core *itself* published a partially-computed draft
  (typically its own planning deadline): the response has
  `status.draftStatus="DRAFT_PARTIAL"` and `partial.partial=true` with
  `partial.replanRequired=true`.
* **Client cancellation (interrupt)** — the client abandons the request (the
  MCP SDK's per-request `read_timeout_seconds` sends `notifications/cancelled`).
  The boundary responds to the cancel event by killing the whole Core process
  tree (Windows: `taskkill /T /F`; POSIX: `killpg`) and draining output. A
  client interrupt does **not** manufacture a `DRAFT_PARTIAL` plan: a partial
  blueprint is returned only if the planner published one before the interrupt
  was handled. If planning never started, the client receives `cancelled`;
  otherwise the abandoned handler raises `CancelledError`. The server session
  stays alive for subsequent calls.
* **Planner/run timeout** — the boundary's own subprocess budget (`draft
  deadline` + margin) is exceeded: the whole tree is killed and the call fails
  closed with `generator-timeout`.
* **Server shutdown / disconnect** — in-flight handlers are cancelled and the
  same tree-kill/drain cleanup runs; no partial blueprint is emitted.

This is validated end-to-end over a **real stdio session** with the official
MCP client SDK: a slow planner fixture (see `tests/fixtures/slow_core/`) writes
a heartbeat marker ~every 50 ms; the test abandons a live `tools/call` via the
SDK's per-request `read_timeout_seconds` (which sends `notifications/cancelled`
over the wire), then asserts the heartbeat stops growing within a bounded
grace (the whole planner child tree is dead), that the session stays responsive
and that a follow-up call in the same session succeeds.

Test/ops escape hatch: the server never parses generator/interpreter from tool
arguments, but reads them from the environment if set —
`DEPLOOM_MCP_GENERATOR_PATH` (planner script path) and `DEPLOOM_MCP_PYTHON`
(interpreter). The public tool signature is unchanged; the planner child never
inherits stray `DEPLOOM_*` variables (`_exec_env` strips them).

### Managed state

Planning metadata (packument cache) lives in a managed directory shared between
calls: `$DEPLOOM_MCP_STATE_DIR` if set, else `~/.deploom-mcp`. Default output
roots fall under `<managed>/artifacts/<run-id>`. Nothing is written into the
analysed project.

The managed state is fail-closed too: it is resolved **without creating it**,
and if it overlaps the analysed project in either direction (state inside the
project, or the project nested inside the state) the request is rejected with
`state-inside-project` **before anything is materialised**. Overlap is checked
on both the lexical absolute path (symlinks/junctions preserved) and the real
path after following them, and on the platform-correct case so a link that
routes *through* the project cannot smuggle writes inside it. The same guard
covers the explicit `output_dir` (`output-inside-project`) and the registry
metadata cache. The test suite asserts a full tree snapshot is unchanged after
a rejected request; the registry metadata cache is created only after all
validation passes.

## Response (compact protocol)

On success the tool returns a compact, agent-friendly payload
(`ok: true, protocol: "upgrade-blueprint", ...`) with:

* `run` — `runId`, `projectId`, project name;
* `policy` — the policy that was actually **applied** (`targetLevel`,
  `minLagOkPct`, `exclusions`, `policyHash`), taken verbatim from the produced
  blueprint, never re-derived from the request; a request-vs-applied mismatch
  (e.g. the planner resolved a different scope) is surfaced as
  `policyDiscrepancy` instead of being hidden;
* `status` — `draftStatus`, `verificationStatus`, `authority`,
  `compatibility`, `parallelExecutionSafety`, `partialReason`;
* `summary` — `totalDependencies`, `proposed`, `needsReview`, `statusCounts`;
* `recommendationGroups` — display-priority groups (NOT a parallel-proof order);
* `dependencies` — compact rows (`package`, `declarationId`, `status`,
  `current`, `target`, `group`, `needsReview`, reason, evidence and next action);
* `decisionEvidence` / `decisionCompleteness` — typed reasons, explicit
  lifecycle, critical/blocker counts, artifact-read requirement and separate
  projection/planning/evidence completeness;
* `workspaceCoverage` — discovered/evaluated/unevaluated manifests, bounded
  discovery status and reconciliation requirement; never a joint-solver claim;
* `runtimeProvenance`, `identities`, `planningOptions`, `recovery`,
  `objectiveEvidence` — actual component identity, semantic decision digests,
  cap versus deadline, unchanged evidence and planning-only objective status;
* `truncation` — `{truncated, inlineDependencies, totalDependencies,
  inlineGroups, totalGroups, inlinePackages, totalPackages, byteLimit, bytes,
  limitsHit}` when the payload is clipped to the deterministic display budget
  (default 50 groups / 50 packages per group / 100 rows / 50 critical reasons, capped at 512 KiB of
  serialized JSON). The budget bounds the serialized **payload** (the exact
  `json.dumps(..., ensure_ascii=False, indent=2)` text, including the
  `truncation` block itself — duplicated in the tool result `content` and
  `structuredContent`), not the whole MCP transport envelope. `bytes` always
  equals the exact final size (never a pre-truncation estimate; the test suite
  re-serializes the response and asserts equality). Budget is enforced
  deterministically: caps are reduced step-wise (packages → groups → rows → evidence)
  until the exact on-wire bytes fit, so the same plan always yields the same
  response; `summary.statusCounts` stays complete regardless. If even the
  minimal required payload cannot fit the budget, the call fails closed with
  `response-too-large` rather than returning an oversized answer;
* `partial` — `{partial, replanRequired, processed, processedTotal, reason}`
  when the draft itself was only partially computed (planner deadline /
  `DRAFT_PARTIAL`; a client interrupt does not manufacture this block);
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
* `needsReview === true` — inspect the evidence (breaks, minimum versions,
  security trade-offs) within the user's existing authority. Seek additional
  approval only when the proposed action exceeds it.
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
* `invalid-intent` / `invalid-candidate-cap` / `invalid-evidence-digest` —
  invalid decision contract, candidate limit or previous-evidence identity.
* `decision-contract-unsupported` / `decision-contract-mismatch` — Core cannot
  acknowledge the requested advanced decision contract or applied another one.
* `invalid-deadline` — deadline non-finite (`NaN`/`±Inf`) or invalid.
* `invalid-registry` — registry not an `http(s)` URL, carries credentials, or
  the TTL is finite-negative/`NaN`.
* `invalid-exclude` — `exclude` is not a non-empty list of non-empty strings.
* `output-inside-project` — `output_dir` inside the project or a project parent.
* `output-conflict` — the resolved artifact path already exists (returned
  artifacts were written by an earlier, identical run).
* `project-not-found` — project dir absent.
* `invalid-project` — no `package.json`.
* `core-not-found` — no usable bundled Core or initialized checkout Core.
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
