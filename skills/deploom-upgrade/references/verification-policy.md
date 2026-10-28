# Verification policy

Verify the accepted task in the authorized checkout with real commands and
targeted runtime checks. Keep completion, installation, project checks, runtime
and policy attainment separate. Neither `DRAFT_READY` nor an agent's final reply
proves any of those other states.

## Final-state evidence

Record each check's command, start/end, exit code, status, log and input hashes.
`manifests`, `lockfile` and `environment` identify installation inputs; checks
also bind relevant tracked `source`/configuration. Document paths and hashing
method. Unknown identity stays unknown.

1. Update manifests and lockfile, then perform the package manager's required
   **clean/frozen install** on that final state (`npm ci`, or the appropriate
   project equivalent). `npm install` can change the lockfile; it is not proof
   that a subsequent clean install succeeded.
2. Run bootstrap when required, typecheck, lint, build and project tests. Verify
   resolved target versions in all affected workspaces and inspect peer errors.
3. Run the runtime checks required by changed behavior and the user/protocol.
4. Audit exclusions and every changed declaration against decision records.
5. Audit the requested policy against the final installed state and complete
   intended scope. Exclusions matching the request alone do not prove Yellow,
   Green, security coverage or latest-compatible attainment. If no adequate
   policy audit is available, report policy `NOT_VERIFIED`.

A downgrade, resync install, manifest/lock/environment edit makes an earlier
clean-install result historical. Preserve it, but do not count it for the final
inputs. A source change similarly invalidates affected checks. Re-run only the
required checks invalidated by actual changes; do not repeat unrelated work.

## Targeted runtime checks

Use local data/mocks when available. A missing backend does not by itself prevent
a structural render/navigation check. If a required local check is impossible,
record the concrete limitation and `NOT_RUN`/`NOT_AVAILABLE` rather than PASS.

| Changed surface | Minimal useful checks |
|---|---|
| Router version or route hierarchy | Render representative nested routes and a deep link; check parent/child path matching or Outlet behavior, including a nonempty page and 404. Typecheck cannot detect every empty render. |
| Redirect or navigation APIs | Check destination, preserved query/hash and history Back/replace semantics. Compare with the previous behavior or explicit new requirement. |
| Route parameters | Preserve existing guards; valid, invalid and missing parameters follow the intended 404/error path without an unintended API call. Do not silently replace a format guard with unchecked params. |
| Storybook configuration/decorators | Read shared preview/config and global decorators; render at least one affected story with them active. Check router providers, parameters and hooks actually used by that story. `storybook:build` proves bundling, not execution of the decorator. |
| Widget/loader/API adaptation | Exercise the changed call with a small local mock for required arguments/behavior. Keep backend/payment E2E separately NOT_RUN when unavailable. |

Preserve current behavior unless change is authorized. A source grep can locate
obsolete API usage, but cannot establish runtime correctness by itself.

## Warnings and failures

- Trace a new warning to source/config/shared entry points before scanning all
  dependencies. Identify its package, import path, impacted scenario and severity.
  An absent export/provider warning requires investigation, even with exit 0.
- Do not call a warning "addon-internal", "pre-existing" or harmless without
  evidence. Compare a retained baseline log or the relevant baseline source;
  record unresolved attribution when neither is available. Size advisories are
  different from missing API warnings; do not silently equate them.
- Compiler failures can identify necessary annotations, exports or API repairs;
  they do not automatically prove that no compatible target exists. Replacing
  the compiler in an already changed tree is a version probe, not a baseline run.
- Keep every failure and successful repair. A wrapper launch error is an
  infrastructure/unknown result, not a failing project command. Tool status
  `completed`, an empty log and a copied duration are not substitutes for an
  observed command exit and criterion.

## Status and authorization

Use `PASSED`, `FAILED`, `INCONCLUSIVE`, `NOT_RUN`, `NOT_AVAILABLE` for individual
checks. Summary states additionally allow `NOT_VERIFIED`. `PASSED` requires an
executed check, its criterion and evidence on the relevant final inputs. A waived
check remains not run. Project checks cannot stand in for runtime or policy.

Use already granted user authority for installs and verification. Respect real
client permissions and untrusted lifecycle-script restrictions; record any
suppressed scripts. Do not invent a new confirmation requirement from this file.
Never change global tooling or credentials without authorization.
