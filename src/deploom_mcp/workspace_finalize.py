"""Current-state gate for a host-approved local workspace attempt."""
from __future__ import annotations

import hashlib
from pathlib import Path
import time

from deploom_mcp.goal_contract import canonical_digest
from deploom_mcp.goal_workspace_observation import workspace_profile_complete
from deploom_mcp.workspace_verification_attempt import (
    _load, _paths, read_workspace_attempt, workspace_verification_admission, workspace_verification_profile,
)


def workspace_toolchain():
    from draft_npm_preflight import _npm_command

    node, npm = _npm_command()
    return {"nodeSha256": hashlib.sha256(Path(node).read_bytes()).hexdigest(),
            "npmCliSha256": hashlib.sha256(Path(npm).read_bytes()).hexdigest()}


def finalize_workspace_profile(output, project, *, source_key, plan, commands, install,
                               expected_receipt_sha256, seconds):
    """Re-observe current state without replaying commands or granting apply."""
    from resolved_dependency_state import MaterializedDependencyObligationError, observe_npm_dependency_obligations
    from source_snapshot import source_snapshot_fingerprint

    deadline = time.monotonic() + seconds

    def remaining():
        seconds_left = deadline - time.monotonic()
        if seconds_left <= 0:
            raise ValueError("WORKSPACE_FINALIZATION_DEADLINE")
        return seconds_left

    output, journal = _paths(output)
    if output.is_relative_to(project):
        raise ValueError("WORKSPACE_FINALIZATION_EVIDENCE_INSIDE_TRIAL")
    state = read_workspace_attempt(output)
    if (state["status"] != "CURRENT_WORKSPACE_PROFILE_OBSERVED"
            or state["receiptSha256"] != expected_receipt_sha256):
        raise ValueError("WORKSPACE_FINALIZATION_RECEIPT_MISMATCH")
    profile = workspace_verification_profile(plan, commands, install)
    admission = workspace_verification_admission(project, source_key, plan, profile)
    run = _load(journal / "running.json", journal)
    proof = _load(output, output.parent)
    expected_install = ("FRESH_COLD_FROZEN_INSTALL_OBSERVED" if install
                        else "EXISTING_TREE_PHYSICALLY_OBSERVED")
    if (run["admission"] != admission or run["admissionSha256"] != state["admissionSha256"]
            or run["attemptId"] != state["attemptId"]
            or canonical_digest(proof) != expected_receipt_sha256
            or not workspace_profile_complete(profile, source_key, proof, plan, project)
            or proof["materializedSourceOrigin"]["toolchain"] != plan["resolverToolchain"]
            or proof["installation"]["status"] != expected_install
            or proof["installation"]["scriptsEnabled"] is not False):
        raise ValueError("WORKSPACE_FINALIZATION_PROFILE_MISMATCH")

    def check_boundary():
        if source_snapshot_fingerprint(project, remaining_budget=remaining) != source_key:
            raise ValueError("WORKSPACE_FINALIZATION_SOURCE_CHANGED")
        if workspace_toolchain() != plan["resolverToolchain"]:
            raise ValueError("WORKSPACE_FINALIZATION_TOOLCHAIN_CHANGED")

    check_boundary()
    try:
        tree = observe_npm_dependency_obligations(
            project, plan, source_origin_digest=proof["deliveredSourceObservationDigest"],
            resolved_state_key=proof["resolvedStateKey"], deadline=deadline,
        )
    except MaterializedDependencyObligationError as exc:
        # Retain the typed reason while keeping physical paths out of the CLI output.
        raise ValueError(str(exc).partition(":")[0]) from exc
    if tree != proof["materializedDependencyObligations"]:
        raise ValueError("WORKSPACE_FINALIZATION_TREE_CHANGED")
    check_boundary()
    remaining()
    return {"schemaVersion": 1, "status": "WORKSPACE_PROFILE_CURRENT",
            "attemptId": state["attemptId"], "verificationReceiptSha256": expected_receipt_sha256,
            "admissionSha256": state["admissionSha256"], "profileSha256": admission["profileSha256"],
            "sourceSnapshotKey": source_key, "lockSha256": plan["approvedLockSha256"],
            "resolverIdentitySha256": plan["resolverIdentitySha256"], "toolchain": plan["resolverToolchain"],
            "physicalTreeSha256": tree["materializedTreeObservationDigest"],
            "checkCount": len(commands), "installation": expected_install,
            "finalPolicy": "NOT_ASSESSED", "applyAuthorized": False, "fullAcceptance": False}
