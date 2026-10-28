# DepLoom MCP

Headless upgrade-planning boundary and MCP server for the DepLoom ecosystem.

DepLoom MCP exposes the Python planning Core through one tool,
`get_upgrade_plan`. The Core is maintained in `AlexanderLevenskikh/deploom` and
pinned here as `upstream/deploom`; built wheels include that exact headless Core,
the schema and the canonical Skill. Desktop and Electron are not required.

**What MCP does** — produce a structured dependency proposal with explicit
objective, target authority, scope coverage, reasons and unresolved evidence.

**What MCP does NOT do** — Autopilot, background migration jobs, agent
dispatch, repair loops, REST, dashboards, GitLab/Renovate integration, or any
form of verification/execution. The plan is `PLANNING_ONLY` and never claims
compatibility as proven.

## Architecture

```text
Developer
    │
    ▼
OpenCode / other MCP client
    │ get_upgrade_plan
    ▼
DepLoom MCP Server
    │
    ▼
Upgrade Blueprint Adapter
    │
    ▼
Pinned DepLoom Planning Core   (upstream/deploom submodule)
    │
    ▼
Structured Upgrade Blueprint
    │
    ▼
AI Agent  → updates manifests, adapts code, reports verification
```

Deployment topology and contracts are described in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
The boundary contract is detailed in
[`docs/STAGE_01_UPGRADE_BLUEPRINT.md`](docs/STAGE_01_UPGRADE_BLUEPRINT.md);
the MCP server in [`docs/STAGE_02_MCP_SERVER.md`](docs/STAGE_02_MCP_SERVER.md);
the universal agent skill in [`docs/STAGE_03_UNIVERSAL_SKILL.md`](docs/STAGE_03_UNIVERSAL_SKILL.md).
The exact-target evidence contract is in [`docs/DECISION_CONTRACT.md`](docs/DECISION_CONTRACT.md);
experiment preparation and agent handoff are in [`docs/EXPERIMENT_HANDOFF.md`](docs/EXPERIMENT_HANDOFF.md).

## Repository layout

```text
upstream/deploom/          pinned planning core (Git submodule; see release-manifest.json)
src/deploom_mcp/           Python package (blueprint adapter + MCP server)
contracts/                 Upgrade Blueprint JSON Schema (v2)
tests/                     unit, protocol and production-boundary tests
tests/fixtures/            fake planning core + skill/installer fixtures
skills/deploom-upgrade/    canonical Agent Skill (skill + references + examples)
integrations/              per-client adapters (opencode / claude-code / codex)
benchmarks/                Stage 1 + Stage 2 performance measurements
docs/                      architecture and stage documentation
examples/opencode/         OpenCode configuration example
```

## Deploom-upgrade Agent Skill

[`skills/deploom-upgrade/`](skills/deploom-upgrade/SKILL.md) is a single,
client-neutral skill for opencode / Claude Code / Codex that lays out the full
workflow — preflight, `get_upgrade_plan`, safety gates, worktree execution,
verification and a single evidence report. Install it with
[`scripts/install_skill.py`](scripts/install_skill.py)
(byte-identical, idempotent, dry-run/verify supported):

```bash
python scripts/install_skill.py --client opencode --scope project --project /path/to/project
python scripts/install_skill.py --client codex --scope user
python scripts/install_skill.py --client claude-code --scope user --dry-run
```

The historical Stage 3 E2E pilot exercised the earlier Skill revision; it does
not validate the current 2.0 contract. The current bundle has synthetic contract,
evidence and installer checks. A new loaded-client or migration experiment is
still required to evaluate agent behavior; see
[`docs/STAGE_03_UNIVERSAL_SKILL.md`](docs/STAGE_03_UNIVERSAL_SKILL.md). The skill never edits
`upstream/deploom`, never writes MCP configuration itself, and never commits,
pushes or merges.

## Requirements

- Python **>= 3.12, < 3.15** (the pinned Core and the MCP SDK are verified in CI on 3.12).
- Access to `github.com` to fetch the submodule and Python packages.
- Windows and Linux are supported; z3-solver is installed from a platform wheel.

## Installation (from a clean clone)

```bash
git clone https://github.com/DepLoom/mcp.git
cd mcp
git submodule update --init --recursive

python -m venv .venv
.venv/Scripts/python -m pip install -e .      # Windows
# .venv/bin/python -m pip install -e .        # Linux/macOS
```

The source checkout fetches the pinned Core as a submodule. A built wheel is
self-contained and also provides `deploom-install-skill`; see
[`docs/DISTRIBUTION.md`](docs/DISTRIBUTION.md) for build, install and isolated
package checks. No Desktop installation is required.

## Running the MCP server (stdio)

```bash
.venv/Scripts/python -m deploom_mcp           # Windows
# .venv/bin/python -m deploom_mcp             # Linux/macOS
```

List of published tools: exactly one — `get_upgrade_plan`.

### `get_upgrade_plan` (contract)

```jsonc
{
  "project_path": "/abs/path/to/project",   // read-only; a package.json is required
  "target_level": "yellow",                  // "yellow" | "green"  (default "yellow")
  "min_lag_ok_pct": 80,                      // minimum registry-lag % for a clean proposal
  "deadline_seconds": 20,                     // default 20, clamped to [5, 600]
  "include_prerelease": false,               // allow prerelease targets
  "registry": null,                          // optional npm registry; must be an http(s) URL
  "registry_metadata_ttl": 3600,             // packument TTL seconds for the shared cache
  "output_dir": null,                        // optional; MUST be outside the project
  "exclude": null,                           // explicit health-scope exclusions
  "target_mode": "pinned",                   // "pinned" | "advisory"
  "objective": "minimum-change-to-policy",   // or "latest-compatible-in-approved-lines"
  "mutable_packages": null,                 // null = all; [] = none; others remain constraints
  "approved_version_lines": {},              // package -> allowed major numbers
  "draft_max_candidates": 3,                 // evidence cap, separate from the deadline
  "previous_evidence_digest": null           // diagnose unchanged published evidence
}
```

All inputs are validated fail-closed before any work starts: non-finite
deadlines, out-of-range `min_lag_ok_pct`, registries with credentials, invalid
TTLs and malformed `exclude` lists are rejected with machine-readable error
codes (`invalid-deadline`, `invalid-policy`, `invalid-registry`,
`invalid-exclude`) instead of being forwarded to the planner. Write boundaries
are fail-closed too: an `output_dir` overlapping the project (`output-inside-project`)
and a managed state overlapping the project (`state-inside-project`) are
rejected **before any directory is created**. Overlap is checked on both the
lexical absolute path (symlinks/junctions preserved) and the real path after
following them, so a link that routes *through* the project cannot smuggle
writes inside it.

The response is a **compact** representation of the Upgrade Blueprint
(`protocol=upgrade-blueprint`, run, status, summary, recommendation groups,
compact dependency rows, artifact paths) plus planning boundaries. The full
Blueprint and raw planner artifacts are written under a controlled artifact
directory whose paths are returned; an AI agent reads them with its own file
tools.

The inline rows are **bounded deterministically** (default 50 groups / 50
packages per group / 100 rows, capped at 512 KiB of serialized JSON): when
there are more, the `truncation` block reports the exact clipped counts
(`inlineGroups/totalGroups`, `inlinePackages/totalPackages`,
`inlineDependencies/totalDependencies`, plus `byteLimit/bytes/limitsHit`)
while `summary.statusCounts` stays complete. The budget bounds the serialized
**payload** and `truncation.bytes` equals its exact final size (the test
suite re-serializes the response and asserts equality); if even the minimal
required payload cannot fit, the call fails closed with
`response-too-large`. The response also carries the policy that was actually
**applied** (`policy.{targetLevel,minLagOkPct,exclusions,policyHash}`), with a
request-vs-applied mismatch surfaced as `policyDiscrepancy`.

A deadline-exhausted run returns a `DRAFT_PARTIAL` plan with
`partial.replanRequired=true`; that partial result is produced by the planner
itself, **not** by client cancellation. Because the tool is async, client
cancellation (e.g. the SDK's per-request read-timeout abandon, which sends
`notifications/cancelled`) reliably kills the whole planner process tree —
verified over a real stdio session; the interrupt yields no manufactured
partial blueprint, only the tree-kill cleanup, and the server session stays
alive (see [`docs/STAGE_02_MCP_SERVER.md`](docs/STAGE_02_MCP_SERVER.md)).

The response is always `NOT_VERIFIED / PLANNING_ONLY / UNKNOWN / UNPROVEN`: a
`PROPOSED` verdict means "this upgrade **can** be proposed", not "already
updated", and `needsReview` requests evidence review within existing user authority.
It does not require asking the owner again for an already authorized action. Machine-readable errors use `{"ok": false, "error":
{code, message, ...}}`; see
[`docs/STAGE_02_MCP_SERVER.md`](docs/STAGE_02_MCP_SERVER.md) for the full
contract, error codes and partial-plan handling.

## Connecting OpenCode

See [`examples/opencode/README.md`](examples/opencode/README.md) (pointer to
[`integrations/opencode/README.md`](integrations/opencode/README.md), which
covers both the MCP server and the `deploom-upgrade` skill). The example
config is meant to be copied into your own OpenCode configuration; this
repository never modifies global OpenCode configuration.

## Tests

```bash
.venv/Scripts/python -m unittest discover -s tests -v
```

The suite covers unit projection tests, real MCP protocol tests over stdio, a
production-boundary test that runs the **real** pinned planner against a real
fixture, 17+ deterministic hardening tests that run the boundary against a
**fake planner core** (`tests/fixtures/fake_core/`, no network) to pin down the
contract validator, hash verification, JSON Schema check, filesystem write
guards, exclusions and the subprocess runner lifecycle, and 20 offline
skill/installer tests (`tests/test_skill_contract.py`,
`tests/test_skill_install.py`). CI runs the same suite on a clean checkout
across Windows/Linux and Python 3.12/3.13, a `scripts/smoke_stdio.py`
startup/clean-stdout check, and an installer smoke on all four install
configs.

## Updating the Core

The Core is a pinned submodule. Updating it is an explicit, reviewable
dependency change:

```bash
git -C upstream/deploom fetch origin
git -C upstream/deploom checkout <new pinned SHA>
git add upstream/deploom
git commit -m "chore(core): pin deploy core to <SHA>"
```

Then refresh `release-manifest.json` and re-run the full test and distribution
checks in [`docs/DISTRIBUTION.md`](docs/DISTRIBUTION.md). Do **not** edit files
inside `upstream/deploom`.

## License / distribution

This repository does not re-license the upstream Core or add an open-source
license. The headless build can be validated locally; these changes do not
publish a package to a registry or alter distribution terms. The repository
owner must select the intended terms before a public package release.
