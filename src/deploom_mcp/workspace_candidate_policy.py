"""Assess an explicit policy on the exact captured candidate dependency graph."""
from __future__ import annotations

from collections import defaultdict
from contextlib import redirect_stdout
import json
from pathlib import PurePosixPath
import sys

from deploom_mcp.goal_contract import canonical_digest
from deploom_mcp.workspace_policy_request import validate_workspace_policy, workspace_health_policy

_NPM_SEVERITIES = {"critical": "C", "high": "H", "moderate": "M", "low": "L", "info": None}


def _native_names(output, packages):
    """Preserve each reported severity per package, including direct via records."""
    native = json.loads(output)
    if native.get("auditReportVersion") != 2 or not isinstance(native.get("vulnerabilities"), dict):
        return None
    result = {label: set() for label in "CHMLU"}
    for name, record in native["vulnerabilities"].items():
        nodes = record.get("nodes")
        if (not nodes or any(slot not in packages or packages[slot].get("link")
                             or slot.rsplit("node_modules/", 1)[-1] != name for slot in nodes)):
            return None
        levels = [record.get("severity")]
        levels.extend(row.get("severity") for row in record["via"] if isinstance(row, dict))
        for level in levels:
            label = _NPM_SEVERITIES.get(level, "U") if isinstance(level, str) else "U"
            if label is not None:
                result[label].add(name)
    return {key: len(names) for key, names in result.items()}


def audit_candidate_policy(inputs, manifests, registry, output, options, *, seconds, use_system_proxy):
    from dependency_live_roadmap_generator import (
        DeadlineClock, DraftBudgetExceeded, LiveDataClient, RegistryInfrastructureError, required_ratio_count,
    )
    from resolution_inventory import npm_lock_resolved_instance
    from workspace_health_evidence import collect_workspace_health_facts
    from workspace_npm_lock_delta import INSTALL_SECTIONS

    options = validate_workspace_policy(options)
    policy = workspace_health_policy(options)
    packages = json.loads(inputs["package-lock.json"].decode("utf-8-sig"))["packages"]
    versions = defaultdict(set)
    rows = []
    internal_links = 0
    for path in manifests:
        owner = PurePosixPath(path).parent.as_posix()
        manifest = json.loads(inputs[path].decode("utf-8-sig"))
        for section in INSTALL_SECTIONS:
            for name in manifest.get(section, {}):
                instance = npm_lock_resolved_instance(packages, name, "" if owner == "." else owner)
                if instance is not None and packages[instance[0]].get("link"):
                    internal_links += 1
                    continue
                slot, version = instance if instance is not None else (None, None)
                rows.append({"owner": owner, "section": section, "package": name, "lockPath": slot, "version": version})
                if version is not None:
                    versions[name].add(version)
    rows.sort(key=lambda row: (row["owner"], row["section"], row["package"]))

    facts, source = {}, None
    if seconds > 0 and versions:
        client = LiveDataClient(registry, timeout=5, batch_size=16, sleep_sec=0, use_system_proxy=use_system_proxy)
        client.draft = True
        client.set_deadline(DeadlineClock(seconds))
        try:
            packuments = {}
            with redirect_stdout(sys.stderr):
                for name in sorted(versions):
                    try:
                        packuments[name] = client.fetch_npm_metadata(name)
                    except RegistryInfrastructureError:
                        continue
                facts, source = collect_workspace_health_facts(
                    client, packuments, versions, lag_months=options["lagPolicyMonths"], osv_allowed_names=frozenset(),
                )
        except DraftBudgetExceeded:
            pass
        finally:
            client.session.close()
    for row in rows:
        fact = facts.get((row["package"], row["version"]))
        row["status"] = ("ok" if fact.lag_ok is True else "lagging") if fact and fact.lag_ok is not None else "unknown"
    lag_confirmed = sum(row["status"] == "ok" for row in rows)
    lag_unknown = sum(row["status"] == "unknown" for row in rows)
    total = len(rows)
    needed = required_ratio_count(total, policy.ratio())
    limits = policy.limits()
    counts = _native_names(output, packages)
    exceeded = []
    if counts is not None:
        for level, cap in limits.items():
            if level == "U":
                continue
            observed = counts["M"] + counts["L"] if level == "M+L" else counts[level]
            if observed > cap:
                exceeded.append(level)
    if exceeded or lag_confirmed + lag_unknown < needed:
        verdict = "FAIL"
    elif not total or lag_unknown or counts is None or counts["U"]:
        verdict = "UNKNOWN"
    else:
        verdict = "PASS"
    return {
        "requested": True, "status": "COMPLETE" if total and lag_unknown == 0 else "INCOMPLETE",
        "complete": total > 0 and lag_unknown == 0, "basis": "EXACT_OWNER_RESOLVED_CANDIDATE_LOCK",
        "months": options["lagPolicyMonths"], "totalDeclarations": total, "unknownDeclarations": lag_unknown,
        "okDeclarations": lag_confirmed, "requiredDeclarations": needed, "internalLinkDeclarations": internal_links,
        "rows": rows, "metadataSnapshotSha256": source["snapshotSha256"] if source else None,
        "policyAttainment": verdict,
    }, {
        "verdict": verdict, "policy": options, "policySha256": canonical_digest(options),
        "securityUnit": "NATIVE_NPM_UNIQUE_PACKAGE_NAMES_PER_SEVERITY",
        "knownBySeverity": counts, "severityLimits": limits, "exceededSeverityCaps": exceeded,
        "lagDenominator": total, "lagRequired": needed, "lagConfirmed": lag_confirmed, "lagUnknown": lag_unknown,
    }
