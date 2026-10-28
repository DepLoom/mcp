# Read-only dependency audit (opt-in MCP server)

This feature is developed separately from the frozen 13-session A/B build.
Neither the default planning server, the pinned Core nor the Skill is changed.

Run the audit-enabled server with the same interpreter/environment as MCP:

```sh
python -m deploom_mcp.audit_server
```

It exposes `get_upgrade_plan` **and** `audit_dependencies`. The ordinary
`python -m deploom_mcp` remains planning-only. Do not change the executable,
tool list or commit midway through the frozen experiment.

## Request

```json
{
  "project_path": "/absolute/path/to/project",
  "package_manager": "auto",
  "registry": null,
  "deadline_seconds": 120,
  "include_lag": false,
  "lag_months": 12,
  "output_dir": "/absolute/path/outside-the-workspace"
}
```

`package_manager` is `auto`, `npm`, `yarn-classic` or `yarn-berry`. An explicit
value must agree with the canonical inputs. pnpm, mixed npm/Yarn lockfiles,
missing locks, contradictory `packageManager` and unsafe output paths fail
closed. The operation never creates a missing project lock by installing or
resolving dependencies. Deadlines are finite, 5–600 seconds.

A member path resolves to the owning lockfile root; the response explicitly
reports the root and discovered manifests. The requested member must belong
to that workspace. This is a **root-scope audit**, not a filtered member audit.
Unsupported local dependencies, unresolved canonical Yarn edges, skipped
workspace symlinks and changed source inputs remain visible as incomplete
coverage. No joint workspace upgrade assignment is computed.

## Package-manager paths

| Project | Audit engine | What is and is not preserved |
| --- | --- | --- |
| npm | `npm audit --json --package-lock-only --ignore-scripts` | Existing npm lockfile in an isolated copy. No `node_modules` interpretation or physical install proof. |
| Yarn Classic | npm audit over Core's synthetic exact-version inventory from `yarn.lock` | No `npm install`, bridge re-resolution, or tarball download. Package/version evidence is retained; synthetic paths are not installation paths and fixes are not evaluated. |
| Yarn Berry | `yarn npm audit --all --recursive --json` | Native all-workspace/direct+transitive registry audit. Yarn 4 uses `--no-deprecations`. Prepared Yarn Berry must already be on PATH. |

Core's existing `manual_dependency_audit.py` supplies canonical Yarn inventory,
manifest interpretation and optional publication-date lag. No second solver
or independent Yellow/Green policy evaluator is introduced. The wrapper
strictly validates native JSON/NDJSON, separates units and keeps incomplete
security evidence from looking like zero findings.

## Read-only and network boundaries

The source checkout is read only. A fresh private snapshot contains manifests,
canonical locks and a declarative subset of registry/auth/TLS configuration.
All native commands run there. The only commands allowed are npm/Yarn version
probes, native audit and optional `npm view` queries for lag. **No install,
`audit fix`, package script, project `yarnPath`, or project Yarn plugin runs.**
The wrapper checks hashes of the bundled Core inventory before invoking it.

Audit sends package names and versions to the **effective configured audit
registry**, as native npm/Yarn do. An explicit `registry` overrides it;
credentials in URLs, query strings and fragments are rejected. The wrapper
never falls back from a private registry to a public one. Project/user npm
registry credentials and declarative Yarn registry/auth settings are used
only in the disposable snapshot and removed afterward. Credentials are
redacted from retained evidence; configuration that executes code or filters
away advisory classes is not forwarded. CA/client-certificate paths may
require host configuration; missing credentials or inaccessible endpoints
remain errors, not clean audits. Native tooling itself must be trusted.

The async runner kills the entire worker/native process tree on timeout or
cancellation. Private snapshots are removed even on exceptions. Only sanitized
bounded evidence persists under a unique managed `audits/<run-id>/audit.json`,
outside the whole canonical workspace. Tool annotations are read-only to the
project and open-world because registry requests occur.

## Result semantics

`ok=true` means a structured report was obtained, not that dependencies are
safe. `status=COMPLETE|INCOMPLETE|ERROR` describes evidence collection.

- `security.verdict=FINDINGS`: known advisory evidence exists.
- `NO_KNOWN_FINDINGS`: a recognized complete response reported no findings for
  the captured lockfile inventory. This is not an exploitability/security guarantee.
- `UNKNOWN`: evidence/coverage failed or changed. Available observed findings
  are retained, but incomplete evidence is not replaced with zero counters.

`auditExitCode` and `workerExitCode` are independent. Native exit 1 with valid
findings is not a process/infrastructure failure. Empty/malformed/unknown JSON,
auth/network errors, contradictory counters and truncated native output cannot
become a successful clean scan.

Counts separately expose unique advisories, affected package records and, when
provided by npm, reported node counts. Yarn Classic inventory entries represent
package/version pairs, **not physical nodes**. These quantities must not be
added together. Full findings remain in the artifact when the compact response
is clipped; counts and uncertainty survive, with `requiresArtifactRead=true`.

The report does **not** assert installed-tree equivalence, manifest/lock
synchronization, runtime correctness, exploitability or Yellow/Green closure.
An audit of the current lock is not verification of a proposed upgrade.

### Optional lag

`include_lag=true` uses the existing Core publication-date check. It reports
manifest-declared version semantics (including a range's version token), **not
necessarily the installed/resolved version**. Lag coverage and security
coverage are separate. No automatic policy PASS is derived from the data.

## Tests

`test_dependency_audit.py` covers parsing, malformed errors, deduplication,
manager/lock disagreement, input validation, output clipping, secret redaction
and command restrictions. `test_audit_stdio.py` exercises the actual opt-in
server and its two advertised tools.

The `audit-native` workflow provisions trusted Node/npm and Yarn 4.9.2 outside
all target projects, builds and installs the wheel outside checkout, then runs
`test_audit_native.py`: real npm, exact Classic inventory, native Berry,
positive clean controls and registry failure against a localhost HTTP fixture.
It verifies unchanged source bytes, no project install/script execution and
no dependency tarball fetch. These are engineering fixtures, not a migration
benchmark or measurements of product speedup. Core and Skill release pins stay
unchanged. Older Berry output shapes have parser fixtures; a native CLI matrix
beyond the recorded versions is not claimed.

## Upstream command documentation

- npm audit: https://docs.npmjs.com/cli/v11/commands/npm-audit/
- Yarn audit scope/options: https://yarnpkg.com/cli/npm/audit
- Yarn configuration: https://yarnpkg.com/configuration/yarnrc/
