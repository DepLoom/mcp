# Optional Core registry diagnostics in MCP (issue #5)

The Core diagnostic PR [DepLoom/core#4](https://github.com/DepLoom/core/pull/4) adds a *structural*, bounded explanation for versions rejected by the existing registry origin/path guard. This MCP change projects the new evidence **only when the invoked Core published it**. The current pinned Core remains unchanged and does **not** emit these new properties. The JSON Schema remains `schemaVersion=2` with optional additions; no change to solver targets, policy or release Core pin is implied.

## Full Blueprint (`artifacts.blueprint`)

- `registryDiagnostics`: aggregated `schemaVersion=1`, `authority=STRUCTURAL_REGISTRY_EVIDENCE_ONLY`, `examinedVersions`, `rejectedVersions`, `noInstallableArtifactRows`, and `rejectionCounts`.
- `dependencies[].registryCandidateDiagnostics`: observed counts, `rejectionCounts`, at most four validated `rejectionSamples` and `rejectedArtifactOrigins`, sanitized configured registry origin/path, and *already-observed* `physicalProbe` status. It can include `projectionTruncated=true`; raw published Core `artifacts.plan` remains the more complete evidence.
- `dependencies[].noTargetDiagnosticCode`: one of `NO_INSTALLABLE_ARTIFACT`, `NO_PUBLISHED_UPGRADE_CANDIDATE`, `UNRESOLVED_TARGET_CONSTRAINT_OR_POLICY` for a `NO_TARGET` row, or `CANDIDATE_CAP` for a `CANDIDATE_SEARCH_TRUNCATED` row.

No field is created for the old bundled Core if the Core did not emit it. An absent field means *not evaluated by that Core*, **not zero known rejected candidates**. The full Blueprint refuses to assert `MATERIALIZED`, `PROJECT_VERIFIED`, or physical compatibility.

## Compact `get_upgrade_plan` response

- `registryDiagnostics` is the same small, whitelisted aggregate (including any `projectionTruncated` marker).
- Every **inlined** `dependencies[]` row may include `noTargetDiagnosticCode` and a restricted `registryCandidateDiagnostics` containing counts, boolean structural verdict, reason histogram and a stable `evidenceRef` pointer `#/dependencies/N/registryCandidateDiagnostics` into the full Blueprint.
- The compact response **never** includes raw tarball URLs, rejected hostnames, metadata URL paths, example versions, credentials or proxy settings from the new diagnostic data. The full Blueprint contains only sanitized origin/path identities and at most four examples. It does not copy arbitrary Core objects.
- The existing response byte budget, row count/truncation indicators, `artifacts.blueprint`, `artifacts.plan`, and the `PLANNING_ONLY` / `NOT_VERIFIED` boundary are unchanged. A `projectionTruncated=true` item makes `decisionCompleteness.requiresArtifactRead=true` and adds `evidenceDetails` to `truncation.limitsHit`.

## Diagnostic codes and proof boundaries

- `FOREIGN_TARBALL_ORIGIN`, `BAD_REGISTRY_PATH`, `MISSING_DIST_TARBALL`: classifications of URL candidates **already rejected** by Core; this layer does not re-evaluate admission or turn them into installable targets.
- `HTTP_ARTIFACT_UNAVAILABLE`: only from physical GET observations actually made by Core. `unprobed=true` means **not** physically verified; `planning-only` status is never treated as a successful download.
- `NO_INSTALLABLE_ARTIFACT` means every **examined newer candidate** was structurally rejected. It is not a claim about every historical registry/version and is not equivalent to `UNKNOWN_SECURITY` or `CANDIDATE_CAP`.
- `metadataRequestBaseAuthority=CONFIGURATION_NOT_HTTP_TRACE` is retained so configured registry identity is never confused with the actual contacted service.

All typed diagnostic strings are whitelisted. Unknown reason codes and malformed counters/URL identities are not copied. Counters are nonnegative true integers (booleans excluded), examples and origins are capped at four, and optional truncation markers preserve uncertainty. The more comprehensive raw Core `plan.json` remains linked by the existing artifact pointer.

## Running safely before repinning Core

A test harness can provide `DEPLOOM_MCP_GENERATOR_PATH` pointing at the standalone Core PR generator. That is an **explicit diagnostic override** and must be recorded as such in evidence. It does not update `upstream/deploom`, change `release-manifest.json.core` or make a wheel contain the unmerged Core. To expose these fields in the normal MCP release, a **separate reviewed Core repin** is required after both PRs pass CI. No merge or release is included in this work.

## Checks

```shell
python -m unittest tests/test_registry_projection.py -v
python -m unittest discover -s tests -p "test_*.py" -v
python scripts/update_release_manifest.py --check
```

The final release manifest changes only `schemaSha256` to bind the new JSON Schema. `core.revision`, `core.contentSha256`, Skill hashes and MCP version stay exactly pinned to their previous values. The wheel/sdist checks validate this unchanged Core inventory with the optional new schema.
