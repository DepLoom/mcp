"""Publish a host-approved targeted workspace delta as an owned source snapshot."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path, PurePosixPath
import re
import time

from deploom_mcp.goal_contract import canonical_digest
from deploom_mcp.goal_final_transaction import assert_partial_source
from deploom_mcp.goal_report import _plain_under
from deploom_mcp.workspace_finalize import finalize_workspace_profile
from deploom_mcp.workspace_verification_attempt import _publish


def _authorized_files(resolver, plan, source_repairs):
    identity = resolver["identity"]
    before, after = identity["sourceManifestSha256"], identity["candidateManifestSha256"]
    if set(before) != set(after):
        raise ValueError("WORKSPACE_DELIVERY_OWNER_SET_MISMATCH")
    files = {"package-lock.json": (plan["baselineLockSha256"], plan["approvedLockSha256"])}
    for owner in before:
        path = (PurePosixPath(owner) / "package.json").as_posix()
        files[path] = (before[owner], after[owner])
    if not isinstance(source_repairs, list) or len(source_repairs) > 64:
        raise ValueError("WORKSPACE_DELIVERY_SOURCE_SCOPE_INVALID")
    for row in source_repairs:
        if not isinstance(row, dict) or set(row) != {"path", "beforeSha256", "afterSha256"}:
            raise ValueError("WORKSPACE_DELIVERY_SOURCE_SCOPE_INVALID")
        name = row["path"]
        if (not isinstance(name, str) or len(name) > 512
                or any(not re.fullmatch(r"[A-Za-z0-9_ .-]+", part) or part.rstrip(" .") != part
                       or part.casefold() in {".git", "node_modules"}
                       for part in name.split("/"))
                or PurePosixPath(name).name.casefold() in {"package.json", "package-lock.json", "npm-shrinkwrap.json", ".npmrc"}
                or name.casefold() in {path.casefold() for path in files}):
            raise ValueError("WORKSPACE_DELIVERY_SOURCE_SCOPE_INVALID")
        files[name] = (row["beforeSha256"], row["afterSha256"])
    if any(not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
           for pair in files.values() for digest in pair):
        raise ValueError("WORKSPACE_DELIVERY_SOURCE_SCOPE_INVALID")
    return files


def deliver_workspace_source(destination, baseline, project, *, baseline_key, source_key, plan,
                             resolver, commands, attempt_receipt, expected_receipt_sha256, source_repairs, seconds,
                             policy_evidence=None):
    """Publish only into a new destination. The verified trial and baseline are read-only."""
    from source_snapshot import (
        build_source_tree_manifest, persist_source_snapshot, source_snapshot_live_source_continuity,
        verification_source_snapshot_scope,
    )
    from verification_source_closure import require_complete_source_policy

    deadline = time.monotonic() + seconds

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise ValueError("WORKSPACE_DELIVERY_DEADLINE")
        return left

    def related(left, right):
        return left.is_relative_to(right) or right.is_relative_to(left)

    destination = Path(destination).absolute()
    baseline = Path(baseline).absolute()
    _plain_under(destination, destination.parent)
    _plain_under(baseline, baseline)
    if (baseline != baseline.resolve(strict=True) or not baseline.is_dir()
            or destination.parent != destination.parent.resolve(strict=True)
            or destination.exists() or related(baseline, project)
            or related(destination, project) or related(destination, baseline)):
        raise ValueError("WORKSPACE_DELIVERY_PATH_INVALID")
    files = _authorized_files(resolver, plan, source_repairs)
    changes = [{"path": name, "beforeSha256": before, "afterSha256": after}
               for name, (before, after) in sorted(files.items()) if before != after]
    with ExitStack() as stack:
        before, candidate = [
            stack.enter_context(verification_source_snapshot_scope(
                root, timeout_seconds=remaining(), strict_cleanup=True))
            for root in (baseline, project)
        ]
        for snapshot, root, key in ((before, baseline, baseline_key), (candidate, project, source_key)):
            require_complete_source_policy(snapshot.input_policy)
            if snapshot.key != key or snapshot.capture_root != root or snapshot.project_relative != Path("."):
                raise ValueError("WORKSPACE_DELIVERY_SOURCE_IDENTITY_MISMATCH")
        if before.policy_key != candidate.policy_key:
            raise ValueError("WORKSPACE_DELIVERY_SOURCE_POLICY_MISMATCH")
        old, new = [], []
        for snapshot, entries in ((before, old), (candidate, new)):
            manifest = build_source_tree_manifest(snapshot.root, policy=snapshot.input_policy,
                                                  timeout_seconds=remaining(), exact_timeout=True)
            if manifest.key != snapshot.manifest_key:
                raise ValueError("WORKSPACE_DELIVERY_SNAPSHOT_CHANGED")
            entries.extend(manifest.entries)
        old = {entry["path"]: entry for entry in old}
        new = {entry["path"]: entry for entry in new}
        assert_partial_source(old, new, changes)
        for name, (previous, proposed) in files.items():
            if (old.get(name, {}).get("sha256") != previous or new.get(name, {}).get("sha256") != proposed
                    or old[name]["kind"] != "file" or new[name]["kind"] != "file"):
                raise ValueError("WORKSPACE_DELIVERY_POSTIMAGE_MISMATCH")
        final = finalize_workspace_profile(
            attempt_receipt, project, source_key=source_key, plan=plan, commands=commands, install=True,
            expected_receipt_sha256=expected_receipt_sha256, seconds=remaining(),
        )
        for snapshot in (before, candidate):
            if not source_snapshot_live_source_continuity(snapshot, timeout_seconds=remaining()):
                raise ValueError("WORKSPACE_DELIVERY_SOURCE_CHANGED")
        remaining()
        destination.mkdir()
        stored = persist_source_snapshot(candidate, destination / "verified-source", timeout_seconds=remaining())
        result = {**final, "status": "TARGETED_WORKSPACE_SOURCE_DELIVERED",
                  "baselineSourceSnapshotKey": baseline_key, "deliveredSourceSnapshotKey": stored.key,
                  "exactFileDelta": changes, "exactFileDeltaSha256": canonical_digest(changes),
                  "sourceSnapshotDirectory": "verified-source"}
        if policy_evidence is not None:
            result.update(status="POLICY_WORKSPACE_SOURCE_DELIVERED", finalPolicy="PASS",
                          policyEvidence=policy_evidence)
    _publish(destination / "receipt.json", result)
    return result


def read_source_repairs(path):
    if path is None:
        return []
    from deploom_mcp.audit_worker import read_bytes

    path = Path(path).absolute()
    return json.loads(read_bytes(_plain_under(path, path.parent)))
