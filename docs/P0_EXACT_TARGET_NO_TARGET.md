# P0 — Chalk exact-target NO_TARGET: root-cause and minimal product scope

**Date:** 2026-10-29. Review-only. Baseline: P3 [PR #11](https://github.com/DepLoom/mcp/pull/11) `d8f1297d177d4295ee2b566fcd79d0dc3807bcdc`, the unchanged 8-file frozen Linux Chalk project SHA256 `4adfa455d02e9f354e5b47e7b4d983f6676b620b13dc1454bda9248272d97cb6`, and pinned Core `49232ab510da8fa854f36d84a639f3db28ab363c`. No user project or paid agent.

## Root cause: do not confuse three different intents

Core `draft_decision_contract.normalize_decision_contract` v1 exposes only `minimum-change-to-policy` and `latest-compatible-in-approved-lines` objectives; `approvedVersionLines` accepts **major-number lists**. `targetMode=pinned` is an authority rule for an already *proposed assignment*, **not** a request to pin a user-specified patch version. No public v1 input encodes `chalk=5.3.0` as an exact, solvable goal.

The previous P3 calls sent `mutable_packages=["chalk"]`, `approved_version_lines={"chalk":[5]}`, target mode pinned or advisory/latest. That correctly keeps `is-number@7.0.0` immutable **as a constraint-bearing companion**, without using `exclude` to remove it from health accounting. Both calls succeeded over real packaged MCP stdio and Core, returning:

```json
{
  "ok": true,
  "status": {"authority": "PLANNING_ONLY", "draftStatus": "DRAFT_READY",
             "verificationStatus": "NOT_VERIFIED"},
  "chalk": {"current": "4.1.2", "status": "NO_TARGET", "target": null,
            "noTargetDiagnosticCode": "UNRESOLVED_TARGET_CONSTRAINT_OR_POLICY"},
  "is-number": {"status": "DEFERRED", "target": null}
}
```

Core's sanitized row reason says there is no policy-driven target. No structural registry-origin/path rejection was published in the compact diagnostics; this **does not** imply proven artifact safety or verified compatibility. Independent P2 oracle proves real public Chalk 5.3.0 *can install*, but does not prove it is solver-feasible under the original declaration and planning constraints.

Existing `evaluate_assignment(assignment={"chalk":"5.3.0"})` can **inspect** the caller's selected version within these bounds. Actual responses: `REPLAN_REQUIRED` in pinned mode, `REVIEW_REQUIRED` in advisory mode; the check `ALTERNATIVE_PEER_SECURITY_POLICY_NOT_RECHECKED` is present. Neither returns a newly solved target nor validated runtime/security evidence. A/B must not count that as successful planning.

## What changed in the product (generic; no Chalk hardcode)

MCP now exposes optional `required_packages=[...]` to forward the Core's *existing*, documented legacy `policies: {package: "required"}` through `DEPLOOM_BASELINE_INTENT_JSON`. This requires movement from current, not a specific version. It is validated before state writes, rejects non-list/malformed names, immutable name requirements and required/excluded contradictions; does not relax solver/security/registry or add tool execution authority. Default absent request stays backward compatible. Public contract documentation and targeted environment/negative tests accompany it.

### Real required-policy probe: important negative result

On the **same Chalk frozen input**, with the same major-5 bound and candidate cap 128, adding `required_packages=["chalk"]` did not produce a migration plan. The genuine Core subprocess returned `generator-failed`, exit code **3**, and a sanitized `unsat` marker. This is a **fail-closed UNSAT**, not success and not a transport error. It may reflect incompatible source declaration/solver constraints; this evidence alone does not identify a unique Z3 unsat core. The MCP bridge forwards the required intent and verifies its input, but **cannot honestly claim this Chalk fixture is solved**. The exact goal is still unsupported.

The final P0 gate must read **`NO_GO / EXACT_VERSION_OBJECTIVE_UNSUPPORTED_REQUIRED_UNSAT`**. No additional candidate-cap increase, no change of target to a convenient major/patch, and no agent benchmark.

## Why an automatic exact-version solver patch is *not* a safe P0 tweak

Adding `exactTargets={pkg:version}` to the Core V1 decisionContract would change its public contract, provenance hashes, Z3 domain/constraints, Core wheel gitlink and security/peer assessment semantics. Pushing a desired target into the output or treating `evaluate_assignment` as a solver would silently bypass unknown policy/compatibility/security evidence and violate planning-only authority. No generic safe mapping from a V1 major line to an arbitrary user exact version exists. A future **separately scoped** Core contract extension needs independently verified exact-target admission, peer/engine feasibility, publication/OSV semantics, required/immutable contradictions, bounded solver tests, and an MCP schema/release repin. Do not implement that expansion as a speculative patch to this Chalk experiment.

## Test evidence and stop

The focused new `tests/test_required_intent_bridge.py` covers Core env transport and invalid/immutable/excluded requirements; `tests/test_d1_pilot.py` prevents coincidental latest-major targets or exit-3 UNSAT from being interpreted as a valid exact objective. Existing P2 `breaking-verify` oracle and all frozen fixture content are unchanged.

Only one future A/B pair was authorized by the user, **conditional on meaningful actionable planning and an available verified model/runner**. Those prerequisites remain false. Therefore **no paid agent session is launched**, no tokens inferred, and no claim of MCP speedup. The pre-existing launch packet remains blocked by the product gate. Continue only with a separately reviewed design of an exact-version planning contract if its additional Core scope is authorized; don't create another benchmark harness.

No merge, release, package publication, private projects, credentials or raw session logs.
