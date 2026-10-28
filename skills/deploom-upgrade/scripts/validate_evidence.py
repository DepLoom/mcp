#!/usr/bin/env python3
"""Check evidence-record consistency; never execute or attest a project check.

This stdlib-only helper does not run commands, read a project or contact services.
It rejects unsupported PASS claims, stale final-input claims, miscounted changes
and missing decision authority. Real logs, input hashes and observations must
still be supplied by the caller; a well-formed record is not independent proof.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any

CHECK_STATUSES = {"PASSED", "FAILED", "INCONCLUSIVE", "NOT_RUN", "NOT_AVAILABLE"}
KINDS = {"install", "checks", "runtime", "policy"}
INPUTS = ("manifests", "lockfile", "environment", "source")
OUTCOMES = {"PLAN_READY", "PASSED", "PARTIAL", "BLOCKED", "FAILED", "INFRA_ERROR", "CANCELLED"}


def _hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def change_counts(changes: list[dict[str, Any]]) -> dict[str, int]:
    updated, added, removed = set(), set(), set()
    for change in changes:
        before, after, name = change.get("from"), change.get("to"), change.get("package")
        if before == after or not isinstance(name, str):
            continue
        if before is None:
            added.add(name)
        elif after is None:
            removed.add(name)
        else:
            updated.add(name)
    return {
        "changedDeclarations": sum(c.get("from") != c.get("to") for c in changes),
        "updatedUniqueNames": len(updated),
        "addedUniqueNames": len(added),
        "removedUniqueNames": len(removed),
    }


def validate_report(report: Any) -> list[str]:
    """Return actionable consistency errors without modifying the input record."""
    if not isinstance(report, dict):
        return ["report must be an object"]
    errors: list[str] = []
    if report.get("schemaVersion") != 2 or report.get("workflow") != "deploom-upgrade":
        errors.append("schemaVersion=2 and workflow=deploom-upgrade are required")
    mode = report.get("mode")
    if mode not in ("PLAN_ONLY", "EXECUTE"):
        errors.append("mode must be PLAN_ONLY or EXECUTE")
    if report.get("targetMode") not in ("pinned", "advisory"):
        errors.append("targetMode must be pinned or advisory")
    if report.get("objective") not in ("minimum-change-to-policy", "latest-compatible-in-approved-lines"):
        errors.append("objective must state policy-minimal or latest-compatible intent")
    if report.get("completion") not in ("COMPLETED", "IN_PROGRESS", "INTERRUPTED", "CANCELLED"):
        errors.append("completion must describe the session, separately from outcome")
    if not isinstance(report.get("outcome"), str) or report["outcome"] not in OUTCOMES:
        errors.append("invalid outcome")
    if report.get("targetLevel") not in ("yellow", "green"):
        errors.append("targetLevel must be yellow or green")

    final_inputs = report.get("finalInputs") or {}
    acceptance, results = report.get("acceptance"), report.get("results")
    verification = report.get("verification")
    changes, decisions = report.get("declarationChanges"), report.get("decisions")
    authorities = report.get("authorities")
    if not isinstance(final_inputs, dict):
        errors.append("finalInputs must be an object")
        final_inputs = {}
    if not isinstance(acceptance, dict) or not isinstance(results, dict):
        errors.append("acceptance and results must be objects with install/checks/runtime/policy keys")
        acceptance, results = {}, {}
    if not isinstance(verification, list) or any(not isinstance(v, dict) for v in verification):
        errors.append("verification must be an array of check records")
        verification = []
    if not isinstance(changes, list) or any(not isinstance(c, dict) for c in changes):
        errors.append("declarationChanges must be an array")
        changes = []
    if not isinstance(decisions, list) or any(not isinstance(d, dict) for d in decisions):
        errors.append("decisions must be an array")
        decisions = []
    if not isinstance(authorities, list) or any(not isinstance(a, dict) for a in authorities):
        errors.append("authorities must be an array")
        authorities = []
    authority_ids = {a.get("id") for a in authorities if isinstance(a.get("id"), str) and a.get("reference")}
    decision_ids: set[str] = set()
    decisions_by_id: dict[str, dict[str, Any]] = {}
    for i, decision in enumerate(decisions):
        label = f"decisions[{i}]"
        identity = decision.get("id")
        if not isinstance(identity, str) or not identity or identity in decision_ids:
            errors.append(f"{label}: unique id required")
        else:
            decision_ids.add(identity)
            decisions_by_id[identity] = decision
        if any(not isinstance(decision.get(k), str) or not decision[k] for k in ("package", "plannerStatus")):
            errors.append(f"{label}: package and original plannerStatus required")
        if decision.get("targetOrigin") not in ("planner", "manual"):
            errors.append(f"{label}: targetOrigin must be planner or manual")
        if decision.get("selectedTarget") is not None:
            if not isinstance(decision.get("selectedTarget"), str) or not decision["selectedTarget"]:
                errors.append(f"{label}: selectedTarget must be a version string or null")
            if not isinstance(decision.get("authorityRef"), str) or decision["authorityRef"] not in authority_ids:
                errors.append(f"{label}: selected target needs an existing authority reference")
            if not decision.get("evidence"):
                errors.append(f"{label}: selected target needs evidence references")
        if decision.get("targetOrigin") == "planner" and decision.get("selectedTarget") != decision.get("plannerTarget"):
            errors.append(f"{label}: manual alternative must not be labelled a planner target")
        if (report.get("targetMode") == "pinned" and decision.get("targetOrigin") == "manual"
                and decision.get("selectedTarget") is not None and decision.get("revisedTargetAuthorization") is not True):
            errors.append(f"{label}: pinned alternative needs explicit revised-target authorization")

    seen_declarations: set[tuple[str, str, str]] = set()
    for i, change in enumerate(changes):
        label = f"declarationChanges[{i}]"
        key = tuple(change.get(k) for k in ("file", "section", "package"))
        if not all(isinstance(k, str) and k for k in key):
            errors.append(f"{label}: file, section and package required")
            continue
        if key in seen_declarations:
            errors.append(f"{label}: duplicate declaration")
        seen_declarations.add(key)
        if change.get("from") == change.get("to"):
            errors.append(f"{label}: unchanged declaration is not an upgrade")
        for field in ("from", "to"):
            if change.get(field) is not None and (not isinstance(change[field], str) or not change[field]):
                errors.append(f"{label}: {field} must be a version/range string or null")
        identity = change.get("decisionId")
        if not isinstance(identity, str) or identity not in decision_ids:
            errors.append(f"{label}: decisionId must reference a recorded decision")
        else:
            decision = decisions_by_id[identity]
            if decision.get("package") != change["package"]:
                errors.append(f"{label}: decision must describe the same package")
            authority = decision.get("authorityRef")
            if not isinstance(authority, str) or authority not in authority_ids or not decision.get("evidence"):
                errors.append(f"{label}: changed declaration needs recorded authority and evidence")
            # Declarations may use a range around the selected exact version;
            # range satisfaction needs package-manager evidence, not this helper.
            if change.get("to") is None and decision.get("selectedTarget") is not None:
                errors.append(f"{label}: removal must have a removal decision (selectedTarget=null)")
            if change.get("to") is not None and (not isinstance(decision.get("selectedTarget"), str)
                                                  or not decision["selectedTarget"]):
                errors.append(f"{label}: changed version needs a selected target decision")
    if report.get("counts") != change_counts(changes):
        errors.append("counts must match changed declarations and unique names; exclude unchanged rows")

    latest: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
    for i, check in enumerate(verification):
        label = f"verification[{i}]"
        kind, name, status = check.get("kind"), check.get("check"), check.get("status")
        if not isinstance(kind, str) or kind not in KINDS or not isinstance(name, str) or not name:
            errors.append(f"{label}: valid kind and check name required")
            continue
        if not isinstance(status, str) or status not in CHECK_STATUSES:
            errors.append(f"{label}: invalid status")
        if status in ("PASSED", "FAILED", "INCONCLUSIVE"):
            started, finished = _time(check.get("startedAt")), _time(check.get("finishedAt"))
            if not started or not finished or finished < started:
                errors.append(f"{label}: observed start/end with timezones required")
            elif (kind, name) not in latest or finished >= latest[(kind, name)][0]:
                latest[(kind, name)] = (finished, check)
            if not check.get("command") or not check.get("log"):
                errors.append(f"{label}: executed check needs command and log")
        if status == "PASSED":
            if type(check.get("exitCode")) is not int or check["exitCode"] != 0:
                errors.append(f"{label}: PASSED requires observed exitCode=0")
            needed = INPUTS[:3] if kind == "install" else INPUTS
            inputs = check.get("inputs") or {}
            if not isinstance(inputs, dict) or not all(_hash(inputs.get(k)) for k in needed):
                errors.append(f"{label}: PASSED requires recorded input hashes")
            if kind == "policy" and check.get("scopeComplete") is not True:
                errors.append(f"{label}: policy PASS requires explicit full intended-scope coverage")
            if kind == "install" and check.get("installMode") not in ("clean", "frozen", "incremental"):
                errors.append(f"{label}: install PASS needs observed installMode")

    for kind in sorted(KINDS):
        required = acceptance.get(kind)
        if not isinstance(required, list) or any(not isinstance(n, str) or not n for n in required):
            errors.append(f"acceptance.{kind} must list required check names")
            required = []
        if not isinstance(results.get(kind), str) or results[kind] not in CHECK_STATUSES | {"NOT_VERIFIED"}:
            errors.append(f"results.{kind} must have an honest separate status")
        if results.get(kind) == "PASSED" or (report.get("outcome") == "PASSED" and required):
            if not required:
                errors.append(f"results.{kind}: cannot infer PASS without a required check")
            for name in required:
                entry = latest.get((kind, name))
                check = entry[1] if entry else {}
                inputs = check.get("inputs") or {}
                needed = INPUTS[:3] if kind == "install" else INPUTS
                if (check.get("status") != "PASSED" or not isinstance(inputs, dict)
                        or not all(_hash(final_inputs.get(k)) and inputs.get(k) == final_inputs[k] for k in needed)):
                    errors.append(f"{kind}/{name}: no PASSED evidence on final inputs")
                if kind == "install" and check.get("installMode") not in ("clean", "frozen"):
                    errors.append(f"{kind}/{name}: incremental install is not clean/frozen final-install evidence")
                if results.get(kind) != "PASSED":
                    errors.append(f"outcome PASSED requires results.{kind}=PASSED")
    if mode == "PLAN_ONLY":
        if changes or verification or report.get("outcome") == "PASSED":
            errors.append("PLAN_ONLY must not claim executed changes or migration PASSED")
    if report.get("outcome") == "PASSED":
        if report.get("completion") != "COMPLETED" or mode != "EXECUTE":
            errors.append("migration PASSED requires completed EXECUTE mode")
        if not acceptance.get("install"):
            errors.append("migration PASSED requires final clean-install acceptance")
        if report.get("targetLevel") in ("yellow", "green") and not acceptance.get("policy"):
            errors.append("yellow/green PASSED requires a final policy audit")
    deliverables = report.get("deliverables", [])
    if not isinstance(deliverables, list) or any(not isinstance(d, dict) for d in deliverables):
        errors.append("deliverables must be an array of indexed delivery records")
    else:
        for i, entry in enumerate(deliverables):
            if any(not isinstance(entry.get(k), str) or not entry[k] for k in ("path", "requiredBy")):
                errors.append(f"deliverables[{i}]: path and requirement reference are required")
            if entry.get("status") not in ("PRESENT", "MISSING", "NOT_VERIFIED"):
                errors.append(f"deliverables[{i}]: status must describe observed file delivery")
            if report.get("outcome") == "PASSED" and entry.get("status") != "PRESENT":
                errors.append(f"deliverables[{i}]: required delivery missing or not verified")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8-sig"))
        errors = validate_report(report)
    except (OSError, ValueError) as exc:
        print(f"evidence: {exc}", file=sys.stderr)
        return 2
    for error in errors:
        print(f"evidence: {error}", file=sys.stderr)
    if errors:
        return 1
    print("evidence: consistent record (execution and hashes are not independently attested)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
