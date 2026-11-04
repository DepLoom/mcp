"""Versioned main API projection for targeted npm workspace plans."""
from __future__ import annotations

import re
from typing import Any

from deploom_mcp.blueprint import _validate_blueprint_schema
from deploom_mcp.distribution import blueprint_schema_path
from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.workspace_policy_request import validate_workspace_policy
from deploom_mcp.workspace_plan_details import prepare_workspace_details, retain_workspace_details
from deploom_mcp.workspace_tools import plan_workspace_snapshot_v1

_KIND = "npm-workspace-upgrade-blueprint"
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_REQUEST_FIELDS = {"schemaVersion", "goalKind", "inventoryDigest", "targets", "environment", "topology"}


def _snapshot_request(request: dict) -> dict:
    if (not isinstance(request, dict) or type(request.get("schemaVersion")) is not int
            or request["schemaVersion"] != 2):
        raise GoalContractError("INVALID_WORKSPACE_REQUEST_VERSION")
    goal = request.get("goalKind")
    if goal not in ("TARGETED_UPGRADE", "MONOREPO_TO_POLICY"):
        raise GoalContractError("UNSUPPORTED_WORKSPACE_GOAL")
    health_goal = goal == "MONOREPO_TO_POLICY"
    required = _REQUEST_FIELDS | ({"policy"} if health_goal else {"packuments"})
    if not required <= request.keys() <= required | {"preflight", "registry"}:
        raise GoalContractError("INVALID_WORKSPACE_UPGRADE_REQUEST")
    policy = validate_workspace_policy(request["policy"]) if health_goal else None
    targets = request["targets"]
    if not isinstance(targets, list) or not (0 if health_goal else 1) <= len(targets) <= 64:
        raise GoalContractError("INVALID_WORKSPACE_UPGRADE_TARGETS")
    seen, mutable = set(), []
    for row in targets:
        if not isinstance(row, dict) or row.keys() != {"selector", "approvedLines", "versions", "exactTarget"}:
            raise GoalContractError("INVALID_WORKSPACE_UPGRADE_TARGETS")
        selector = row["selector"]
        if (not isinstance(selector, dict) or selector.keys() != {"owner", "section", "name"}
                or any(not isinstance(value, str) or not 1 <= len(value) <= 512 for value in selector.values())
                or selector["section"] not in {"dependencies", "devDependencies", "optionalDependencies"}):
            raise GoalContractError("INVALID_WORKSPACE_SELECTOR")
        key = tuple(selector[field] for field in ("owner", "section", "name"))
        if key in seen:
            raise GoalContractError("DUPLICATE_WORKSPACE_SELECTOR")
        seen.add(key)
        lines, versions, exact = row["approvedLines"], row["versions"], row["exactTarget"]
        if (not isinstance(lines, list) or not 1 <= len(lines) <= 32
                or any(type(line) is not int or not 0 <= line <= 999999 for line in lines)
                or len(set(lines)) != len(lines)
                or not isinstance(versions, list) or not 1 <= len(versions) <= 32
                or any(not isinstance(value, str) or len(value) > 64 or not _VERSION.fullmatch(value)
                       or int(value.split(".", 1)[0]) not in lines for value in versions)
                or len(set(versions)) != len(versions)
                or (exact is not None and (not isinstance(exact, str) or exact not in versions))):
            raise GoalContractError("INVALID_WORKSPACE_VERSION_AUTHORITY")
        item = {"declaration": list(key), "versions": versions, "exactTarget": exact}
        if health_goal:
            item["approvedLines"] = lines
        mutable.append(item)
    snapshot = {key: value for key, value in request.items() if key not in {"schemaVersion", "goalKind", "targets", "policy"}}
    if policy is not None:
        snapshot["policy"] = policy
    return {**snapshot, "mutable": mutable}


def _compact_workspace_plan(plan: dict, request: dict) -> dict:
    evidence = plan["workspaceEvidence"]
    validation = plan.get("packageManagerValidation", {"status": "NOT_RUN", "code": "WORKSPACE_PREFLIGHT_NOT_REQUESTED"})
    candidate = plan["assignment"]
    request_id = canonical_digest(request)
    policy = plan.get("policy", {"status": "NOT_ASSESSED", "targetScope": "NONE_FOR_TARGETED_UPGRADE"})
    identity = {"requestId": request_id}
    if policy.get("evidence"):
        identity["healthEvidenceSha256"] = policy["evidence"]["snapshotSha256"]
    compact = {
        "ok": True, "schemaVersion": 2, "kind": _KIND, "goalKind": request["goalKind"],
        "inventoryDigest": plan["inventoryDigest"], "requestId": request_id,
        "candidateId": canonical_digest({**identity, "assignment": candidate}) if candidate else None,
        "authority": "PLANNING_ONLY", "verification": "NOT_VERIFIED", "executionReady": False,
        "coverage": {
            "discoveredOwnerCount": len(evidence["owners"]),
            "discoveredDeclarationCount": evidence["declarationCount"],
            "constraintDeclarationCount": evidence["constraintDeclarationCount"],
            "selectedDeclarationCount": len(evidence["targets"]),
            "heldDeclarationCount": evidence["constraintDeclarationCount"] - len(evidence["targets"]),
            "evaluatedOwnerCoverage": "UNKNOWN" if plan["status"] == "UNKNOWN" else "MODELED_DOMAIN_ONLY",
        },
        "intent": {
            "targets": request["targets"],
            "unselectedDirectDependencies": "HOLD_DECLARATION_AND_LOCK_VERSION",
            "immutableInstances": request.get("preflight", {}).get("immutableInstances", []),
        },
        "constraintModel": {
            "status": plan["status"], "reason": plan.get("reason"),
            "topology": plan["topology"], "domainEvidence": plan["candidateEvidence"],
            "transitivePlacement": "NOT_MODELED",
        },
        "jointAssignment": {
            "status": "COMPUTED" if candidate is not None else plan["status"], "targets": evidence["targets"],
        },
        "packageManagerValidation": {
            field: validation[field] for field in (
                "status", "code", "authority", "npmExitCode", "baselineLockSha256",
                "candidateLockSha256", "resolverIdentitySha256", "resolverArtifactId", "learningAllowed",
            ) if field in validation
        },
        "installation": {"status": "NOT_RUN"}, "checks": {"status": "NOT_RUN"},
        "runtime": {"status": "NOT_RUN"}, "acceptance": {"status": "NOT_RUN"},
        "policy": policy,
    }
    if request["goalKind"] == "MONOREPO_TO_POLICY":
        compact["intent"]["workspacePolicy"] = validate_workspace_policy(request["policy"])
    search = plan.get("candidateSearch")
    if search is not None:
        compact["packageManagerValidation"]["search"] = {
            "limit": search["limit"], "timeoutSeconds": search["timeoutSeconds"],
            "attemptCount": len(search["attempts"]), "stop": search["stop"],
            "attempts": [
                {"candidateId": canonical_digest({**identity, "assignment": entry["assignment"]}),
                 "status": entry["validation"]["status"], "code": entry["validation"]["code"]}
                for entry in search["attempts"]
            ],
        }
    if validation["status"] == "RESOLUTION_PASSED":
        compact["action"] = {"kind": "REVIEW_FOR_AUTHORIZED_ISOLATED_TRIAL", "code": validation["code"]}
    elif candidate is not None and validation["code"] == "WORKSPACE_PREFLIGHT_NOT_REQUESTED":
        compact["action"] = {"kind": "REVIEW_FOR_AUTHORIZED_PREFLIGHT", "code": validation["code"]}
    else:
        compact["action"] = {"kind": "INSPECT_BLOCKER", "code": validation["code"] if candidate is not None else plan["status"]}
    return compact


async def get_workspace_upgrade_plan(project_path: str, request: dict) -> dict[str, Any]:
    """Run the isolated owning workspace worker and project a targeted plan."""
    try:
        snapshot = _snapshot_request(request)
    except GoalContractError as exc:
        return {"ok": False, "error": {"code": exc.code}}
    result = await plan_workspace_snapshot_v1(project_path, snapshot)
    if not result["ok"]:
        return result
    try:
        plan = result["plan"]
        compact = _compact_workspace_plan(plan, request)
        compact["details"], detail_bytes = prepare_workspace_details(compact, plan)
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "error": {"code": "WORKSPACE_WORKER_INVALID_RESULT"}}
    if _validate_blueprint_schema(compact, blueprint_schema_path(workspace=True)) is not None:
        return {"ok": False, "error": {"code": "WORKSPACE_BLUEPRINT_SCHEMA_INVALID"}}
    retain_workspace_details(compact["details"]["planId"], detail_bytes)
    return compact
