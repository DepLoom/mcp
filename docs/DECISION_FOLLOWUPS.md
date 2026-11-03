# Decision follow-ups: evaluate_assignment and explain_conflict

**Status:** evidence-bound MCP tools. They are read-only, do not install
dependencies, and do **not** serialize/reconstruct Core's internal Z3 IR.
The first release implements user-authority and published-evidence checks,
not an independent alternative-version peer solver.

These tools are registered in the default stdio server:
\`python -m deploom_mcp\`. The audit-enabled entrypoint
\`python -m deploom_mcp.audit_server\` exposes them too.

## Recommended agent workflow

1. Call \`get_upgrade_plan\` for the actual manifest with correct goal,
   objective, target_mode, mutable_packages and approved_version_lines.
2. Take the absolute \`artifacts.blueprint\` path and
   \`identities.decisionDigest\` from the returned compact protocol.
3. Ask \`explain_conflict\` about a relevant package, or
   \`evaluate_assignment\` about a possible exact-version alternative.
4. Treat unknown peer/security/policy evidence as unresolved. For
   pinned mode, changing an assigned version requires a new plan/authority
   before executing. Run actual install, audit and behavioral checks against
   final source/manifest/lock bytes separately.

### evaluate_assignment

\`\`\`json
{
  "blueprint_path": "/absolute/path/to/upgrade-blueprint.json",
  "assignment": {"react-router-dom": "6.30.6"},
  "expected_decision_digest": "optional-64-character-lowercase-sha256"
}
\`\`\`

Partial assignments override the **published selected versions**, not the
current lockfile. Unmentioned rows retain their selected target or current
version. The tool also accepts \`declarationId\` keys when one package appears
in multiple declarations and package-only keys are ambiguous. Inputs must be
exact semver versions. It never writes package.json or lockfiles.

Checks include:
- immutable, excluded or otherwise out-of-scope changes;
- approved major lines (including the current-major default for the
  latest-compatible-in-approved-lines objective);
- pinned replan requirement versus advisory alternative;
- known blockers of the *unchanged published assignment*, including
  final-target policy violations even when the exact version is PROPOSED;
- input-file drift based on Core's recorded byte hashes;
- partial planning and workspace reconciliation requirements.

For an unchanged assignment, \`PUBLISHED_PLAN_BLOCKER\` carries
\`evidenceCode\`, \`evidenceRef\`, lifecycle and project/scope provenance.
An \`ACTIVE_FINAL\` \`TARGET_POLICY_BLOCKED\` is a real published policy
failure, not the old source spec made immutable. \`PROPOSED\` signifies a
planner target, not health-policy success or a verified migration.

Results:
- \`MATCHES_PUBLISHED_PLAN\`: no divergence detected from the published
  assignment; this is **not** proven compatibility.
- \`BLOCKED_BY_KNOWN_CONSTRAINTS\`: a known user scope/major restriction or
  a documented blocker is present.
- \`REPLAN_REQUIRED\`: a pinned target changed.
- \`REVIEW_REQUIRED\`: advisory alternatives or incomplete evidence.
- \`STALE_BLUEPRINT\`: recorded local input bytes changed; regenerate.

Every successful response has \`compatibility="UNKNOWN"\`,
\`verificationStatus="NOT_VERIFIED"\`,
\`peerConstraints="NOT_RESOLVED_FOR_ALTERNATIVE"\` and
\`policyAttainment="NOT_RECOMPUTED"\`. Unlike a fresh run of Core, the tool
does not claim to validate new peer metadata, transitive dependencies,
engine constraints, target security or policy attainment for alternatives.
An apparent matches-plan result is only consistent with the **stored** plan.

### explain_conflict

\`\`\`json
{
  "blueprint_path": "/absolute/path/to/upgrade-blueprint.json",
  "package": "react",
  "max_items": 20,
  "expected_decision_digest": "optional-64-character-lowercase-sha256"
}
\`\`\`

\`package\` is optional. The response extracts **typed published evidence**:
\`code\`, \`severity\`, \`scope\`, \`project\`, \`lifecycle\`,
\`evidenceRef\`, \`message\`, and \`nextAction\`. It includes relevant
solver component membership and the currently published candidate versions.
An aggregate/project-level policy blocker is not misattributed to a package.
A legacy peer note is not automatically a verified ACTIVE_FINAL blocker.
\`NO_DOCUMENTED_BLOCKER_OR_REVIEW\` means only that the published evidence
doesn't show one, not that a new assignment is installable.

All display lists have explicit totals/truncation, with the complete
Blueprint remaining at its original artifact path. The tool does not send
project files to a network endpoint, require an agent Skill or execute
a planner subprocess.

## Freshness and authority

The reader validates the full Blueprint against the shipped JSON Schema and
recomputes its \`decisionDigest\`, \`evidenceDigest\` and \`policyDigest\`.
It checks recorded local file byte hashes where available. A byte match is
reported as \`MATCHES_RECORDED_FILES\` rather than a proof of full semantic
input equality or a frozen registry/OSV snapshot; virtual/effective settings
are not replayed. If files changed, \`STALE_BLUEPRINT\` takes precedence.

This is deliberately a lightweight, conservative first step. To prove a
candidate peer-compatible, Core must expose a fresh evaluation of the same
constraint IR with the proposed assignment held fixed, including source and
workspace evidence. Returning \`PASS\` based only on the old Blueprint would
be incorrect. That fuller Core evaluation is a separate follow-up.

## Ownership and regression coverage

MCP owns artifact validation, publication digest verification, decision
projection and tools. Core continues to own Z3 modeling and target search.
Regressions exercise unchanged, pinned, advisory, excluded, stale, target
major, ambiguous declaration and typed peer blocker cases. Full CI on
Windows/Linux and installed-wheel stdio smoke remain required before merge.

The former A/B's exact commit must remain frozen. These tools do not alter
the pinned Core, release manifest, audit tool implementation or Skill.
