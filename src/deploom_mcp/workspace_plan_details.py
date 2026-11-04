"""Read-only, bounded details from the exact published workspace plan."""
from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
import json
import re
from secrets import token_urlsafe
from threading import Lock
from time import monotonic

from deploom_mcp.blueprint import _validate_blueprint_schema
from deploom_mcp.distribution import blueprint_schema_path

_DETAILS_TTL = 900
_MAX_PLANS = 16
_HANDLE = re.compile(r"[a-zA-Z0-9_-]{43}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_PLANS: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
_LOCK = Lock()


def _key(row: dict) -> str:
    return json.dumps(row["declaration"], ensure_ascii=True, separators=(",", ":"))


def _native_receipt(receipt: dict) -> dict:
    fields = ("status", "code", "authority", "npmExitCode", "resolverIdentitySha256",
              "baselineLockSha256", "candidateLockSha256", "learningAllowed", "immutableInstanceCount")
    result = {key: receipt[key] for key in fields if key in receipt}
    if "attempts" in receipt:
        result["commands"] = [
            {key: row[key] for key in ("phase", "npmExitCode", "outputSha256", "status", "code") if key in row}
            for row in receipt["attempts"]
        ]
    if "lockDelta" in receipt:
        delta = receipt["lockDelta"]
        result["lockDelta"] = {key: delta[key] for key in (
            "status", "code", "changedPackageCount", "changedEdgeCount", "impactedInstanceCount", "unrelatedCount"
        ) if key in delta}
    return result


def prepare_workspace_details(compact: dict, plan: dict) -> tuple[dict, bytes]:
    """Freeze allowlisted evidence. Registry snapshots, log bodies and paths stay out."""
    validation = plan.get("packageManagerValidation", compact["packageManagerValidation"])
    search = plan.get("candidateSearch")
    receipts = []
    if search:
        receipts = [
            {"candidateId": summary["candidateId"], **_native_receipt(row["validation"])}
            for summary, row in zip(compact["packageManagerValidation"]["search"]["attempts"], search["attempts"], strict=True)
        ]
    constraint_model = dict(compact["constraintModel"])
    if constraint_model["reason"] is not None:
        code = constraint_model["reason"].split(":", 1)[0]
        constraint_model["reason"] = code if re.fullmatch(r"[A-Z][A-Z0-9_]{1,80}", code) else "MODEL_REASON_REDACTED"
    evidence = {
        **{key: compact[key] for key in ("inventoryDigest", "requestId", "candidateId", "authority", "verification",
                                        "executionReady", "installation", "checks", "runtime", "acceptance", "policy")},
        "constraintModel": constraint_model,
        "constraints": plan.get("constraintEvidence"),
        "targets": compact["jointAssignment"]["targets"],
        "packageManagerValidation": _native_receipt(validation),
        "search": compact["packageManagerValidation"].get("search"),
        "candidateReceipts": receipts,
    }
    data = json.dumps(evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    return {"planId": token_urlsafe(32), "snapshotSha256": sha256(data).hexdigest(),
            "expiresInSeconds": _DETAILS_TTL}, data


def retain_workspace_details(plan_id: str, data: bytes) -> None:
    with _LOCK:
        now = monotonic()
        while _PLANS and (len(_PLANS) >= _MAX_PLANS or next(iter(_PLANS.values()))[0] <= now):
            _PLANS.popitem(last=False)
        _PLANS[plan_id] = (now + _DETAILS_TTL, data)


def get_workspace_plan_details(
    *, plan_id: str, request_id: str, candidate_id: str | None, snapshot_sha256: str,
    owner: str, section: str, offset: int = 0, limit: int = 8,
) -> dict:
    """Read one owner/section page using the process-local plan capability."""
    unavailable = {"ok": False, "error": {"code": "WORKSPACE_DETAILS_UNAVAILABLE"}}
    if (not isinstance(plan_id, str) or not _HANDLE.fullmatch(plan_id)
            or not isinstance(request_id, str) or not _DIGEST.fullmatch(request_id)
            or not isinstance(snapshot_sha256, str) or not _DIGEST.fullmatch(snapshot_sha256)
            or (candidate_id is not None and (not isinstance(candidate_id, str) or not _DIGEST.fullmatch(candidate_id)))
            or not isinstance(owner, str) or not 1 <= len(owner) <= 512
            or section not in ("dependencies", "devDependencies", "optionalDependencies")
            or type(offset) is not int or not 0 <= offset <= 64
            or type(limit) is not int or not 1 <= limit <= 16):
        return unavailable
    with _LOCK:
        stored = _PLANS.get(plan_id)
        if stored is None or stored[0] <= monotonic():
            _PLANS.pop(plan_id, None)
            return unavailable
        data = stored[1]
    if sha256(data).hexdigest() != snapshot_sha256:
        return unavailable
    evidence = json.loads(data)
    if (evidence["requestId"], evidence["candidateId"]) != (request_id, candidate_id):
        return unavailable
    targets = [row for row in evidence.pop("targets") if row["declaration"][:2] == [owner, section]]
    if not targets or offset >= len(targets):
        return unavailable

    constraints = evidence.pop("constraints")
    scope = {_key(row) for row in targets}
    page = targets[offset:offset + limit]
    page_keys = {_key(row) for row in page}
    rules = []
    if constraints is not None:
        for rule in constraints["rules"]:
            consumer, provider = rule["consumer"], rule["provider"]
            if consumer not in page_keys and provider not in page_keys:
                continue
            rules.append({
                "consumer": json.loads(consumer) if consumer in scope else None,
                "candidateVersion": rule["version"] if consumer in scope else None,
                "provider": json.loads(provider) if provider in scope else None,
                "providerContext": "NONE" if provider is None else "IN_SCOPE" if provider in scope else "EXTERNAL",
                "allowedVersions": rule["allowedVersions"] if provider in scope else None,
                "provenance": rule["provenance"],
            })
    for row in page:
        row["modeledDomain"] = constraints["domains"].get(_key(row)) if constraints is not None else None
    rule_coverage = ("UNAVAILABLE" if constraints is None else
                     "PARTIAL" if constraints["ruleCount"] > len(constraints["rules"]) else "COMPLETE_IN_MODELED_SCOPE")
    payload = {"ok": True, "schemaVersion": 2, "kind": "npm-workspace-plan-details",
               "snapshotSha256": snapshot_sha256, "scope": {"owner": owner, "section": section},
               "page": {"offset": offset, "total": len(targets),
                        "nextOffset": offset + len(page) if offset + len(page) < len(targets) else None},
               "targets": page, "rules": rules, "ruleCoverage": rule_coverage, **evidence}
    if _validate_blueprint_schema(payload, blueprint_schema_path(workspace=True), definition="details") is not None:
        return {"ok": False, "error": {"code": "WORKSPACE_BLUEPRINT_SCHEMA_INVALID"}}
    return payload
