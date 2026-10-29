"""Safe optional projection of Core's registry candidate diagnostics.

This is evidence rendering, not registry policy, an artifact verifier or a solver.
Never echo a Core-supplied tarball URL, credential, or arbitrary source string.
Absent diagnostics in the pinned legacy Core stay *absent*, not zero/clean.
"""
from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

REJECTION_CODES = frozenset({
    "FOREIGN_TARBALL_ORIGIN", "BAD_REGISTRY_PATH",
    "MISSING_DIST_TARBALL", "HTTP_ARTIFACT_UNAVAILABLE",
})
NO_TARGET_CODES = frozenset({
    "NO_INSTALLABLE_ARTIFACT", "NO_PUBLISHED_UPGRADE_CANDIDATE",
    "UNRESOLVED_TARGET_CONSTRAINT_OR_POLICY", "CANDIDATE_CAP",
})
CORE_AUTHORITY = "STRUCTURAL_REGISTRY_EVIDENCE_ONLY"
MAX_EXAMPLES = 4
MAX_COUNTER = 2_147_483_647
_SAFE_STATUS = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_SAFE_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")
_SAFE_HOST = re.compile(r"^[A-Za-z0-9.:_-]{1,253}$")
_SAFE_SEGMENT = re.compile(r"^[a-zA-Z0-9._~@+-]{1,48}$")
_SECRET_NAME = re.compile(r"(?i)token|api[-_]?key|secret|password|bearer|authorization|credential")
_LONG_HEX = re.compile(r"(?i)^[0-9a-f]{24,}$")


def _count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= MAX_COUNTER else None


def _counter_map(value: Any, allowed: frozenset[str] | None = None) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    out = {}
    for k in sorted(k for k in value if isinstance(k, str)):
        if not isinstance(k, str) or (k not in allowed if allowed is not None else not _SAFE_STATUS.fullmatch(k)):
            continue
        n = _count(value[k])
        if n is not None and len(out) < 16:
            out[k] = n
    return out


def _origin(value: Any) -> str | None:
    """Rebuild an origin, so userinfo, query, fragments and paths cannot leak."""
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        port = parts.port
        if parts.scheme not in ("http", "https") or not _SAFE_HOST.fullmatch(host):
            return None
        if port is not None and not 1 <= port <= 65535:
            return None
        host = f"[{host}]" if ":" in host else host
        default_port = 80 if parts.scheme == "http" else 443
        result = f"{parts.scheme}://{host.lower()}" + (f":{port}" if port and port != default_port else "")
        return result if len(result) <= 265 else None
    except ValueError:
        return None


def _path(value: Any) -> str | None:
    """Keep only safe public registry path structure, never credential segments."""
    if not isinstance(value, str) or not value.startswith("/"):
        return None
    result = []
    for segment in value.strip("/").split("/"):
        if not segment:
            continue
        decoded = unquote(segment)
        suspicious = (not _SAFE_SEGMENT.fullmatch(decoded)
                      or _SECRET_NAME.search(decoded) is not None
                      or _LONG_HEX.fullmatch(decoded) is not None)
        result.append("[redacted]" if suspicious else decoded)
        if len(result) >= 12:
            break
    output = "/" + "/".join(result)
    return output[:160] if len(output) <= 160 else output[:148] + "[redacted]"


def _identity(value: Any) -> dict[str, str | None]:
    if not isinstance(value, Mapping):
        return {"origin": None, "pathPrefix": None}
    return {"origin": _origin(value.get("origin")), "pathPrefix": _path(value.get("pathPrefix"))}


def project_registry_aggregate(raw: Any) -> dict[str, Any] | None:
    """The aggregate is always tiny and contains only known typed counters."""
    if not isinstance(raw, Mapping) or type(raw.get("schemaVersion")) is not int or raw["schemaVersion"] != 1 or raw.get("authority") != CORE_AUTHORITY:
        return None
    result: dict[str, Any] = {"schemaVersion": 1, "authority": CORE_AUTHORITY}
    for name in ("examinedVersions", "rejectedVersions", "noInstallableArtifactRows"):
        n = _count(raw.get(name))
        if n is not None:
            result[name] = n
    result["rejectionCounts"] = _counter_map(raw.get("rejectionCounts"), REJECTION_CODES)
    counts = raw.get("rejectionCounts")
    if isinstance(counts, dict) and len(counts) != len(result["rejectionCounts"]):
        result["projectionTruncated"] = True
    return result


def project_registry_candidate(raw: Any) -> dict[str, Any] | None:
    """Render a bounded whitelist; everything else stays in Core's raw plan."""
    if not isinstance(raw, Mapping) or type(raw.get("schemaVersion")) is not int or raw["schemaVersion"] != 1 or raw.get("authority") != CORE_AUTHORITY:
        return None
    projected: dict[str, Any] = {"schemaVersion": 1, "authority": CORE_AUTHORITY}
    for field in ("examined", "accepted", "rejected", "upgradeCandidates",
                  "acceptedUpgradeCandidates", "rejectedUpgradeCandidates"):
        n = _count(raw.get(field))
        if n is not None:
            projected[field] = n
    if type(raw.get("allUpgradeCandidatesRejected")) is bool:
        projected["allUpgradeCandidatesRejected"] = raw["allUpgradeCandidatesRejected"]
    projected["rejectionCounts"] = _counter_map(raw.get("rejectionCounts"), REJECTION_CODES)
    for field in ("configuredRegistry", "metadataRequestBase"):
        if field in raw:
            projected[field] = _identity(raw[field])
    if raw.get("metadataRequestBaseAuthority") == "CONFIGURATION_NOT_HTTP_TRACE":
        projected["metadataRequestBaseAuthority"] = "CONFIGURATION_NOT_HTTP_TRACE"
    samples = raw.get("rejectionSamples")
    items = samples if isinstance(samples, list) else []
    examples = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code, version = item.get("code"), item.get("version")
        if code in REJECTION_CODES and isinstance(version, str) and _SAFE_VERSION.fullmatch(version):
            examples.append({"code": code, "version": version})
            if len(examples) >= MAX_EXAMPLES:
                break
    projected["rejectionSamples"] = examples
    origins = raw.get("rejectedArtifactOrigins")
    values = origins if isinstance(origins, list) else []
    projected["rejectedArtifactOrigins"] = []
    for value in values:
        identity = _origin(value)
        if identity and identity not in projected["rejectedArtifactOrigins"]:
            projected["rejectedArtifactOrigins"].append(identity)
            if len(projected["rejectedArtifactOrigins"]) >= MAX_EXAMPLES:
                break
    physical = raw.get("physicalProbe")
    if isinstance(physical, Mapping):
        p: dict[str, Any] = {}
        n = _count(physical.get("observed"))
        if n is not None:
            p["observed"] = n
        if type(physical.get("unprobed")) is bool:
            p["unprobed"] = physical["unprobed"]
        p["failureCounts"] = _counter_map(physical.get("failureCounts"), REJECTION_CODES)
        p["statusCounts"] = _counter_map(physical.get("statusCounts"))
        projected["physicalProbe"] = p
    # Capture clipped evidence honestly, without ever logging the discarded values.
    reason_counts = raw.get("rejectionCounts")
    physical_failure = physical.get("failureCounts") if isinstance(physical, Mapping) else None
    physical_status = physical.get("statusCounts") if isinstance(physical, Mapping) else None
    if (raw.get("projectionTruncated") is True
        or isinstance(samples, list) and len(samples) > len(examples)
        or isinstance(origins, list) and len(origins) > len(projected["rejectedArtifactOrigins"])
        or isinstance(reason_counts, dict) and len(reason_counts) != len(projected["rejectionCounts"])
        or isinstance(physical_failure, dict) and len(physical_failure) != len(projected["physicalProbe"]["failureCounts"])
        or isinstance(physical_status, dict) and len(physical_status) != len(projected["physicalProbe"]["statusCounts"])):
        projected["projectionTruncated"] = True
    return projected


def project_no_target_code(value: Any, draft_status: str) -> str | None:
    if (isinstance(value, str) and value in NO_TARGET_CODES
        and ((draft_status == "no-target" and value != "CANDIDATE_CAP")
             or (draft_status == "candidate-search-truncated" and value == "CANDIDATE_CAP"))):
        return value
    return None


def compact_registry_candidate(full: Any, ref: str) -> dict[str, Any] | None:
    """Small agent-facing summary: no hostnames, URL paths, or sample versions."""
    clean = project_registry_candidate(full)
    if clean is None:
        return None
    fields = ("schemaVersion", "authority", "examined", "accepted", "rejected",
              "upgradeCandidates", "acceptedUpgradeCandidates", "rejectedUpgradeCandidates",
              "allUpgradeCandidatesRejected", "rejectionCounts", "projectionTruncated")
    return {**{name: clean[name] for name in fields if name in clean},
            "evidenceRef": ref}
