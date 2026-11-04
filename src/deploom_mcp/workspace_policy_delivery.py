"""Bind a host-approved native policy audit to a delivery candidate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from deploom_mcp.audit_worker import read_bytes
from deploom_mcp.goal_contract import canonical_digest
from deploom_mcp.goal_report import _plain_under
from deploom_mcp.workspace_policy_request import validate_workspace_policy, workspace_health_policy


def _read(path):
    path = Path(path).absolute()
    return read_bytes(_plain_under(path, path.parent))


def load_delivery_policy(audit_path, expected_sha256, policy_path, plan):
    """Require exact host-selected audit bytes and a PASS for the approved policy."""
    from dependency_live_roadmap_generator import required_ratio_count

    options = validate_workspace_policy(json.loads(_read(policy_path)))
    policy_digest = canonical_digest(options)
    raw = _read(audit_path)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("WORKSPACE_DELIVERY_AUDIT_DIGEST_MISMATCH")
    report = json.loads(raw)
    candidate = report["candidateAudit"]
    assessment = candidate["policyAssessment"]
    lag = report["lag"]
    manifests = {(PurePosixPath(owner) / "package.json").as_posix(): record["manifestSha256"]
                 for owner, record in plan["workspaceOwners"].items()}
    files = {**manifests, "package-lock.json": plan["approvedLockSha256"]}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if (report["protocol"] != "dependency-audit" or report["schemaVersion"] != 1 or report["packageManager"] != "npm"
            or report["ok"] is not True or report["workerExitCode"] != 0 or report["inputIntegrity"] != "UNCHANGED"
            or report["sourceFiles"] != files or report["sourceInputSha256"] != digest
            or report["coverage"]["complete"] is not True or report["coverage"]["unresolvedEdges"] != []
            or sorted(report["coverage"]["manifests"]) != sorted(manifests)
            or report["security"]["complete"] is not True or report["security"]["nodeUnit"] != "LOCKFILE_PATHS"
            or candidate["basis"] != "EXACT_RESOLVER_CANDIDATE"
            or candidate["status"] != "BOUND_SECURITY_AUDIT_COMPLETE"
            or candidate["sourceInputSha256"] != digest or candidate["lockSha256"] != plan["approvedLockSha256"]
            or candidate["resolverIdentitySha256"] != plan["resolverIdentitySha256"]
            or candidate["registrySha256"] != plan["registrySha256"]
            or assessment["policySha256"] != policy_digest or assessment["policy"] != options):
        raise ValueError("WORKSPACE_DELIVERY_AUDIT_BINDING_MISMATCH")
    if candidate["finalPolicy"] != "PASS" or assessment["verdict"] != "PASS" or lag["policyAttainment"] != "PASS":
        raise ValueError("WORKSPACE_DELIVERY_POLICY_NOT_MET")
    policy = workspace_health_policy(options)
    limits = policy.limits()
    counts = assessment["knownBySeverity"]
    total, confirmed = assessment["lagDenominator"], assessment["lagConfirmed"]
    if (report["status"] != "COMPLETE" or lag["complete"] is not True or lag["status"] != "COMPLETE"
            or lag["basis"] != "EXACT_OWNER_RESOLVED_CANDIDATE_LOCK" or lag["months"] != options["lagPolicyMonths"]
            or not isinstance(lag["metadataSnapshotSha256"], str)
            or not re.fullmatch("[a-f0-9]{64}", lag["metadataSnapshotSha256"])
            or type(total) is not int or type(confirmed) is not int or not 0 <= confirmed <= total or total == 0
            or total != lag["totalDeclarations"] or confirmed != lag["okDeclarations"]
            or total != len(lag["rows"]) or confirmed != sum(row["status"] == "ok" for row in lag["rows"])
            or any(row["status"] not in {"ok", "lagging"} for row in lag["rows"])
            or assessment["lagUnknown"] != 0 or lag["unknownDeclarations"] != 0
            or assessment["lagRequired"] != required_ratio_count(total, policy.ratio())
            or lag["requiredDeclarations"] != assessment["lagRequired"] or confirmed < assessment["lagRequired"]
            or assessment["securityUnit"] != "NATIVE_NPM_UNIQUE_PACKAGE_NAMES_PER_SEVERITY"
            or assessment["severityLimits"] != limits or assessment["exceededSeverityCaps"] != []
            or not isinstance(counts, dict) or set(counts) != set("CHMLU") or counts["U"] != 0
            or any(type(value) is not int or value < 0 for value in counts.values())
            or any((counts["M"] + counts["L"] if level == "M+L" else counts[level]) > cap
                   for level, cap in limits.items())):
        raise ValueError("WORKSPACE_DELIVERY_AUDIT_INCOMPLETE")
    return {"auditSha256": expected_sha256, "policySha256": policy_digest, "targetLevel": options["targetLevel"],
            "generatedAt": report["generatedAt"], "lagConfirmed": confirmed, "lagDenominator": total,
            "knownBySeverity": counts}
