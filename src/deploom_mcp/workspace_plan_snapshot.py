"""Planning-only handoff from a bound workspace inventory to bundled Core."""
from __future__ import annotations

from hashlib import sha256
import json
import os
import re
import sys
import time
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
    if (not isinstance(request, dict) or not required <= request.keys() <= required | {"preflight"}
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

    preflight = request.get("preflight")
    if "preflight" in request:
        if not isinstance(preflight, dict) or not {"reauthor"} <= preflight.keys() <= {"reauthor", "immutableInstances", "candidateLimit"}:
            raise GoalContractError("INVALID_WORKSPACE_PREFLIGHT_REQUEST")
        limit = preflight.get("candidateLimit", 3)
        if type(limit) is not int or not 1 <= limit <= 3:
            raise GoalContractError("INVALID_WORKSPACE_PREFLIGHT_REQUEST")
        pins = preflight.get("immutableInstances", [])
        if (not isinstance(pins, list) or len(pins) > 128
                or any(not isinstance(path, str) or not path or len(path) > 512 for path in pins)
                or len(set(pins)) != len(pins)):
            raise GoalContractError("INVALID_WORKSPACE_PREFLIGHT_REQUEST")

    def plan(digest, rows, packages, inputs):
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
            def solve(excluded, timeout_ms):
                return plan_workspace_upgrade(
                    packages, declarations, admitted, exact, request["packuments"],
                    environment=request["environment"], topology=topology, timeout_ms=timeout_ms,
                    excluded_assignments=tuple(excluded))

            if preflight is None:
                result = solve((), 5000)
            else:
                def verify(candidate, timeout_seconds):
                    return _preflight(project, preflight, candidate, admitted, inputs, timeout_seconds)

                result = _try_workspace_candidates(solve, verify, limit, request)
        finally:
            sys.path.pop(0)
        return {**result, "inventoryDigest": digest, "candidateEvidence": "CALLER_SNAPSHOT",
                "executionReady": False}

    return observe_npm_workspace(project, plan_snapshot=plan)


def _try_workspace_candidates(solve, verify, limit, request):
    deadline = time.monotonic() + 60
    vectors, attempts = [], []
    result = None
    stop = "CANDIDATE_LIMIT"
    for _ in range(limit):
        remaining = deadline - time.monotonic()
        if remaining < 1:
            stop = "TIME_BUDGET"
            break
        plan = solve(vectors, min(5000, int(remaining * 1000)))
        if vectors and plan["status"] != "SAT_IN_MODELED_DOMAIN":
            stop = ("NO_DISTINCT_MODELED_CANDIDATE" if plan["status"] == "UNSAT_IN_MODELED_DOMAIN"
                    else "ALTERNATIVE_MODEL_UNKNOWN")
            break
        remaining = deadline - time.monotonic()
        if remaining < 1:
            if result is None:
                result = {**plan, "packageManagerValidation": {"status": "NOT_RUN", "code": "TIME_BUDGET"}}
            stop = "TIME_BUDGET"
            break
        assignment = plan["assignment"]
        if assignment is not None and assignment in vectors:
            stop = "DUPLICATE_CANDIDATE"
            break
        native = verify(plan, int(remaining))
        result = {**plan, "packageManagerValidation": native}
        if assignment is not None:
            vectors.append(assignment)
            attempts.append({"assignment": assignment, "validation": native})
        if (native["status"] != "RESOLUTION_FAILED"
                or native["code"] not in {"ERESOLVE", "EBADENGINE", "LOCK_PEER_MISMATCH"}):
            stop = native["status"]
            break
        if not any(len(row["versions"]) > 1 and row["exactTarget"] is None for row in request["mutable"]):
            stop = "NO_DISTINCT_MODELED_CANDIDATE"
            break
    if result is None:
        raise GoalContractError("WORKSPACE_PLAN_TIMEOUT")
    snapshot = json.dumps(request, sort_keys=True, ensure_ascii=True).encode()
    return {**result, "candidateSearch": {"limit": limit, "timeoutSeconds": 60,
            "requestSha256": sha256(snapshot).hexdigest(), "stop": stop, "attempts": attempts}}


def _preflight(project, request, plan, admitted, inputs, timeout_seconds):
    if plan["status"] != "SAT_IN_MODELED_DOMAIN":
        return {"status": "NOT_RUN", "code": "WORKSPACE_PLAN_UNAVAILABLE"}
    configured = [os.environ.get(key) for key in ("DEPLOOM_WORKSPACE_NPM_ARTIFACTS",
                  "DEPLOOM_WORKSPACE_NPM_CACHE", "DEPLOOM_WORKSPACE_NPM_REGISTRY")]
    if not all(configured):
        return {"status": "NOT_RUN", "code": "WORKSPACE_PREFLIGHT_HOST_DISABLED"}
    artifacts, cache = (Path(value) for value in configured[:2])
    project = project.resolve()
    if (not artifacts.is_absolute() or not cache.is_absolute() or not cache.is_dir()
            or any(path.resolve().is_relative_to(project) or project.is_relative_to(path.resolve())
                   for path in (artifacts, cache))):
        raise GoalContractError("WORKSPACE_PREFLIGHT_HOST_PATH_INVALID")
    for path in inputs:
        if not path.endswith("package.json"):
            continue
        try:
            (project / Path(path).parent / ".npmrc").lstat()
        except FileNotFoundError:
            continue
        return {"status": "NOT_RUN", "code": "WORKSPACE_PREFLIGHT_PROJECT_NPMRC_UNSUPPORTED"}
    from deploom_mcp.dependency_audit import AuditError, checked_registry
    from workspace_npm_preflight import run_workspace_npm_preflight

    try:
        registry = checked_registry(configured[2])
    except AuditError as exc:
        raise GoalContractError("WORKSPACE_PREFLIGHT_HOST_REGISTRY_INVALID") from exc

    assignment = {_key(json.loads(key)): version for key, version in plan["assignment"].items()}
    reauthor = _key(request["reauthor"]) if request["reauthor"] is not None else None
    if reauthor is not None and (reauthor not in assignment or reauthor in admitted):
        raise GoalContractError("INVALID_WORKSPACE_PREFLIGHT_REAUTHOR")
    manifests = {str(Path(path).parent.as_posix()): raw for path, raw in inputs.items()
                 if path.endswith("package.json")}
    result = run_workspace_npm_preflight(
        manifests, inputs["package-lock.json"], assignment, frozenset(admitted),
        artifacts=artifacts.resolve(), cache=cache.resolve(), registry=registry,
        reauthor=reauthor, timeout_seconds=timeout_seconds,
        immutable_instances=frozenset(request.get("immutableInstances", ())))
    # Only the local host can resolve the artifact identifier to its owned directory.
    scratch = result.pop("workspace", None)
    if result["status"] == "RESOLUTION_PASSED" and scratch is not None:
        result["resolverArtifactId"] = Path(scratch).name
    return result
