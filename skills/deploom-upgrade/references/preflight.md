# Preflight

Collect the facts that affect this task, without an automatic permission round.
Read existing user authority first. Record missing evidence as unknown.

## Task and workspace

1. Record action (`PLAN_ONLY`/`EXECUTE`), objective, target mode, approved version
   lines, mutable packages, exclusions and permitted companion/source/config
   repairs. Preserve explicit user pins and environment restrictions.
2. Record git root, HEAD, branch and existing diff. Never stash, reset, clean or
   discard user work to make execution possible. A plan can inspect a dirty tree.
3. Use the checkout the user assigned, including an existing isolated worktree.
   Otherwise prefer an isolated working copy for execution. Create another
   worktree only if compatible with the task; do not require duplicate isolation.
   Without Git, plan-only still works; explain how to retain a reviewable baseline
   before executing rather than claiming a fictitious commit.
4. Discover all root/workspace manifests, package manager/lockfile, Node engines
   and actual runtime. Keep the whole graph as compatibility context even when
   only a subset may change. `mutable_packages` limits edits; it is not an
   instruction to exclude contextual peers from analysis.
5. Read configured registry and package scripts. Pass the registry to the MCP;
   never expose credentials. Discover bootstrap, typecheck, lint, build, tests,
   Storybook and runtime entry points affected by proposed updates.
6. Record which checks are required by the user/protocol and already authorized.
   Resolve project-rule conflicts using the user's explicit decision. Do not ask
   again for authority already granted. A genuine client denial is still a block.
7. Confirm `get_upgrade_plan` availability. If absent, report infrastructure
   unavailability; do not present a manual plan as a DepLoom verdict.
8. Record expected contract identity from `contract.json`, installed receipt if
   available, and actually loaded Skill identity separately. An on-disk match
   is not evidence of the current client's loaded content.

## Baseline and fingerprints

Before edits, retain a baseline manifest map and identities of inputs actually
used. For later verification, use deterministic, separately named hashes for
`manifests`, `lockfile`, `environment` and relevant `source`/configuration.
Hash sorted project-relative paths and file contents; exclude generated output,
node_modules and the evidence report itself from source identity. Record the
algorithm and covered paths. If a required identity is unavailable, keep it
unknown; never guess a SHA.

Core `source.inputHashes` may be an opaque combined fingerprint, not these
per-category hashes. Preserve it unchanged. Compare real input bytes or a
documented Core identity; re-plan if pre-application inputs changed. When moving
to a new worktree, prove equivalence of relevant bytes instead of assuming that
the same HEAD includes uncommitted changes.

## Minimal acceptance contract

Before execution, record:

- user authority references, objective and target-selection rules;
- mutable names plus allowed companions and all workspace context;
- required install/build/test/runtime checks and policy criterion;
- applicable execution deliverables from the user/Core workflow, indexed once;
- baseline identity and allowed working copy;
- what would require another decision: a new major line, excluded edit,
  environment change, material behavior change or a revised pinned target.

Use `references/evidence-report.md`. No artificial file-count limit applies to
authorized compatibility repairs. Distinguish an unavailable check from a check
the user waived. A waiver narrows acceptance; it does not manufacture PASS.

For blinded experiments, keep a shared frozen-input directory and an assigned
slot. Do not read neighboring worktrees, caches of their reports or results.
Use only the evidence available to this run to diagnose failures.
