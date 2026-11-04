"""Planning-only handoff from a bound workspace inventory to bundled Core."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from deploom_mcp.distribution import resolve_core_dir
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.workspace_inventory import observe_npm_workspace


def _key(value):
    if not isinstance(value, list) or len(value) != 3 or not all(isinstance(v, str) for v in value):
        raise GoalContractError("INVALID_WORKSPACE_PLAN_DECLARATION")
    return tuple(value)


def plan_workspace_snapshot(project: Path, request: dict) -> dict:
    """Bind a bounded caller-provided metadata snapshot to current project bytes."""
    required = {"inventoryDigest", "mutable", "packuments", "environment", "topology"}
    if (not isinstance(request, dict) or set(request) != required
            or not isinstance(request["inventoryDigest"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", request["inventoryDigest"])
            or not isinstance(request["mutable"], list)
            or not 1 <= len(request["mutable"]) <= 64
            or not isinstance(request["packuments"], dict)
            or not isinstance(request["environment"], dict)
            or set(request["environment"]) - {"node", "npm"}
            or any(not isinstance(value, str) for value in request["environment"].values())
            or len(json.dumps(request, ensure_ascii=True, allow_nan=False)) > 12000):
        raise GoalContractError("INVALID_WORKSPACE_PLAN_REQUEST")

    def plan(digest, rows, packages):
        if digest != request["inventoryDigest"]:
            raise GoalContractError("STALE_WORKSPACE_INVENTORY")
        declarations = {(row["owner"], row["section"], row["name"]): row["declaredSpec"]
                        for row in rows if row["section"] != "peerDependencies"}
        admitted, exact = {}, {}
        for entry in request["mutable"]:
            if not isinstance(entry, dict) or set(entry) != {"declaration", "versions", "exactTarget"}:
                raise GoalContractError("INVALID_WORKSPACE_PLAN_SCOPE")
            key = _key(entry["declaration"])
            versions = entry["versions"]
            target = entry["exactTarget"]
            if (key not in declarations or key in admitted or not isinstance(versions, list)
                    or not 1 <= len(versions) <= 32 or any(not isinstance(v, str) or
                        not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", v) for v in versions)
                    or len(set(versions)) != len(versions) or (target is not None and target not in versions)):
                raise GoalContractError("INVALID_WORKSPACE_PLAN_SCOPE")
            admitted[key] = tuple(versions)
            if target is not None:
                exact[key] = target

        raw_topology = request["topology"]
        topology = None
        core_dir = resolve_core_dir()
        if not (core_dir / "workspace_peer_model.py").is_file():
            raise GoalContractError("WORKSPACE_PLANNING_CORE_UNAVAILABLE")
        sys.path.insert(0, str(core_dir))
        try:
            from workspace_peer_model import WorkspaceTopology, plan_workspace_upgrade

            if raw_topology is not None:
                if (not isinstance(raw_topology, dict) or set(raw_topology) != {"instanceGroups", "providers"}
                        or not isinstance(raw_topology["instanceGroups"], list)
                        or not isinstance(raw_topology["providers"], list)):
                    raise GoalContractError("INVALID_WORKSPACE_PLAN_TOPOLOGY")
                groups = []
                for group in raw_topology["instanceGroups"]:
                    if not isinstance(group, list):
                        raise GoalContractError("INVALID_WORKSPACE_PLAN_TOPOLOGY")
                    groups.append(tuple(map(_key, group)))
                providers = {}
                for row in raw_topology["providers"]:
                    if not isinstance(row, dict) or set(row) != {"consumer", "peer", "provider"}:
                        raise GoalContractError("INVALID_WORKSPACE_PLAN_TOPOLOGY")
                    consumer, peer = _key(row["consumer"]), row["peer"]
                    provider = _key(row["provider"]) if row["provider"] is not None else None
                    if (consumer not in declarations or not isinstance(peer, str)
                            or (provider is not None and provider not in declarations)
                            or (consumer, peer) in providers):
                        raise GoalContractError("INVALID_WORKSPACE_PLAN_TOPOLOGY")
                    providers[consumer, peer] = provider
                topology = WorkspaceTopology(providers, tuple(groups))
            result = plan_workspace_upgrade(
                packages, declarations, admitted, exact, request["packuments"],
                environment=request["environment"], topology=topology, timeout_ms=5000)
        finally:
            sys.path.pop(0)
        return {**result, "inventoryDigest": digest, "candidateEvidence": "CALLER_SNAPSHOT",
                "executionReady": False}

    return observe_npm_workspace(project, plan_snapshot=plan)
