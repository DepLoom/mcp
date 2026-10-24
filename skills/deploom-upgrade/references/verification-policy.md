# Verification policy

Rules for proving what the upgrade actually did. Verification always runs on the
**worktree** (never the original checkout) and uses the project's real commands
and the coding client's own tools.

## Check pipeline

Run every relevant check; a check that cannot run for a legitimate reason is
recorded as such, it is never skipped silently:

1. **Install / lockfile validation** — install with the project's package
   manager (`npm ci` or `npm install`, `yarn`, `pnpm`, matching the lockfile)
   and confirm the lockfile is in sync and the resolved versions match the plan.
2. **Typecheck** — the project's typecheck script (`tsc --noEmit` etc.).
3. **Lint** — the project's lint script.
4. **Build** — the project's build script.
5. **Tests** — the project's test script.
6. **Policy audit** — re-confirm: no `policyDiscrepancy`; applied exclusions
   equal requested; excluded packages unchanged in manifest and lockfile.
7. **Diff audit** — every manifest/lockfile change traces to a `PROPOSED` row;
   nothing outside the intended change set; excluded packages untouched.

Order matters (fail fast), but each check that can run still runs to produce a
complete record.

## Status vocabulary

Each check gets exactly one of:

- `PASSED` — the check ran and its criterion holds.
- `FAILED` — the check ran and its criterion failed.
- `INCONCLUSIVE` — the check ran but produced no clear verdict.
- `NOT_RUN` — the check was not executed (e.g. no test script, user said skip).
- `NOT_AVAILABLE` — the tool/command does not exist in this project.

Rules:

- A check that did not run is **never** `PASSED`.
- DepLoom's internal verdicts (`PROJECT_VERIFIED`, `CONSTRAINT_SATISFIED`) are
  planning vocabulary and are **never** used as verification results here —
  they do not exist for a planning-only blueprint.
- For every check record: command, exit code, result, elapsed time (when
  measured), failure/skip reason, and any diagnostic artifacts (paths to logs,
  output excerpts, diff).

## Baseline vs regression

If the project already had failing checks before the upgrade, distinguish a
**baseline failure** (pre-existing, unrelated) from a **regression** introduced
by the update:

- Re-run the same check on the baseline (original checkout or `baselineCommit`
  worktree) if feasible, and compare.
- Report both facts (baseline failed too / new failure) instead of a single
  binary.
- A regression should normally be handled by the bounded repair loop (≤ 3
  attempts per root cause) or reported as `FAILED` with diagnostics.

## Install safety for untrusted projects

Package lifecycle scripts (`preinstall`, `postinstall`, `rebuild`, …) are not
unconditionally safe.

- For projects that are not in the trusted set: perform the install with a safe
  mode where lifecycle scripts are suppressed
  (`npm install --ignore-scripts`, or the yarn/pnpm equivalent) when that does
  not break the build, and note that scripts were not run.
- Otherwise ask for user confirmation before letting scripts execute.
- Never auto-negotiate registry credentials or write credentials anywhere.

## Recording

Collect every check result into the evidence report's `verification` array
(see `references/evidence-report.md`). The report is the transcript of what was
actually executed, measured and observed — nothing more.
