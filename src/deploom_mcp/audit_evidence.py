"""Strict audit-response normalization. No command execution or policy solver.

Counts refer to different units. In particular a Yarn inventory node is not a
physical installation path and a successful command is not a clean audit.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

SEVERITIES = ("critical", "high", "moderate", "low", "info", "unknown")


class AuditError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def severity(value: Any) -> str:
    return value if isinstance(value, str) and value in SEVERITIES else "unknown"


def normalize_audit(stdout: str, exit_code: int, engine: str, *,
                    confirmed_empty_success: bool = False) -> dict[str, Any]:
    """Accept npm audit v1/v2, Berry raw reports and Berry tree NDJSON.

    JSON-shaped errors, arbitrary {}, mixed diagnostics, unrecognized records
    and unexpected exits are never converted into a complete zero-finding scan.
    """
    if exit_code not in (0, 1):
        raise AuditError("audit-command-failed", f"audit command exited with {exit_code}")
    if not stdout.strip():
        # Yarn 4.x currently prints *no JSON lines* when the recursive audit
        # completes without advisories. Trust this shape only when the worker
        # explicitly confirmed a supported native Yarn version, exit=0 and
        # empty stderr, after executing the exact audit command. A bare empty
        # response can never make npm or an unverified Berry run appear clean.
        if engine == "yarn-berry" and confirmed_empty_success and exit_code == 0:
            result = normalize_audit(
                '{"type":"info","name":0,"displayName":"YN0000","data":"No audit suggestions"}',
                exit_code, engine,
            )
            result["completionEvidence"] = "YARN4_NATIVE_EXIT_ZERO_NO_JSON"
            return result
        raise AuditError("audit-output-invalid", "audit returned no JSON")
    try:
        whole = json.loads(stdout)
        records = [whole]
    except json.JSONDecodeError:
        try:
            records = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise AuditError("audit-output-invalid", "audit returned malformed JSON/NDJSON") from exc
    advisories: dict[str, dict[str, Any]] = {}
    packages: dict[str, dict[str, Any]] = {}
    recognized = False
    clean_signal = False
    reported_nodes: dict[str, int] | None = None

    def add(package: str, value: dict[str, Any], *, versions: list | None = None,
            paths: list | None = None) -> None:
        if not isinstance(package, str) or not package:
            raise AuditError("audit-output-invalid", "advisory is missing its package name")
        identifier = value.get("source") or value.get("id") or value.get("url")
        if identifier is None or not isinstance(identifier, (str, int)):
            raise AuditError("audit-output-invalid", "advisory has no stable ID or URL")
        key = str(identifier)
        level = severity(value.get("severity"))
        entry = advisories.setdefault(key, {
            "id": key, "severity": level, "title": str(value.get("title") or ""),
            "url": str(value.get("url") or ""), "packages": [],
            "vulnerableRange": str(value.get("range") or value.get("vulnerable_versions") or ""),
        })
        if SEVERITIES.index(level) < SEVERITIES.index(entry["severity"]):
            entry["severity"] = level
        if package not in entry["packages"]:
            entry["packages"].append(package)
        record = packages.setdefault(package, {"name": package, "severity": level,
                                               "advisoryIds": [], "nodes": [], "versions": []})
        if SEVERITIES.index(level) < SEVERITIES.index(record["severity"]):
            record["severity"] = level
        if key not in record["advisoryIds"]:
            record["advisoryIds"].append(key)
        for field, items in (("nodes", paths), ("versions", versions)):
            if items is not None:
                if not isinstance(items, list) or any(not isinstance(x, str) for x in items):
                    raise AuditError("audit-output-invalid", f"invalid {field} in audit")
                record[field] = sorted(set(record[field]) | set(items))

    for record in records:
        if not isinstance(record, dict) or record.get("error"):
            raise AuditError("audit-output-invalid", "audit returned an error or a non-object")
        if "vulnerabilities" in record and isinstance(record["vulnerabilities"], dict):
            if not isinstance(record.get("metadata"), dict) or not isinstance(record["metadata"].get("vulnerabilities"), dict):
                raise AuditError("audit-output-invalid", "npm audit is missing completion counters")
            recognized = True
            for name, value in record["vulnerabilities"].items():
                if not isinstance(value, dict) or not isinstance(value.get("via"), list):
                    raise AuditError("audit-output-invalid", "invalid npm vulnerability record")
                nodes = value.get("nodes", [])
                if not isinstance(nodes, list) or any(not isinstance(n, str) for n in nodes):
                    raise AuditError("audit-output-invalid", "invalid npm node paths")
                packages.setdefault(name, {"name": name, "severity": severity(value.get("severity")),
                                           "nodes": nodes, "versions": [], "advisoryIds": []})
                packages[name]["viaPackages"] = [v for v in value["via"] if isinstance(v, str)]
                packages[name]["fixAvailable"] = value.get("fixAvailable")
                for via in value["via"]:
                    if isinstance(via, dict):
                        add(name, via, paths=nodes)
                    elif not isinstance(via, str):
                        raise AuditError("audit-output-invalid", "invalid npm advisory edge")
            if not record["vulnerabilities"]:
                clean_signal = True
        elif isinstance(record.get("advisories"), dict):
            recognized = True
            for value in record["advisories"].values():
                if not isinstance(value, dict):
                    raise AuditError("audit-output-invalid", "invalid legacy advisory record")
                paths = []
                for finding in value.get("findings", []):
                    if isinstance(finding, dict):
                        paths.extend(finding.get("paths", []))
                add(value.get("module_name") or value.get("name"), value, paths=paths)
            clean_signal |= not bool(record["advisories"])
        elif engine == "yarn-berry" and isinstance(record.get("children"), dict):
            # Yarn 3/4 treeUtils.emitTree(..., {json:true}). One line per advisory.
            child = record["children"]
            if not all(key in child for key in ("ID", "Severity", "Issue")):
                raise AuditError("audit-output-invalid", "incomplete Yarn audit tree")
            recognized = True
            add(record.get("value"), {"id": child["ID"], "title": child["Issue"],
                "severity": child["Severity"], "url": child.get("URL", ""),
                "range": child.get("Vulnerable Versions", "")},
                versions=child.get("Tree Versions", []))
        elif engine == "yarn-berry" and record.get("type") == "info" and record.get("displayName") == "YN0000" and record.get("data") == "No audit suggestions":
            recognized = clean_signal = True
        elif engine == "yarn-berry" and record and all(isinstance(v, list) for v in record.values()):
            # Registry bulk response in older Berry releases.
            recognized = True
            for name, values in record.items():
                for value in values:
                    if not isinstance(value, dict):
                        raise AuditError("audit-output-invalid", "invalid Yarn bulk advisory")
                    add(name, value, versions=value.get("versions"))
            clean_signal |= all(not v for v in record.values())
        elif engine == "yarn-berry" and record == {} and exit_code == 0:
            # Older Berry versions print an empty raw registry response.
            recognized = clean_signal = True
        else:
            raise AuditError("audit-output-invalid", "unrecognized audit response; no clean result inferred")
        metadata = record.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise AuditError("audit-output-invalid", "audit metadata is not an object")
        counts = metadata.get("vulnerabilities")
        if counts is not None:
            if not isinstance(counts, dict) or any(type(v) is not int or v < 0 for v in counts.values()):
                raise AuditError("audit-output-invalid", "invalid audit counters")
            reported_nodes = {k: counts.get(k, 0) for k in SEVERITIES}
    if not recognized or (not packages and not clean_signal):
        raise AuditError("audit-output-invalid", "no recognized completed audit result")
    if exit_code == 1 and not packages:
        raise AuditError("audit-output-invalid", "nonzero audit exit without any findings")
    if not packages and reported_nodes and sum(reported_nodes.values()):
        raise AuditError("audit-output-invalid", "audit counters contradict an empty finding list")
    ac = Counter(a["severity"] for a in advisories.values())
    pc = Counter(p["severity"] for p in packages.values())
    for record in advisories.values():
        record["packages"].sort()
    return {
        "complete": True, "verdict": "FINDINGS" if packages else "NO_KNOWN_FINDINGS",
        "counts": {"uniqueAdvisories": len(advisories), "affectedPackages": len(packages),
                   "advisoriesBySeverity": {k: ac[k] for k in SEVERITIES},
                   "packagesBySeverity": {k: pc[k] for k in SEVERITIES},
                   "reportedNodesBySeverity": reported_nodes},
        "advisories": sorted(advisories.values(), key=lambda a: (SEVERITIES.index(a["severity"]), a["id"])),
        "packages": sorted(packages.values(), key=lambda p: (SEVERITIES.index(p["severity"]), p["name"])),
        "scope": "LOCKFILE_RECORDED_REGISTRY_PACKAGES", "installedTreeVerified": False,
        "exploitability": "NOT_ASSESSED", "policyAttainment": "NOT_ASSESSED",
    }
