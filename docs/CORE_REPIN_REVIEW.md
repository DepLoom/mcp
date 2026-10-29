# P2.5: Reviewed Core diagnostics pin for MCP (stacked PR)

## Dependencies and boundaries

- Base: **DepLoom/mcp PR #6** (`feat/registry-diagnostics-projection-20261029`), not `main`. The diff of this dependent PR must contain only the Core gitlink repin, regenerated release inventory, regression guards and this note. Review and merge the base PR before integrating this dependent PR; no merge or release is part of P2.5.
- Source Core: **DepLoom/core PR #4** commit `c6bc30e47d51b02d7b907af5a05cd0a638529c28`, not merged into Core `main`. The Git submodule points at that exact commit. Do not silently repoint the gitlink to a Core `main` that lacks the diagnostics.
- Old embedded Core: `ba35b37c311fba13937ad4b2f46f760d57d9ab11` (`1.0.0-preview.20`). New Core is also `1.0.0-preview.20` — the upstream version string is not changed. If a new released version is required, use the separate release process; do not rename it in this PR.

## Review outcome (Core PR #4 and MCP PR #6)

- Core's origin/path guard remains the one and only candidate admission predicate. `registry_candidate_diagnostics.py` classifies its rejections; `NO_INSTALLABLE_ARTIFACT` applies only to a candidate-search universe where **all examined newer candidates are structurally rejected**. It is neither a solver verdict nor proof of physical artifact installation.
- MCP's `registry_projection.py` copies only whitelisted diagnostic fields and code/counts. Full Blueprint includes sanitized identities and at most four samples; compact protocol excludes registry origins, paths and sample versions, preserving the existing byte budget. An old Core that lacks these fields stays unknown/absent, never zero-known-clean. The existing JSON Schema is optional, version 2; no API version bump.
- E2E parent-versus-PR comparison in P2 and P1.5 retained matching target `lodash@4.18.1`, foreign `NO_TARGET` without target, security-known 1/1, and `PLANNING_ONLY` / `NOT_VERIFIED` authority. That was previously tested through the explicit temporary Core generator override. This repin removes the need for the override in an installed wheel.
- P1.5 and P2 cross-PR smoke covered both code paths; CI must additionally validate the **actual repinned wheel**, because projecting a local checkout's Core is not equivalent to shipping the right files in a wheel/sdist.

## Release inventory provenance

- 133 previously pinned Core resources: two changed modules (generator and lockfile_consistency) after P2.6; 131 others must match their old published SHA-256 byte for byte.
- 134 new Core resources: new `registry_candidate_diagnostics.py`. Regenerate `core.files`, `core.contentSha256`, and `core.revision` with `scripts/update_release_manifest.py`; leave `schemaSha256`, `skillManifestSha256`, `skill.bundleSha256`, `mcpVersion`, and the complete Core semantic version unchanged.
- `scripts/update_release_manifest.py --check` requires the checked out clean gitlink and must pass on Windows/Linux CI. `smoke_wheel.py` imports the new Core diagnostics module from the *installed wheel*'s private `_core` path; this prevents false success from a checkout or a global package.
- The OSV workflow now triggers on the gitlink or release manifest change; otherwise a repin PR could pass headless CI and silently skip the only real network check.

## Non-goals

- No tarball guard weakening, solver changes, Core source edits, module version/release bump, MCP API/Skill changes, agents, benchmark runs or merge.
- A valid `get_upgrade_plan` remains planning-only; it cannot prove npm installability or compatibility.

## Post-review acceptance gates

1. Core PR #4 3-platform CI green; MCP PR #6 4-platform CI, native audit and real OSV smoke green. Record both head SHAs unchanged in the dependent PR.
2. Repin branch passes GitHub Actions Core checkout, `scripts/update_release_manifest.py --check`, four-platform MCP CI, wheel/sdist installed smoke, Skill integrity and actual stdio/OSV through real CONNECT proxy.
3. Evidence that a real installed wheel uses the new pinned gitlink and exposes `registryCandidateDiagnostics` and `noTargetDiagnosticCode`, not just `DEPLOOM_MCP_GENERATOR_PATH` override. Full JSON Schema, compact bounds and read-only source invariants must hold.

## P2.6 security review / repin update

This stacked PR now targets Core PR #4 hardening commit `c6bc30e47d51b02d7b907af5a05cd0a638529c28` and inherits the separate MCP PR #6 sanitization/counter-consistency update. Core now rejects dot/encoded traversal, userinfo, query-bearing tarballs, malformed ports and out-of-prefix redirects **before** contacting their destinations. The installed-wheel smoke asserts the new guard and the bundled `lockfile_consistency.py` hash. Full CI and live OSV must be rerun on the exact updated repin head; no merge/release is approved.
