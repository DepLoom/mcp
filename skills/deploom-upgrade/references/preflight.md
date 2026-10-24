# Preflight

Run before **any** planning request and again before execution. If any check
that matters for the requested mode cannot be completed reliably, the result is
`BLOCKED` (report the missing capability; never guess past it).

## The checks

1. **Git root, HEAD, branch.** `git rev-parse --show-toplevel`,
   `git rev-parse --abbrev-ref HEAD`, `git rev-parse HEAD`. Record them as the
   baseline. If `git` is unavailable or the project is not a git repo, block
   execution (worktree isolation is required for `EXECUTE`); `PLAN_ONLY` can
   still proceed read-only on manifest+lockfile.
2. **Working tree state.** `git status --porcelain`. A dirty checkout is fine
   for `PLAN_ONLY`. For `EXECUTE`:
   - Do **not** auto `git stash` / `git reset --hard` / `git clean` to make
     room — that would mutate the original checkout.
   - Prefer an already-clean checkout; if it is dirty, plan against the actual
     current tree, then prove the worktree matches the plan inputs (see
     execution-policy "Source identity across the worktree") or re-plan inside
     the worktree.
3. **Package manager & lockfile.** Detect from lockfile + tooling:
   `package-lock.json` → npm, `yarn.lock` → yarn, `pnpm-lock.yaml` → pnpm,
   `bun.lockb` → bun. The planner reads the manifest and lockfile; the same
   package manager must be used to update the lockfile during execution.
4. **Node.js version & engines.** `node --version`; read `engines` in
   `package.json` and check the toolchain satisfies it. A planned upgrade must
   not force a Node version the environment cannot run.
5. **Manifest & workspace configuration.** `package.json` present at
   `project_path`; detect `workspaces` / monorepo layout. The planner handles
   per-project analysis; record which project(s) the plan covers.
6. **Available check commands.** From `package.json` `scripts` discover
   `lint` / `typecheck` / `build` / `test` (and any CI-observed commands).
   Record them; they become the verification pipeline. If a script is absent,
   it is `NOT_AVAILABLE`, not skipped silently.
7. **Registry configuration.** Note `.npmrc` / `publishConfig.registry` /
   environment registry. Pass an explicit `registry` to `get_upgrade_plan` when
   the project pins one, so planning matches installation.
8. **Target level.** From the user request: `yellow` (default) or `green`.
   If the user did not state it and intent is ambiguous, ask.
9. **Exclusions.** The exact set of packages that must not change (e.g.
   `react`). Confirm the set with the user if there is any doubt; the planner
   enforces it via `exclude` **before** planning.
10. **Client file permissions.** Confirm the coding client is allowed to read
    the project and (for `EXECUTE`) create files, run `git worktree` and shell
    commands. If a needed permission is missing/denied → `BLOCKED`, never
    bypass the client's sandbox.
11. **`get_upgrade_plan` availability.** Confirm the MCP tool is present in the
    client's tool list. If not → `BLOCKED` and point at the integration
    instructions (`integrations/<client>/README.md`).
12. **Worktree feasibility.** For `EXECUTE`: confirm `git worktree add` is
    supported and a target path exists (outside or alongside the checkout, not
    inside it in a way that would confuse the package manager).

## Blocked conditions (summary)

`BLOCKED` (report, do not act) when any of the following is true:

- `get_upgrade_plan` is not reachable;
- the project is not a git repo (for `EXECUTE`);
- the checkout cannot be proven clean or the worktree cannot be proven to match
  the plan inputs;
- the client lacks a permission required for the operation;
- a requested exclusion conflicts with the action (see execution-policy);
- intent or target level is ambiguous and the user did not resolve it.

## Output of preflight

Keep a short machine-readable record to carry into the evidence report:
`baselineCommit`, `branch`, `packageManager`, `nodeVersion`, `checkScripts`,
`registry`, `targetLevel`, `exclusions`, `mcpAvailable`, `worktreeFeasible`.
