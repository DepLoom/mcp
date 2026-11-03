# Optional official migration references

`get_goal_plan` accepts an optional `migration_evidence_request`. It appends an
`officialMigrationEvidence` section to the existing proposal. Omit the parameter
for the original planning route: no knowledge retrieval, cache reads or parser
loads are performed. Exact planning still does not invoke Z3. The tool advertises
an open-world hint because an explicitly requested reference section may access
public official sources.

```json
{
  "schema": "official_migration_evidence.v1",
  "mode": "ON_DEMAND",
  "transitions": [{"package": "strip-json-comments", "from": "3.1.1", "to": "5.0.0"}],
  "detail": "summary",
  "localApplicability": "OFF"
}
```

The transition must match the observed root version and the proposal's selected
target. This does not authorize that transition, source repair or execution.
The first explicit providers cover `strip-json-comments` 3.1.1 to 5.0.0 and
Vite 6.4.5 to 7.0.0. The latter includes plugin-react 4.5.2 as a **reference**, not
an automatically selected companion. Other exact transitions return UNSUPPORTED;
rename/alias mappings are not guessed. No-transition performs no HTTP requests.
Prerelease targets require a separate policy and are not admitted here.

## Authority and conflicts

References are REFERENCE_ONLY; local syntax observations are PLANNING_ONLY.
`verificationAuthority` and local `executionAuthority` remain NONE. No section
can change the original goal, policy, binding, assignment, check profile,
executionReady or checkpoint. A source document, hash or cache entry is not an
execution receipt. The keyword helper is reused only as LABELLED_KEYWORD_HINTS;
its legacy labels do not become established breaking behavior or solver rules.

Package metadata, source literals and documentation claims are distinguished.
For example, strip-json-comments 5.0.0 has conflicting trailingCommas defaults
in its types and source/README. Both claims and their exact source references
are retained; this mechanism does not execute the source to resolve the conflict.
A guide's applicability or a compatible peer range does not establish complete
npm placement, a successful install or working application behavior.

## Retrieval and cache

Only catalog-verified public GitHub repository/tag/artifact mappings are used.
Tags are resolved to immutable commits, annotated tags are bounded, and each
UTF-8 body receives SHA-256. HTTPS connects to a validated public DNS address
with certificate/hostname validation. Arbitrary URLs, credentials, cookies,
private addresses and redirects are rejected; no environment proxy credentials
are forwarded. This direct public HTTPS transport does not modify proxy settings.
Unavailable transport remains UNKNOWN, without automatic retries or fallback to
main/master. Project paths, source, lockfiles and local observations are never
included in outbound requests.

Limits shared by the whole evidence request: 12 HTTP transactions, 128 KiB per
body, 1 MiB total body bytes, 30 seconds and the remaining overall tool deadline.
The implementation is sequential (within the maximum concurrency of two).
Unknown stream lengths mark byte accounting incomplete and stop further fetches.
Metrics count HTTP body bytes actually read, not headers, TCP overhead or tokens.

The host-owned `migration-evidence` cache under DEPLOOM_MCP_STATE_DIR uses atomic
records and content hashes. A pin is revalidated after 24 hours. Drift, corrupt
records/bodies and backward/future cache clocks fail closed. The local operator
controls the cache; hashes are not signature attestation against a hostile writer.
The operator state root and inspected project must be disjoint; overlapping
paths are rejected before cache access or temporary input writes. Source
retrievedAt records the observed version-pin time used for TTL, not a network
transfer timestamp for every immutable artifact.
`DEPLOOM_MCP_MIGRATION_OFFLINE=1` permits only fresh, verified cached inputs.
Stale/missing offline inputs remain UNKNOWN; no silent stale data is served.
`detail: full` is a bounded source view with explicit textTruncated flags;
canonical commit URLs and full content hashes remain available.

## Local syntax inspection

`localApplicability: ON` optionally uses an already installed **host** TypeScript
6.0.3 library. Configure DEPLOOM_MCP_TYPESCRIPT_PATH to its absolute typescript.js
path and DEPLOOM_MCP_TYPESCRIPT_SHA256 to the exact file hash. The library must be
outside the inspected project. There is no automatic installation or loading of
project-owned parsers, imports, plugins or configuration. A missing/mismatched
host parser gives UNKNOWN rather than an absence claim.

The pinned helper parses JS/TS text; it never executes it. Results expose literal
imports/require syntax and limited Vite/Sass option syntax. require shadowing,
runtime module resolution and semantic API compatibility are not established.
Dynamic loads, aliases, callback/spread/computed configuration, parse failures,
unsupported Vue/Svelte/Astro files and partial coverage stay UNKNOWN. An absence
is only NOT_OBSERVED_SCOPED for the explicitly inspected JS/TS language scope,
not UNUSED or project-wide safety. semanticApplicability remains UNKNOWN.

Coverage includes eligible/scanned/unreadable/unsupported counts and limits
(256 files, 128 KiB per file, 1 MiB total source text). Findings are bound to the
observed source identity and file digest inventory. Changes during inspection
mark them STALE. Source text is passed only to the local pinned parser in an
owned temporary input file, removed on normal completion; no text is sent to
reference providers. Caller verification remains mandatory.

## Validation boundaries

No-agent tests separately exercise retrieval/pins/cache, syntax observation and
normal MCP attachment. A strong comparator may use the same official docs,
hashes, cache, TypeScript checking and official codemods. Known fixtures are not
an independent blind holdout. Warm-cache measurements do not prove an advantage
over another equally cached client, and no agent token, repair success or ROI
claim follows from a smaller response. Skill behavior requires its own loaded
client validation. Existing closed project trials are not repeated by this feature.
