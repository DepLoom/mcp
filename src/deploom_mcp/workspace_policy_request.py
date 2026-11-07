"""Normalize workspace policy and bind it to host-owned evidence sources."""
from __future__ import annotations

from contextlib import redirect_stdout
import os
import sys
import time

from deploom_mcp.goal_contract import GoalContractError


def validate_workspace_policy(value: dict) -> dict:
    defaults = {"minLagOkPct": 80, "lagPolicyMonths": 12, "maxKnownHigh": 1}
    optional = {"maxKnownModerate", "maxKnownLow"}
    if (not isinstance(value, dict) or value.get("targetLevel") not in ("yellow", "green")
            or not value.keys() <= {"targetLevel", "securityEvidenceMode", *defaults, *optional}
            or "securityEvidenceMode" in value and value["securityEvidenceMode"] != "REGISTRY_ONLY"):
        raise GoalContractError(
            "INVALID_WORKSPACE_HEALTH_POLICY",
            "Use policy.targetLevel 'yellow' or 'green'. Optional fields: minLagOkPct, "
            "lagPolicyMonths, maxKnownHigh, maxKnownModerate, maxKnownLow, securityEvidenceMode (REGISTRY_ONLY).",
        )
    options = {**defaults, **value}
    if (type(options["minLagOkPct"]) is not int or not 0 <= options["minLagOkPct"] <= 100
            or type(options["lagPolicyMonths"]) is not int or options["lagPolicyMonths"] not in {3, 6, 9, 12}
            or any(type(options[key]) is not int or not 0 <= options[key] <= 99
                   for key in ({"maxKnownHigh"} | optional) & options.keys())):
        raise GoalContractError("INVALID_WORKSPACE_HEALTH_POLICY")
    if options["targetLevel"] == "green":
        if "minLagOkPct" in value and value["minLagOkPct"] != 100:
            raise GoalContractError("INVALID_WORKSPACE_GREEN_LAG_TARGET")
        if "maxKnownHigh" in value and value["maxKnownHigh"] != 0:
            raise GoalContractError("INVALID_WORKSPACE_GREEN_SECURITY_TARGET")
        options.update(minLagOkPct=100, maxKnownHigh=0)
        if optional & options.keys():
            options = {"maxKnownModerate": 20, "maxKnownLow": 20, **options}
    return options


def workspace_health_policy(options):
    from workspace_health_policy import WorkspaceHealthPolicy

    level = options["targetLevel"]
    explicit_ml = "maxKnownModerate" in options or "maxKnownLow" in options
    return WorkspaceHealthPolicy(
        level, options["minLagOkPct"], max_high=options["maxKnownHigh"],
        max_moderate=options.get("maxKnownModerate"), max_low=options.get("maxKnownLow"),
        max_combined_moderate_low=20 if level == "green" and not explicit_ml else None,
    )


def bind_workspace_policy_solver(options, packages, declarations, approved, exact, admitted, environment, topology,
                                 *, effective_peer_ranges=None, metadata_deadline: float | None = None):
    from deploom_mcp.dependency_audit import AuditError, checked_registry
    from dependency_live_roadmap_generator import DeadlineClock, LiveDataClient
    from workspace_peer_model import plan_workspace_from_registry

    registry_only = options is not None and options.get("securityEvidenceMode") == "REGISTRY_ONLY"
    registry_value = os.environ.get("DEPLOOM_WORKSPACE_POLICY_REGISTRY")
    if not registry_value:
        if options is None or registry_only:
            raise GoalContractError("WORKSPACE_REQUEST_REGISTRY_NOT_AUTHORIZED")
        level = options["targetLevel"]

        def host_disabled(_excluded, _timeout):
            return {"status": "UNKNOWN", "assignment": None, "reason": "WORKSPACE_POLICY_HOST_DISABLED",
                    "topology": "PROPOSED_INSTANCE_LAYOUT" if topology else "OBSERVED_LOCK_LAYOUT",
                    "authority": "PLANNING_ONLY", "verification": "NOT_VERIFIED",
                    "policy": {"status": "UNKNOWN", "targetLevel": level,
                               "authority": "DECLARED_DOMAIN_PLANNING_ONLY", "finalAudit": "NOT_RUN"}}

        return host_disabled
    try:
        registry = checked_registry(registry_value)
        if not registry.startswith("https://"):
            raise AuditError("invalid-registry", "workspace policy requires an HTTPS registry")
    except AuditError as exc:
        raise GoalContractError("WORKSPACE_POLICY_HOST_REGISTRY_INVALID") from exc

    advisory_source = None
    if registry_only:
        from registry_advisory_evidence import registry_advisory_source_matches

        configured = os.environ.get("DEPLOOM_MCP_REGISTRY_ADVISORY_SOURCE", "")
        if not registry_advisory_source_matches(registry, configured):
            raise GoalContractError("WORKSPACE_REGISTRY_ADVISORY_NOT_AUTHORIZED")
        advisory_source = registry

    policy = workspace_health_policy(options) if options is not None else None
    external_disabled = os.environ.get("DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE") == "1"
    allowed = frozenset()
    if policy and not registry_only and not external_disabled:
        allowed = frozenset(name.strip() for name in
                            os.environ.get("DEPLOOM_WORKSPACE_POLICY_OSV_PACKAGES", "").split(",") if name.strip())
    client = LiveDataClient(
        registry, timeout=5, batch_size=16, sleep_sec=0,
        use_system_proxy=os.environ.get("DEPLOOM_WORKSPACE_POLICY_SYSTEM_PROXY") == "1",
        allow_registry_metadata_redirects=not (registry_only or external_disabled),
    )
    client.draft = True
    metadata_seconds = 60 if metadata_deadline is None else min(60, max(0, metadata_deadline - time.monotonic()))
    client.set_deadline(DeadlineClock(metadata_seconds))

    def solve(excluded, timeout_ms):
        # The isolated worker reserves stdout for its single JSON result.
        with redirect_stdout(sys.stderr):
            return plan_workspace_from_registry(
                packages, declarations, approved, exact, client,
                environment=environment, topology=topology, candidate_versions=admitted,
                health_policy=policy, health_lag_months=options["lagPolicyMonths"] if options is not None else 12,
                osv_allowed_names=allowed, registry_advisory_source=advisory_source,
                timeout_ms=timeout_ms, excluded_assignments=tuple(excluded), include_constraint_evidence=True,
                effective_peer_ranges=effective_peer_ranges,
            )

    return solve
