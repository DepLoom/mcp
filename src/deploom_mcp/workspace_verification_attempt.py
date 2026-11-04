"""Host-owned, single-use workspace verification observations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from deploom_mcp.goal_contract import canonical_digest
from deploom_mcp.goal_report import _plain_under
from deploom_mcp.goal_workspace_observation import workspace_profile_complete


def _publish(path, payload):
    """Commit a complete file without replacing an existing record."""
    fd, name = tempfile.mkstemp(prefix=".workspace-verify-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write((json.dumps(payload, ensure_ascii=True, allow_nan=False) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.link(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _paths(output):
    output = Path(output).absolute()
    journal = output.with_name(output.name + ".attempt")
    return _plain_under(output, output.parent), _plain_under(journal, output.parent)


def _load(path, journal):
    from deploom_mcp.audit_worker import read_bytes

    return json.loads(read_bytes(_plain_under(path, journal)))


def read_workspace_attempt(output):
    """Report persisted state only. A RUNNING marker is not process liveness."""
    output, journal = _paths(output)
    if not journal.exists():
        return {"status": "WORKSPACE_ATTEMPT_NOT_FOUND"}
    try:
        run = _load(journal / "running.json", journal)
        if (run["state"] != "RUNNING" or not re.fullmatch("[a-f0-9]{32}", run["attemptId"])
                or canonical_digest(run["admission"]) != run["admissionSha256"]):
            raise ValueError("invalid admission")
        result = {"attemptId": run["attemptId"], "admissionSha256": run["admissionSha256"],
                  "status": "WORKSPACE_ATTEMPT_INCOMPLETE", "processLiveness": "UNOBSERVED"}
        terminal_path = journal / "terminal.json"
        if terminal_path.exists():
            terminal = _load(terminal_path, journal)
            receipt = _load(output, output.parent)
            if (terminal["state"] not in {"OBSERVED", "UNPROVEN"}
                    or terminal["attemptId"] != run["attemptId"]
                    or receipt["verificationAttemptId"] != run["attemptId"]
                    or receipt["verificationAdmissionSha256"] != run["admissionSha256"]
                    or terminal["receiptSha256"] != canonical_digest(receipt)
                    or receipt["status"] != {"OBSERVED": "CURRENT_WORKSPACE_PROFILE_OBSERVED",
                                             "UNPROVEN": "WORKSPACE_VERIFICATION_UNPROVEN"}[terminal["state"]]):
                raise ValueError("invalid terminal binding")
            return {**result, "status": receipt["status"], "receiptSha256": terminal["receiptSha256"]}
        if (journal / "cancelling.json").exists():
            result["status"] = "WORKSPACE_ATTEMPT_CANCELLING_UNCONFIRMED"
        return result
    except (KeyError, OSError, TypeError, ValueError):
        return {"status": "WORKSPACE_ATTEMPT_UNPROVEN"}


def workspace_verification_profile(plan, commands, install):
    return {"commands": commands, "checkBindings": [], "materialize": install,
            "obligations": plan["obligationPlanDigest"]}


def workspace_verification_admission(project, source_key, plan, profile):
    return {"sourceSnapshotKey": source_key, "workspacePathDigest": canonical_digest(str(project)),
            "resolverIdentitySha256": plan["resolverIdentitySha256"], "toolchain": plan["resolverToolchain"],
            "lockSha256": plan["approvedLockSha256"], "baselineLockSha256": plan["baselineLockSha256"],
            "registrySha256": plan["registrySha256"], "profileSha256": canonical_digest(profile),
            "installAuthorized": profile["materialize"], "lifecycleScriptsAuthorized": False}


def run_workspace_attempt(output, project, *, source_key, plan, commands, install, verify):
    output, journal = _paths(output)
    if output.is_relative_to(project) or project.is_relative_to(journal) or output.exists():
        raise ValueError("WORKSPACE_ATTEMPT_OUTPUT_UNAVAILABLE")
    # Directory creation is the single-writer admission lock. An interrupted
    # attempt stays reserved even if no receipt reached disk.
    journal.mkdir()
    profile = workspace_verification_profile(plan, commands, install)
    admission = workspace_verification_admission(project, source_key, plan, profile)
    run = {"state": "RUNNING", "attemptId": uuid.uuid4().hex, "admission": admission,
           "admissionSha256": canonical_digest(admission)}
    _publish(journal / "running.json", run)
    try:
        receipt = verify(admission["profileSha256"])
        expected_install = ("FRESH_COLD_FROZEN_INSTALL_OBSERVED" if install
                            else "EXISTING_TREE_PHYSICALLY_OBSERVED")
        operations = receipt.get("ownerOperationObservations", [])
        expected_operations = ([("strict-frozen-install", None)] if install else [])
        expected_operations += [("project-command", index) for index in range(1, len(commands) + 1)]
        if (not workspace_profile_complete(profile, source_key, receipt, plan, project)
                or receipt["materializedSourceOrigin"]["toolchain"] != admission["toolchain"]
                or receipt["installation"]["status"] != expected_install
                or receipt["installation"]["scriptsEnabled"] is not False
                or [(row["operation"], row["commandIndex"]) for row in operations] != expected_operations
                or any(row["supervision"] != "guaranteed-tree" or row["descendantsRemaining"] != 0
                       or row["returnCode"] != 0 or row["timedOut"] or row["cancelled"] for row in operations)):
            raise ValueError("WORKSPACE_VERIFIER_OBSERVATION_MISMATCH")
        state = "OBSERVED"
    except KeyboardInterrupt:
        _publish(journal / "cancelling.json", {"state": "CANCELLING", "attemptId": run["attemptId"]})
        raise
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        message = str(exc)
        receipt = {"status": "WORKSPACE_VERIFICATION_UNPROVEN",
                   "error": message if re.fullmatch("[A-Z_]+", message) else type(exc).__name__,
                   "operations": getattr(exc, "operation_observations", ())}
        state = "UNPROVEN"
    receipt.update(verificationAttemptId=run["attemptId"], verificationAdmissionSha256=run["admissionSha256"])
    _publish(output, receipt)
    _publish(journal / "terminal.json", {"state": state, "attemptId": run["attemptId"],
                                       "receiptSha256": canonical_digest(receipt)})
    return receipt
