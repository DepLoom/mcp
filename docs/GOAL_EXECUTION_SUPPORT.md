# Goal execution: supported boundary and evidence

This document describes the opt-in goal route in the engineering implementation.
It does not change the existing health planner or announce a public release.
A plan, an installed dependency tree, a checked project and a current checkpoint
are separate results. Only the required checks of the agreed profile establish
the selected goal; they do not prove arbitrary JavaScript behavior.

## Route and authority

- `get_goal_plan`: exact V1, explicit source-repair V2, or finite coupled V3.
  Exact targets do not need combinatorial search. V3 uses the Core/Z3 model;
  its assignment still needs full-tree package-manager and project checks.
- `verify_goal_candidate`: requires the operator's pinned goal, named checks,
  source/toolchain identities, execution registration and exact delta grant.
  It checks an isolated candidate. Its accepted result is historical candidate
  evidence, not permission to change the registered workspace.
- `apply_goal_candidate`: requires a separate final capability. It preserves
  owned source/dependency preimages, materializes the approved tree, executes
  the mandatory profile, seals the result and publishes a durable checkpoint.
- `get_goal_checkpoint`: current mode checks physical continuity and the
  registered environment within a non-renewable read budget. Terminal replay
  cannot spend another install/check attempt. Explicit historical mode verifies
  stored evidence and never turns it into a current result or write capability.

Inspect `currentWorkspaceVerified` separately from the historical checkpoint
status and `goalChecksComplete`. A changed output can retain a valid historical
checkpoint while current verification is false. Imported `get_goal_report`
records remain client-reported; they cannot manufacture owner authority.

## Declared execution matrix

| Surface | Validated scope | Important boundary |
|---|---|---|
| OS/toolchain | Native Windows; acceptance Python 3.12, Node 24.18/npm 11.16 | Other allowed parser/runtime versions are not evidence of an equivalent real-project acceptance |
| Package manager | Standalone root npm lock v2 and v3 | pnpm/Yarn/workspace execution stays unsupported; inventory is a different capability |
| User objective | Explicit functional-maintenance REPORT_ONLY | No silent replacement of a Yellow/Green/security requirement |
| Repair | Explicit source paths and before hashes | No arbitrary repair scope from agent prose |
| Coupled choice | Approved finite domains and exact derived lineage | Root constraint feasibility does not guarantee npm transitive placement or runtime behavior |
| Partial results | Incomplete Draft scope stays visible; exact MCP execution rejects partial acceptance | No general accepted partial-execution claim |
| Environment | Host-owned explicit mapping for the selected profile; owned process trees | No OS sandbox against an arbitrary hostile writer or arbitrary external JS inputs |

The selected real acceptance corpus contains an API repair (pino-pretty), a
coupled Vite/plugin choice (react-crash-2024), and an exact esbuild update (dotenv).
Their hash-bound records retain their own Core/wheel identities. They are not
three fresh executions on every subsequent engineering build. The original
network-blocked scenario remains user-deferred, not a passing case.

For engineering wheel 8a0b6e77 (Core aefe83a), all three
selected projects now have acceptance records. The current S3 record includes
463 dependency instances with no unknowns, 17 held direct roots, the original
four commands, 12 HTTP checks, six real browser checks, terminal replay and a
fresh current read after runtime. Historical checkpoints keep their original
identities. Measurement-helper admission failures and their bounded recovery
are retained; they are not Core fixes. S3 cold component profiling has an
unattributed entry-wrapper span. The reserved warm route subsequently passed
the same functional checks; its checkpoint remains separate from cold.

## Generated outputs and source transport

The [standalone output contract](STANDALONE_OUTPUT_RECEIPTS.md) requires fresh
`dist`/`.tap` roots, the pinned original recipe, complete bounded inventories and
receipts bound to source, lock, profile, invocation, environment and toolchain.
Explicit input-only copying transports the recipe but produces no verification
receipt. Generic source proofs still reject receiptless output exclusions.
Current proof physically checks the recorded generated outputs again.

[Platform archive](../upstream/deploom/docs/NPM_PLATFORM_ARCHIVE_RESOURCE_V1.md),
[historical identity](../upstream/deploom/docs/NPM_HISTORICAL_IDENTITIES_V1.md), and
[unchanged alias](../upstream/deploom/docs/NPM_V2_UNCHANGED_ALIASES_V1.md) compatibility preserve their
provenance, exact scope and denial rules. These are not blanket exclusions of
missing dependencies or authorization to normalize a project lockfile.

## Migration references and performance claims

[Official migration evidence](OFFICIAL_MIGRATION_EVIDENCE.md) is optional and
reference-only. The initial catalog covers two exact transitions. Syntax
observations are not semantic applicability; unknown coverage stays explicit.
Cached immutable documents can avoid HTTP, but the strong baseline receives the
same documents. Unique migration, time, token and business value is not proven.

One engineering whole-route pair completed for pino-pretty on 2026-11-03:
971.469 seconds cold and 965.359 seconds warm. Only the immutable official
reference cache was treated; source, target, checks and initial npm cache matched.
Both arms passed final/current/replay with 774 instances, no unknowns and the
original coverage. HTTP transactions fell from 11 to zero for identical pinned
sources; reference retrieval measured 11.687 versus 3.297 seconds. The observed
0.629% whole-route difference is not a proven causal speedup: this was one fixed
order pair with uncontrolled OS/disk/load. The underlying hash-bound records are
local engineering evidence, not a public-release certificate.

A qualified S3 pair also completed: successful owned-phase sums were 742.563
and 685.030 seconds, while raw orchestrated intervals were 1320.269 and 1004.181
seconds. Cold included retained measurement-helper failures and recovery, so
these are not a causal whole-route speed comparison. Pinned official retrieval
fell from 11.985 to 3.125 seconds and HTTP transactions from ten to zero.
Imported model/Z3 phases were observed; the worker entry wrapper remains a
residual and process association through Windows launchers is qualified.
The exact dotenv acceptance has no separate cold/warm whole-route profile.

A subsequent engineering wheel 04edfef5 keeps the same Core and uses at most
four concurrent readers for its bounded Core-file integrity loop. Every call
still reads and hashes fresh bytes, checks symlinks and resolved containment,
then checks module origins before model use; no integrity PASS is cached.
On the same S3 inputs and Core directory, two full-model component comparisons
measured 0.224/0.264 seconds serial versus 0.173/0.211 seconds with four readers,
with identical model/assignment semantics. The installed boundary gate passed
180 tests with one declared native leaf-symlink creation skip; real NTFS junction
and logical symlink denial passed. Actual stdio planning and metadata-tamper
rejection also passed. This is a local component result, not a whole-route
speedup or three fresh real-project acceptances on wheel 04edfef5. The earlier
real-project checkpoints retain wheel 8a0b6e77 identities.

Cache-seeded acceptance runs, endpoint timings, synthetic solver tests and parser
performance gates are not interchangeable speedup evidence. A rejected
optimization is not promoted merely because its unit tests passed. Current
selected-profile acceptance is not a claim of universally faster migrations,
universal security or public-release readiness.
