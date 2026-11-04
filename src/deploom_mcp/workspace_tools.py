"""Read-only bounded workspace discovery; never enters the goal execution path."""
from __future__ import annotations
import json
import math
import os
import re
import sys
from pathlib import Path
from mcp_types import ToolAnnotations
from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.workspace_inventory import safe_spec, validate_workspace_targets
from deploom_mcp.process_runner import run_process_async


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _valid_success(row, targets, expected_digest):
    try:
        if type(row["schemaVersion"]) is not int or row["schemaVersion"] != 1:
            return False
        if targets is not None:
            held = row["heldDeclarationsByOwner"]
            return (row["kind"] == "npm-workspace-declaration-proposal"
                and row["inventoryDigest"] == expected_digest and _sha(row["inventoryDigest"])
                and row["targets"] == sorted(targets, key=lambda r: (r["owner"], r["section"], r["name"]))
                and type(row["heldDeclarationCount"]) is int and 0 <= row["heldDeclarationCount"] <= 4096
                and _sha(row["heldDeclarationsDigest"])
                and isinstance(held, list) and 1 <= len(held) <= 256
                and all(isinstance(r["owner"], str) and type(r["count"]) is int and r["count"] >= 0 and _sha(r["digest"]) for r in held)
                and sum(r["count"] for r in held) == row["heldDeclarationCount"]
                and row["globalImmutableInstances"] == "NOT_SPECIFIED_BY_THIS_PROPOSAL")
        owners, declarations, identity = row["owners"], row["declarations"], row["identity"]
        if (row["kind"] != "npm-workspace-declaration-inventory" or row["discoveryComplete"] is not True
                or row["discovery"]["status"] != "COMPLETE" or row["discovery"]["reasons"] != []
                or not isinstance(owners, list) or not 1 <= len(owners) <= 256
                or not isinstance(declarations, list) or len(declarations) > 4096
                or not _sha(row["inventoryDigest"]) or canonical_digest(identity) != row["inventoryDigest"]
                or row["lockOwner"] != "." or type(row["lockfileVersion"]) is not int or row["lockfileVersion"] != 3
                or row["physicalTree"] != "NOT_OBSERVED"
                or row["sourceContinuity"] != "READ_TWICE_ONLY_NOT_AN_EXECUTION_BINDING"):
            return False
        owner_map={o["owner"]:o for o in owners}
        if len(owner_map)!=len(owners) or "." not in owner_map or identity["workspaceOwners"]!=[o["owner"] for o in owners]:
            return False
        inputs=identity["manifestInputs"]
        if set(inputs)!={"package-lock.json",*[o["manifestPath"] for o in owners]} or not all(_sha(v) for v in inputs.values()):
            return False
        for o in owners:
            if o["manifestPath"] != ("package.json" if o["owner"]=="." else o["owner"]+"/package.json") or o["manifestSha256"]!=inputs[o["manifestPath"]]:
                return False
        if (type(row["discovery"]["ownerCount"]) is not int or row["discovery"]["ownerCount"]!=len(owners)
                or type(row["discovery"]["declarationCount"]) is not int or row["discovery"]["declarationCount"]!=len(declarations)):
            return False
        for declaration in declarations:
            if (set(declaration)!={"owner","section","name","declaredSpec"}
                    or declaration["owner"] not in owner_map or not isinstance(declaration["name"],str)
                    or declaration["section"] not in {"dependencies","devDependencies","optionalDependencies","peerDependencies"}
                    or not safe_spec(declaration["declaredSpec"])):
                return False
        evidence = row["lockResolutionEvidence"]
        if evidence["authority"] != "LOCKFILE_ONLY":
            return False
        resolutions = evidence["rows"]
        if resolutions is not None:
            if not isinstance(resolutions, list) or len(resolutions) != len(declarations):
                return False
            for declaration, resolution in zip(declarations, resolutions):
                if set(resolution) != set(declaration) | {"lockPath", "lockVersion"}:
                    return False
                if any(resolution[key] != value for key, value in declaration.items()):
                    return False
                path, version = resolution["lockPath"], resolution["lockVersion"]
                if path is None and version is None:
                    continue
                if not isinstance(version, str) or not version or not isinstance(path, str):
                    return False
                owner = declaration["owner"]
                owner_parts = [] if owner == "." else owner.split("/")
                suffix = "node_modules/" + declaration["name"]
                candidates = {"/".join([*owner_parts[:i], suffix]) for i in range(len(owner_parts) + 1)}
                if path not in candidates or "\\" in path or ":" in path or any(
                        part in {"", ".", ".."} for part in path.split("/")):
                    return False
        links=row["workspaceLinks"]
        return (isinstance(links,list) and len(links)==len(owners)-1
                and {r["targetOwner"] for r in links}==set(owner_map)-{"."}
                and all(r["path"]=="node_modules/"+owner_map[r["targetOwner"]]["name"] for r in links))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


async def observe_workspace_inventory_v1(project_path: str, expected_inventory_digest: str = "",
                                         targets: list[dict] | None = None, deadline_seconds: float = 15) -> dict:
    try:
        if (isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float))
                or not math.isfinite(deadline_seconds) or not 5 <= deadline_seconds <= 30):
            raise GoalContractError("INVALID_DEADLINE")
        if not isinstance(project_path, str) or not Path(project_path).is_absolute():
            raise GoalContractError("INVALID_PROJECT_PATH")
        trusted_parent = Path(__file__).resolve().parent.parent
        bootstrap = "import sys,runpy;sys.path.insert(0," + repr(str(trusted_parent)) + ");runpy.run_module('deploom_mcp.workspace_inventory',run_name='__main__')"
        argv = [sys.executable, "-I", "-B", "-X", "utf8", "-c", bootstrap, project_path]
        if targets is not None:
            validate_workspace_targets(targets)
            if not isinstance(expected_inventory_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_inventory_digest):
                raise GoalContractError("INVALID_WORKSPACE_INVENTORY_DIGEST")
            request = json.dumps({"inventoryDigest": expected_inventory_digest, "targets": targets}, allow_nan=False)
            if len(request) > 65536:
                raise GoalContractError("INVALID_WORKSPACE_REQUEST")
            argv.append(request)
        elif expected_inventory_digest:
            raise GoalContractError("INVALID_WORKSPACE_REQUEST")
        child = await run_process_async(argv, cwd=str(trusted_parent), env={k:v for k,v in os.environ.items() if not k.upper().startswith("PYTHON")},
                                        timeout=deadline_seconds, max_output_bytes=600 * 1024)
        if child.timed_out:
            raise GoalContractError("WORKSPACE_OBSERVATION_TIMEOUT")
        result = json.loads(child.stdout)
        if not isinstance(result, dict):
            raise GoalContractError("WORKSPACE_WORKER_INVALID_RESULT")
        if child.returncode or result.get("ok") is not True:
            code = (result.get("error") or {}).get("code", "WORKSPACE_INPUT_UNAVAILABLE")
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z_]{1,80}", code):
                code = "WORKSPACE_INPUT_UNAVAILABLE"
            raise GoalContractError(code)
        row = result.get("proposal" if targets is not None else "inventory", {})
        if (row.get("executionReady") is not False or row.get("verification") != "NOT_VERIFIED"
                or row.get("jointAssignment") != "NOT_COMPUTED"
                or row.get("authority") != ("PLANNING_ONLY" if targets is not None else "READ_ONLY_INVENTORY")):
            raise GoalContractError("WORKSPACE_WORKER_INVALID_RESULT")
        if not _valid_success(row, targets, expected_inventory_digest):
            raise GoalContractError("WORKSPACE_WORKER_INVALID_RESULT")
        return result
    except GoalContractError as exc:
        partial = any(token in exc.code for token in ("LIMIT", "TIMEOUT"))
        unknown = exc.code in {"WORKSPACE_INPUT_UNAVAILABLE", "WORKSPACE_WORKER_INVALID_RESULT"}
        return {"ok": False, "error": {"code": exc.code},
                "discovery": {"status": "PARTIAL" if partial else "UNKNOWN" if unknown else "INVALID", "reasons": [exc.code]},
                "executionReady": False, "verification": "NOT_VERIFIED"}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"ok": False, "error": {"code": "WORKSPACE_INPUT_UNAVAILABLE"},
                "discovery": {"status": "UNKNOWN", "reasons": ["WORKSPACE_INPUT_UNAVAILABLE"]},
                "executionReady": False, "verification": "NOT_VERIFIED"}


async def plan_workspace_snapshot_v1(project_path: str, request: dict) -> dict:
    error = "INVALID_WORKSPACE_PLAN_REQUEST"
    try:
        if not isinstance(project_path, str) or not Path(project_path).is_absolute():
            raise GoalContractError("INVALID_PROJECT_PATH")
        payload = json.dumps(request, ensure_ascii=True, allow_nan=False)
        if not isinstance(request, dict) or len(payload) > 12000:
            raise GoalContractError(error)
        trusted_parent = Path(__file__).resolve().parent.parent
        bootstrap = ("import sys,runpy;sys.path.insert(0," + repr(str(trusted_parent))
                     + ");runpy.run_module('deploom_mcp.workspace_inventory',run_name='__main__')")
        argv = [sys.executable, "-I", "-B", "-X", "utf8", "-c", bootstrap,
                project_path, "plan-snapshot", payload]
        child = await run_process_async(
            argv, cwd=str(trusted_parent),
            env={key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")},
            timeout=75 if "preflight" in request else 15, max_output_bytes=600 * 1024)
        if child.timed_out:
            raise GoalContractError("WORKSPACE_PLAN_TIMEOUT")
        result = json.loads(child.stdout)
        if child.returncode or result.get("ok") is not True:
            code = result.get("error", {}).get("code", "WORKSPACE_INPUT_UNAVAILABLE")
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z_]{1,80}", code):
                code = "WORKSPACE_INPUT_UNAVAILABLE"
            raise GoalContractError(code)
        plan = result["plan"]
        if (plan["authority"] != "PLANNING_ONLY" or plan["verification"] != "NOT_VERIFIED"
                or plan["executionReady"] is not False or plan["candidateEvidence"] != "CALLER_SNAPSHOT"
                or plan["inventoryDigest"] != request.get("inventoryDigest")
                or plan["status"] not in {"SAT_IN_MODELED_DOMAIN", "UNSAT_IN_MODELED_DOMAIN", "UNKNOWN"}
                or (plan["status"] == "SAT_IN_MODELED_DOMAIN") != isinstance(plan["assignment"], dict)
                or (plan["status"] != "SAT_IN_MODELED_DOMAIN" and plan["assignment"] is not None)):
            raise GoalContractError("WORKSPACE_WORKER_INVALID_RESULT")
        receipt = plan.get("packageManagerValidation")
        if ("preflight" in request) != ("packageManagerValidation" in plan):
            raise GoalContractError("WORKSPACE_WORKER_INVALID_RESULT")
        if "preflight" in request:
            if not isinstance(receipt, dict) or receipt.get("status") not in {
                "NOT_RUN", "RESOLUTION_PASSED", "RESOLUTION_FAILED", "UNKNOWN"
            }:
                raise GoalContractError("WORKSPACE_WORKER_INVALID_RESULT")
            if receipt["status"] == "RESOLUTION_PASSED":
                artifact = receipt.get("resolverArtifactId")
                if not isinstance(artifact, str) or not re.fullmatch(r"workspace-npm-[a-z0-9_]{8}", artifact):
                    raise GoalContractError("WORKSPACE_WORKER_INVALID_RESULT")
            elif "resolverArtifactId" in receipt:
                raise GoalContractError("WORKSPACE_WORKER_INVALID_RESULT")
            if receipt["status"] == "NOT_RUN":
                if set(receipt) != {"status", "code"}:
                    raise GoalContractError("WORKSPACE_WORKER_INVALID_RESULT")
            elif (plan["status"] != "SAT_IN_MODELED_DOMAIN"
                  or receipt.get("authority") != "LOCKFILE_ONLY"
                  or receipt.get("installation") != "NOT_RUN"
                  or receipt.get("runtime") != "NOT_RUN"
                  or receipt.get("learningAllowed") is not False):
                raise GoalContractError("WORKSPACE_WORKER_INVALID_RESULT")
        return result
    except GoalContractError as exc:
        error = exc.code
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        pass
    return {"ok": False, "error": {"code": error}, "executionReady": False, "verification": "NOT_VERIFIED"}


def register_workspace_tools(server, result_builder):
    @server.tool(name="observe_workspace_inventory_v1", title="Observe npm-workspace declarations without execution",
        description="Read-only bounded root npm lock-v3 workspace inventory. Relative literal/single-star patterns only; no globstar, negation or brace expansion. Owners and declaration sections stay separate. When supported by bundled Core, report owner-specific lock paths and versions as lockfile-only evidence. Optional owner/section/name/fromSpec/toVersion targets require the current inventory digest and produce declaration intent only. No registry, solver, install, checks, repair, checkpoint or execution authority. Existing goal APIs still reject workspaces. Hashes bind dependency inputs only, not full source or runtime. Caps/errors never yield a complete truncated proposal.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def observe_workspace_inventory_tool(project_path: str, expected_inventory_digest: str = "",
                                                targets: list[dict] | None = None, deadline_seconds: float = 15):
        return result_builder(await observe_workspace_inventory_v1(project_path, expected_inventory_digest, targets, deadline_seconds))

    @server.tool(name="plan_workspace_snapshot_v1", title="Model a bounded npm workspace upgrade",
        description="Planning only. Takes the current workspace inventory digest, owner-scoped candidate lists, caller metadata and optionally a proposed instance topology. Optional host-enabled preflight writes only a scratch lock via offline npm. No package installation, physical-tree observation or execution authority.",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
    async def plan_workspace_snapshot_tool(project_path: str, request: dict):
        return result_builder(await plan_workspace_snapshot_v1(project_path, request))
