"""Goal-aware read-only MCP boundary, separate from existing health planning."""
from __future__ import annotations
import json
import math
import time
import os
import sys
from pathlib import Path
from typing import Any
from mcp_types import ToolAnnotations
from deploom_mcp.goal_contract import GoalContractError, assert_current_binding, validate_goal
from deploom_mcp.process_runner import run_process_async

async def get_goal_plan(project_path: str, goal: dict[str, Any], deadline_seconds: float = 30.0, migration_evidence_request: dict | None = None) -> dict:
    started=time.monotonic()
    try:
        if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float)) or not math.isfinite(deadline_seconds) or not 5 <= deadline_seconds <= 60:
            raise GoalContractError("INVALID_DEADLINE")
        validated = validate_goal(goal)
        encoded = json.dumps(validated, allow_nan=False)
        if len(encoded) > 65536:
            raise GoalContractError("GOAL_TOO_LARGE")
        if not isinstance(project_path, str) or not Path(project_path).is_absolute():
            raise GoalContractError("INVALID_PROJECT_PATH")
        result = await run_process_async([sys.executable, "-B", "-m", ("deploom_mcp.goal_choice_worker" if validated["schemaVersion"] == 3 else "deploom_mcp.goal_worker"), project_path, encoded],
            cwd=str(Path(__file__).parent), env=dict(os.environ), timeout=deadline_seconds, max_output_bytes=128*1024)
        if result.timed_out:
            return {"ok": False, "error": {"code": "GOAL_BINDING_TIMEOUT"}, "verification": "NOT_VERIFIED"}
        try:
            payload = json.loads(result.stdout)
        except (ValueError, TypeError):
            return {"ok": False, "error": {"code": "GOAL_BINDING_PROCESS_FAILED"}, "verification": "NOT_VERIFIED"}
        if not isinstance(payload, dict) or (result.returncode and payload.get("ok")):
            return {"ok": False, "error": {"code": "GOAL_BINDING_PROCESS_FAILED"}, "verification": "NOT_VERIFIED"}
        if payload.get("ok"):
            binding = payload.get("binding")
            if (payload.get("authority") != "PLANNING_ONLY" or payload.get("verification") != "NOT_VERIFIED"
                    or payload.get("executionReady") is not False or not isinstance(binding, dict)):
                raise GoalContractError("INVALID_GOAL_WORKER_PROTOCOL")
            assert_current_binding(binding, validated, binding.get("identity", {}))
            if validated["schemaVersion"] == 3:
                from deploom_mcp.goal_choice_contract import validate_choice_response
                validate_choice_response(payload, validated)
            if migration_evidence_request is not None:
                from deploom_mcp.migration_evidence import attach_to_plan
                return await attach_to_plan(payload,project_path,migration_evidence_request,deadline_seconds-(time.monotonic()-started))
        return payload
    except GoalContractError as exc:
        return {"ok": False, "error": {"code": exc.code}, "verification": "NOT_VERIFIED"}
    except (OSError, ValueError, TypeError):
        return {"ok": False, "error": {"code": "GOAL_INPUT_UNAVAILABLE"}, "verification": "NOT_VERIFIED"}

def register_goal_tools(server, result_builder) -> None:
    from deploom_mcp.goal_report import get_goal_report
    from deploom_mcp.goal_candidate import verify_goal_candidate

    from deploom_mcp.goal_final_candidate import apply_goal_candidate,get_goal_checkpoint

    @server.tool(name="apply_goal_candidate",title="Apply an exact host-authorized candidate and publish a scoped checkpoint",
        description="Requires separately pinned final capability and closed historical candidate receipt. Windows ordinary NTFS standalone projects with root npm lock v2 or v3 only. Preserves preimages and old node_modules, runs offline frozen staging and actual workspace checks, reopens durable source before checkpoint pointer commit. IDs/binding/explicit operation only; no caller paths, commands or proofs. Same-attempt recovery keeps original deadline. Selected host profile only, fullAcceptance=false; no product-wide or untested runtime guarantee.",
        annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=True,openWorldHint=False))
    async def apply_goal_candidate_tool(run_id: str,candidate_id: str,expected_binding_digest: str,authorization: str=""):
        return result_builder(await apply_goal_candidate(run_id,candidate_id,expected_binding_digest,authorization))

    @server.tool(name="get_goal_checkpoint",title="Read a host-produced verified goal checkpoint",
        description="Read-only completed checkpoint, exact registered IDs/binding. Default current preserves strict metadata freshness and current continuity checks. Explicit historical verifies owner-record integrity and selected-profile binding with expired metadata; it does not reevaluate the mathematical model, verify current state or authorize execution. No fallback, new execution budget, installation or project check rerun.",
        annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,openWorldHint=False))
    async def get_goal_checkpoint_tool(run_id: str,candidate_id: str,expected_binding_digest: str,mode: str='current'):
        return result_builder(await get_goal_checkpoint(run_id,candidate_id,expected_binding_digest,mode=mode))

    @server.tool(name="verify_goal_candidate", title="Run an exact host-authorized isolated candidate verification",
        description="Requires host-registered run/candidate, expected binding and explicit verify-private-candidate operation. Exact candidate files, complete check profile and permission are pinned host configuration only. Offline Core verification supports engineering controls and PREAPPROVED_HOST_POLICY private execution. Permission alone is not acceptance. Explicit normative profiles may admit a historical isolated candidate only after actual physical dependency guards and cleanup. Final workspace apply/checkpoint requires the separate apply_goal_candidate capability; this isolated tool never grants it. Full-goal acceptance remains unproven. Imports/closed runs/baseline-observation rejected. No caller paths, commands, assignments or proofs. Never reruns stopped S1/BR1, no full acceptance or legacy promotion.",
        annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,openWorldHint=False))
    async def verify_goal_candidate_tool(run_id: str,candidate_id: str,expected_binding_digest: str,authorization: str=""):
        return result_builder(await verify_goal_candidate(run_id,candidate_id,expected_binding_digest,authorization))


    @server.tool(name="get_goal_report", title="Read an explicitly unverified goal observation report",
        description="Read run_id only from operator-controlled goal-runs state, rebind current source/lock/toolchain/goal, preserve the full required-check denominator. Imported artifacts stay CLIENT_REPORTED/NOT_VERIFIED; hashes and reported PASS never grant verification or full attainment. No arbitrary proof path, no execution or project changes.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def get_goal_report_tool(run_id: str, expected_goal_digest: str):
        return result_builder(await get_goal_report(run_id,expected_goal_digest,get_goal_plan))

    @server.tool(name="get_goal_plan", title="Bind an explicit functional-maintenance goal",
        description="Read-only goal proposal bound to current source, lock, implementation and toolchain. Exact v1/v2 uses no combinatorial solver. Finite-domain coupled v3 uses host-pinned fresh root metadata and Core/Z3, preserves parent and selection lineage in a derived exact intent. Declared-root optimality is not full npm placement, installation, health, security or project verification. REPORT_ONLY never lowers Yellow policy. PLANNING_ONLY/NOT_VERIFIED; executionReady=false until independent host admission and all registered checks. Optional migration_evidence_request adds read-only official references and syntax observations within this deadline; it never grants verification or execution authority. Omitted request performs no knowledge network work.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
    async def get_goal_plan_tool(project_path: str, goal: dict[str, Any], deadline_seconds: float = 30.0, migration_evidence_request: dict | None = None):
        if migration_evidence_request is None:
            return result_builder(await get_goal_plan(project_path, goal, deadline_seconds))
        return result_builder(await get_goal_plan(project_path, goal, deadline_seconds,migration_evidence_request))
