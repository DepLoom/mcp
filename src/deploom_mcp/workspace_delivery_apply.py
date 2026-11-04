"""Apply a separately approved workspace delivery to an isolated copy."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import time
import uuid

from deploom_mcp.audit_worker import read_bytes
from deploom_mcp.goal_contract import canonical_digest
from deploom_mcp.goal_delta_trial import _write_exact_files
from deploom_mcp.goal_final_transaction import assert_partial_source
from deploom_mcp.goal_report import _plain_under
from deploom_mcp.workspace_finalize import workspace_toolchain
from deploom_mcp.workspace_verification_attempt import _publish


def apply_workspace_delivery(baseline, delivery, destination, *, expected_receipt_sha256, expected_profile_sha256,
                             goal, expected_policy_sha256, seconds):
    """Check all preimages, then write only into a new host-owned workspace."""
    from source_snapshot import (build_source_tree_manifest, materialize_source_for_verification,
                                 open_source_snapshot, source_snapshot_live_source_continuity,
                                 verification_source_snapshot_scope)
    from verification_source_closure import require_complete_source_policy

    deadline = time.monotonic() + seconds

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise ValueError("WORKSPACE_APPLY_DEADLINE")
        return left

    baseline, delivery, destination = (Path(value).absolute() for value in (baseline, delivery, destination))
    for directory in (baseline, delivery, destination):
        _plain_under(directory, directory.parent)
    if (any(path != path.resolve(strict=True) or not path.is_dir()
            for path in (baseline, delivery, destination.parent))
            or destination.exists()
            or any(a.is_relative_to(b) or b.is_relative_to(a)
                   for a, b in ((baseline, delivery), (baseline, destination), (delivery, destination)))):
        raise ValueError("WORKSPACE_APPLY_PATH_INVALID")

    receipt_path = _plain_under(delivery / "receipt.json", delivery)
    raw = read_bytes(receipt_path)
    if hashlib.sha256(raw).hexdigest() != expected_receipt_sha256:
        raise ValueError("WORKSPACE_APPLY_DELIVERY_MISMATCH")
    receipt = json.loads(raw)
    expected_status = {"targeted": "TARGETED_WORKSPACE_SOURCE_DELIVERED", "policy": "POLICY_WORKSPACE_SOURCE_DELIVERED"}[goal]
    if (receipt["schemaVersion"] != 1 or receipt["status"] != expected_status
            or receipt["profileSha256"] != expected_profile_sha256
            or receipt["installation"] != "FRESH_COLD_FROZEN_INSTALL_OBSERVED"
            or receipt["sourceSnapshotDirectory"] != "verified-source"
            or receipt["applyAuthorized"] is not False
            or (goal == "policy" and (receipt["finalPolicy"] != "PASS"
                or receipt["policyEvidence"]["policySha256"] != expected_policy_sha256))
            or (goal == "targeted" and (receipt["finalPolicy"] != "NOT_ASSESSED" or expected_policy_sha256 is not None))):
        raise ValueError("WORKSPACE_APPLY_APPROVAL_MISMATCH")
    changes = receipt["exactFileDelta"]
    if (not isinstance(changes, list) or not 1 <= len(changes) <= 321
            or canonical_digest(changes) != receipt["exactFileDeltaSha256"]):
        raise ValueError("WORKSPACE_APPLY_DELTA_INVALID")
    files = {}
    for row in changes:
        name = row["path"]
        if (set(row) != {"path", "beforeSha256", "afterSha256"} or not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_ ./-]{1,512}", name)
                or any(not part or part.rstrip(" .") != part or part.casefold() in {".git", "node_modules"}
                       for part in name.split("/"))
                or not all(re.fullmatch("[a-f0-9]{64}", row[key]) for key in ("beforeSha256", "afterSha256"))
                or row["beforeSha256"] == row["afterSha256"]
                or name.casefold() in files):
            raise ValueError("WORKSPACE_APPLY_DELTA_INVALID")
        files[name.casefold()] = row
    snapshot_path = _plain_under(delivery / "verified-source", delivery)
    if not _plain_under(snapshot_path / "tree", delivery).is_dir():
        raise ValueError("WORKSPACE_APPLY_DELIVERY_MISMATCH")
    candidate = open_source_snapshot(snapshot_path, expected_key=receipt["deliveredSourceSnapshotKey"],
                                     timeout_seconds=remaining())
    if candidate.project_relative != Path(".") or candidate.key != receipt["sourceSnapshotKey"]:
        raise ValueError("WORKSPACE_APPLY_DELIVERY_MISMATCH")
    require_complete_source_policy(candidate.input_policy)

    def manifest(path, policy):
        return build_source_tree_manifest(path, policy=policy, timeout_seconds=remaining(), exact_timeout=True)

    def ensure_current(before):
        if not source_snapshot_live_source_continuity(before, timeout_seconds=remaining()):
            raise ValueError("WORKSPACE_APPLY_BASELINE_CHANGED")
        if workspace_toolchain() != receipt["toolchain"]:
            raise ValueError("WORKSPACE_APPLY_TOOLCHAIN_CHANGED")

    with verification_source_snapshot_scope(baseline, timeout_seconds=remaining(), strict_cleanup=True) as before:
        require_complete_source_policy(before.input_policy)
        if (before.key != receipt["baselineSourceSnapshotKey"] or before.capture_root != baseline
                or before.project_relative != Path(".") or before.policy_key != candidate.policy_key):
            raise ValueError("WORKSPACE_APPLY_BASELINE_MISMATCH")
        left, right = manifest(before.root, before.input_policy), manifest(candidate.root, candidate.input_policy)
        if left.key != before.manifest_key or right.key != candidate.manifest_key:
            raise ValueError("WORKSPACE_APPLY_SNAPSHOT_CHANGED")
        old, new = ({entry["path"]: entry for entry in item.entries} for item in (left, right))
        assert_partial_source(old, new, changes)
        postimages = []
        for row in changes:
            name = row["path"]
            if new[name]["sha256"] != row["afterSha256"]:
                raise ValueError("WORKSPACE_APPLY_POSTIMAGE_MISMATCH")
            path = _plain_under(candidate.root / PurePosixPath(name), candidate.root)
            raw = read_bytes(path)
            if hashlib.sha256(raw).hexdigest() != row["afterSha256"]:
                raise ValueError("WORKSPACE_APPLY_POSTIMAGE_MISMATCH")
            postimages.append((name, raw))
        if new.get("package-lock.json", {}).get("sha256") != receipt["lockSha256"]:
            raise ValueError("WORKSPACE_APPLY_POSTIMAGE_MISMATCH")
        ensure_current(before)
        destination.mkdir()
        attempt = {"schemaVersion": 1, "attemptId": uuid.uuid4().hex,
                   "deliverySha256": expected_receipt_sha256, "profileSha256": expected_profile_sha256,
                   "goal": goal, "policySha256": expected_policy_sha256}
        _publish(destination / "intent.json", {**attempt, "state": "RUNNING"})
        try:
            with tempfile.TemporaryDirectory(prefix=".apply-", dir=destination) as directory:
                staged, snapshot, _ = materialize_source_for_verification(
                    baseline, Path(directory) / "project", timeout_seconds=remaining())
                if snapshot.key != before.key:
                    raise ValueError("WORKSPACE_APPLY_BASELINE_CHANGED")
                _write_exact_files(staged, postimages)
                if manifest(staged, before.input_policy).key != candidate.manifest_key:
                    raise ValueError("WORKSPACE_APPLY_POSTIMAGE_MISMATCH")
                ensure_current(before)
                remaining()
                staged.rename(destination / "project")
        except KeyboardInterrupt:
            _publish(destination / "terminal.json", {**attempt, "state": "CANCELLING"})
            raise
        except (OSError, RuntimeError, ValueError):
            _publish(destination / "terminal.json", {**attempt, "state": "UNPROVEN"})
            raise
    remaining()
    result = {**attempt, "state": "APPLIED", "status": "WORKSPACE_APPLIED_TO_ISOLATED_COPY",
              "projectDirectory": "project", "sourceSnapshotKey": candidate.key,
              "lockSha256": receipt["lockSha256"], "exactFileDeltaSha256": receipt["exactFileDeltaSha256"],
              "installation": "NOT_RUN_IN_DELIVERED_COPY", "finalPolicy": receipt["finalPolicy"],
              "fullAcceptance": False}
    _publish(destination / "terminal.json", result)
    return result
