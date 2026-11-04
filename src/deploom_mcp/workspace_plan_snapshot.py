"""Planning-only handoff from a bound workspace inventory to bundled Core."""
from __future__ import annotations

from hashlib import sha256
import json
import os
import re
import sys
import time
from pathlib import Path

from deploom_mcp.dependency_audit import AuditError, checked_registry
from deploom_mcp.distribution import resolve_core_dir
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.workspace_inventory import observe_npm_workspace


def _key(value):
    if not isinstance(value, list) or len(value) != 3 or not all(isinstance(v, str) for v in value):
        raise GoalContractError("INVALID_WORKSPACE_PLAN_DECLARATION")
    return tuple(value)


def _project_registry(inputs):
    """Interpret only settings whose behavior the isolated npm command preserves."""
    if any(path.endswith("/.npmrc") for path in inputs):
        raise GoalContractError("WORKSPACE_PREFLIGHT_MEMBER_NPMRC_UNSUPPORTED")
    options = {}
    try:
        for raw in inputs.get(".npmrc", b"").decode("utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            key, sep, value = (part.strip() for part in line.partition("="))
            if not sep or key not in {"registry", "save-exact"} or key in options:
                raise ValueError
            options[key] = value
        if "save-exact" in options and options["save-exact"] not in {"true", "false"}:
            raise ValueError
        registry = checked_registry(options.get("registry"))
        if registry and (not registry.startswith("https://") or any(char in registry for char in ";$`{}")):
            raise ValueError
        return registry
    except (ValueError, AuditError):
        raise GoalContractError("WORKSPACE_PREFLIGHT_PROJECT_NPMRC_UNSUPPORTED") from None


def _workspace_registry(configured, inputs):
    registry = checked_registry(configured)
    project_registry = _project_registry(inputs)
    if registry and project_registry and registry != project_registry:
        raise GoalContractError("WORKSPACE_PREFLIGHT_PROJECT_REGISTRY_MISMATCH")
    return project_registry or registry


def plan_workspace_snapshot(project: Path, request: dict) -> dict:
    """Bind a bounded caller-provided metadata snapshot to current project bytes."""
    health_goal = isinstance(request, dict) and "policy" in request
    required = {"inventoryDigest", "mutable", "environment", "topology", "policy" if health_goal else "packuments"}
    if (not isinstance(request, dict) or not required <= request.keys() <= required | {"preflight"}
            or not isinstance(request["inventoryDigest"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", request["inventoryDigest"])
            or not isinstance(request["mutable"], list)
            or not (0 if health_goal else 1) <= len(request["mutable"]) <= 64
            or (not health_goal and not isinstance(request["packuments"], dict))
            or not isinstance(request["environment"], dict)
            or set(request["environment"]) - {"node", "npm"}
            or any(not isinstance(value, str) for value in request["environment"].values())
            or len(json.dumps(request, ensure_ascii=True, allow_nan=False)) > 12000):
        raise GoalContractError("INVALID_WORKSPACE_PLAN_REQUEST")

    if health_goal:
        from deploom_mcp.workspace_policy_request import validate_workspace_policy

        health_options = validate_workspace_policy(request["policy"])
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
        admitted, exact, approved = {}, {}, {}
        target_keys = {"declaration", "versions", "exactTarget"} | ({"approvedLines"} if health_goal else set())
        for entry in request["mutable"]:
            if not isinstance(entry, dict) or set(entry) != target_keys:
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
            if health_goal:
                lines = entry["approvedLines"]
                if (not isinstance(lines, list) or not 1 <= len(lines) <= 32
                        or any(type(line) is not int or not 0 <= line <= 999999 for line in lines)
                        or len(set(lines)) != len(lines)
                        or any(len(v) > 64 or int(v.split(".", 1)[0]) not in lines for v in versions)):
                    raise GoalContractError("INVALID_WORKSPACE_VERSION_AUTHORITY")
                approved[key] = tuple(lines)
            if target is not None:
                exact[key] = target

        raw_topology = request["topology"]
        topology = None
        core_dir = resolve_core_dir()
        if not (core_dir / "workspace_peer_model.py").is_file():
            raise GoalContractError("WORKSPACE_PLANNING_CORE_UNAVAILABLE")
        sys.path.insert(0, str(core_dir))
        try:
            from resolution_inventory import npm_lock_resolved_instance
            from workspace_peer_model import WorkspaceTopology, declaration_id, plan_workspace_upgrade

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
            if health_goal:
                from deploom_mcp.workspace_policy_request import bind_workspace_policy_solver

                # Policy collection is online. The project can select the registry
                # only when it agrees with the host's explicit online permission.
                policy_registry = os.environ.get("DEPLOOM_WORKSPACE_POLICY_REGISTRY")
                if policy_registry:
                    try:
                        _workspace_registry(policy_registry, inputs)
                    except AuditError:
                        raise GoalContractError("WORKSPACE_POLICY_HOST_REGISTRY_INVALID") from None
                solve = bind_workspace_policy_solver(health_options, packages, declarations, approved, exact,
                                                     admitted, request["environment"], topology)
            else:
                def solve(excluded, timeout_ms):
                    return plan_workspace_upgrade(
                        packages, declarations, admitted, exact, request["packuments"],
                        environment=request["environment"], topology=topology, timeout_ms=timeout_ms,
                        excluded_assignments=tuple(excluded), include_constraint_evidence=True)

            if preflight is None:
                result = solve((), 5000)
            else:
                def verify(candidate, timeout_seconds):
                    return _preflight(project, preflight, candidate, admitted, inputs, timeout_seconds)

                result = _try_workspace_candidates(solve, verify, limit, request)

            # Summarize the same bound read used by the solver, including owners
            # with no declarations. Baseline resolution remains lockfile evidence.
            owners = sorted({Path(path).parent.as_posix() for path in inputs if path.endswith("package.json")})
            result["workspaceEvidence"] = {
                "owners": owners, "declarationCount": len(rows),
                "constraintDeclarationCount": len(declarations),
                "targets": [
                    {"declaration": list(key), "declaredSpec": declarations[key],
                     "baselineLock": npm_lock_resolved_instance(packages, key[2], "" if key[0] == "." else key[0]),
                     "selectedVersion": (result["assignment"] or {}).get(declaration_id(key))}
                    for key in sorted(admitted)
                ],
            }
        finally:
            sys.path.pop(0)
        return {**result, "inventoryDigest": digest, "candidateEvidence": "CORE_REGISTRY" if health_goal else "CALLER_SNAPSHOT",
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
    if not all(configured[:2]):
        return {"status": "NOT_RUN", "code": "WORKSPACE_PREFLIGHT_HOST_DISABLED"}
    artifacts, cache = (Path(value) for value in configured[:2])
    project = project.resolve()
    if (not artifacts.is_absolute() or not cache.is_absolute() or not cache.is_dir()
            or any(path.resolve().is_relative_to(project) or project.is_relative_to(path.resolve())
                   for path in (artifacts, cache))):
        raise GoalContractError("WORKSPACE_PREFLIGHT_HOST_PATH_INVALID")
    try:
        registry = _workspace_registry(configured[2], inputs)
    except GoalContractError as exc:
        return {"status": "NOT_RUN", "code": exc.code}
    except AuditError:
        raise GoalContractError("WORKSPACE_PREFLIGHT_HOST_REGISTRY_INVALID") from None
    if not registry:
        return {"status": "NOT_RUN", "code": "WORKSPACE_PREFLIGHT_HOST_DISABLED"}

    from workspace_npm_preflight import run_workspace_npm_preflight

    if "policy" in plan and plan["policy"]["evidence"]["registryIdentitySha256"] != sha256(registry.encode()).hexdigest():
        return {"status": "NOT_RUN", "code": "WORKSPACE_POLICY_RESOLVER_REGISTRY_MISMATCH"}
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
