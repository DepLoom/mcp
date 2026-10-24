Use the `deploom-upgrade` skill available in this project to run a real dependency upgrade in EXECUTE mode, exactly per the skill.

Honor these facts exactly:
- Private registry: `http://127.0.0.1:4873`. Pass `registry="http://127.0.0.1:4873"` to `get_upgrade_plan` and use that same registry for every npm command (the project `.npmrc` already points npm at it). Never touch the public npm registry.
- Exclusions: `exclude=["kontur-fixture-c"]`. `kontur-fixture-c` must remain at `3.0.0` in both `package.json` and `package-lock.json`, before and after.
- Target level `yellow`, `min_lag_ok_pct=80`.
- Create the git worktree OUTSIDE the project directory (e.g. a sibling directory next to this project), not inside it.
- Write the evidence report JSON to `<worktree>/deploom-evidence.json` following `references/evidence-report.md`, and finish with a short user-facing summary listing what you updated, what you excluded (kontur-fixture-c untouched), which checks ran, and the worktree path.
- Do NOT commit, do NOT push, do NOT create a merge request, do NOT delete the worktree, do NOT touch the original checkout's tracked files.

Rules: follow the skill's safety gates; if any gate fails, report `BLOCKED`; if checks fail after your bounded repair budget (max 3 attempts per root cause), report `FAILED`; never invent verification results; a successful plan is not a successful migration until the worktree checks pass.
