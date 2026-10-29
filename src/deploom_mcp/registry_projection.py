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
_SAFE_VERSION = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]{1,32})?(?:\+[0-9A-Za-z.-]{1,24})?$")
_SAFE_HOST = re.compile(r"^[A-Za-z0-9.:_-]{1,253}$")
_SAFE_SEGMENT = re.compile(r"^[a-zA-Z0-9._~@+-]{1,48}$")
_SECRET_NAME = re.compile(r"(?i)token|api[-_]?key|secret|password|bearer|authorization|credential")
_LONG_HEX = re.compile(r"(?i)^[0-9a-f]{24,}$")
# Only known descriptive prefix labels may appear in the full Blueprint.
# Short, innocent-looking tenant identifiers can be actual bearer credentials.
_PUBLIC_PATH_LABELS = frozenset({
    "repository", "repositories", "registry", "npm", "npm-group", "npm-hosted",
    "public", "private", "api", "v1", "v2", "artifactory", "packages",
})
# Never emit a possibly sensitive key from fabricated physicalProbe metadata.
_PROBE_STATUSES = frozenset({
    "available", "missing-artifact", "empty-artifact", "foreign-registry-redirect",
    "planning-only", "current-installed", "missing-tarball-url", "foreign-registry-url",
    "redirect-limit-exceeded", "unknown",
})


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
    """Never export raw nonstandard registry path segments or short secrets."""
    if not isinstance(value, str) or not value.startswith("/"):
        return None
    result = []
    for segment in value.strip("/").split("/"):
        if not segment:
            continue
        decoded = unquote(segment)
        if decoded.lower() in _PUBLIC_PATH_LABELS and _SAFE_SEGMENT.fullmatch(decoded):
            result.append(decoded.lower())
        else:
            result.append("[redacted]")
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
    counts = {name: _count(raw.get(name)) for name in
              ("examinedVersions", "rejectedVersions", "noInstallableArtifactRows")}
    reasons = _counter_map(raw.get("rejectionCounts"), REJECTION_CODES)
    if (any(value is None for value in counts.values())
        or counts["rejectedVersions"] > counts["examinedVersions"]
        or not isinstance(raw.get("rejectionCounts"), Mapping)
        or len(raw["rejectionCounts"]) != len(reasons)
        or sum(reasons.values()) != counts["rejectedVersions"]):
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
    # A malformed/altered Core plan must not assert a false NO_INSTALLABLE.
    required = ("examined", "accepted", "rejected", "upgradeCandidates",
                "acceptedUpgradeCandidates", "rejectedUpgradeCandidates")
    numbers = {k: _count(raw.get(k)) for k in required}
    reasons = _counter_map(raw.get("rejectionCounts"), REJECTION_CODES)
    if (any(v is None for v in numbers.values())
        or numbers["accepted"] + numbers["rejected"] != numbers["examined"]
        or numbers["acceptedUpgradeCandidates"] + numbers["rejectedUpgradeCandidates"] != numbers["upgradeCandidates"]
        or numbers["upgradeCandidates"] > numbers["examined"]
        or numbers["acceptedUpgradeCandidates"] > numbers["accepted"]
        or numbers["rejectedUpgradeCandidates"] > numbers["rejected"]
        or not isinstance(raw.get("rejectionCounts"), Mapping)
        or len(raw["rejectionCounts"]) != len(reasons)
        or sum(reasons.values()) != numbers["rejected"]
        or type(raw.get("allUpgradeCandidatesRejected")) is not bool
        or raw["allUpgradeCandidatesRejected"] != bool(
            numbers["upgradeCandidates"] > 0 and numbers["acceptedUpgradeCandidates"] == 0
            and numbers["rejectedUpgradeCandidates"] == numbers["upgradeCandidates"])):
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
        if (code in REJECTION_CODES and isinstance(version, str) and len(version) <= 64
            and _SAFE_VERSION.fullmatch(version) and not _SECRET_NAME.search(version)):
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
    physical_invalid = False
    if physical is not None:
        if not isinstance(physical, Mapping):
            physical_invalid = True
        else:
            observed = _count(physical.get("observed"))
            unprobed = physical.get("unprobed")
            statuses_raw = physical.get("statusCounts", {})
            failures_raw = physical.get("failureCounts", {})
            statuses = _counter_map(statuses_raw, _PROBE_STATUSES)
            failures = _counter_map(failures_raw, REJECTION_CODES)
            # A partial/forged status map cannot claim that real probes
            # succeeded, nor may a failure count contradict actual statuses.
            expected_failures = {
                "HTTP_ARTIFACT_UNAVAILABLE": statuses.get("missing-artifact", 0)
                    + statuses.get("empty-artifact", 0),
                "FOREIGN_TARBALL_ORIGIN": statuses.get("foreign-registry-redirect", 0),
            }
            expected_unprobed = not any(
                k not in {"planning-only", "current-installed"} for k in statuses
            )
            if (observed is None or type(unprobed) is not bool
                or not isinstance(statuses_raw, Mapping)
                or not isinstance(failures_raw, Mapping)
                or sum(statuses.values()) != observed
                or unprobed != expected_unprobed
                or any(failures.get(code, 0) != expected for code, expected in expected_failures.items())
                or any(code not in expected_failures for code in failures)):
                physical_invalid = True
            else:
                projected["physicalProbe"] = {
                    "observed": observed, "unprobed": unprobed,
                    "failureCounts": failures, "statusCounts": statuses,
                }
    # Capture clipped evidence honestly, without ever logging the discarded values.
    reason_counts = raw.get("rejectionCounts")
    physical_failure = physical.get("failureCounts") if isinstance(physical, Mapping) else None
    physical_status = physical.get("statusCounts") if isinstance(physical, Mapping) else None
    if (raw.get("projectionTruncated") is True or physical_invalid
        or isinstance(samples, list) and len(samples) > len(examples)
        or isinstance(origins, list) and len(origins) > len(projected["rejectedArtifactOrigins"])
        or isinstance(reason_counts, dict) and len(reason_counts) != len(projected["rejectionCounts"])
        or isinstance(physical_failure, dict) and len(physical_failure) != len(projected.get("physicalProbe", {}).get("failureCounts", {}))
        or isinstance(physical_status, dict) and len(physical_status) != len(projected.get("physicalProbe", {}).get("statusCounts", {}))):
        projected["projectionTruncated"] = True
    return projected


def project_no_target_code(value: Any, draft_status: str,
                           diagnostic: Mapping[str, Any] | None = None) -> str | None:
    """The typed NO_TARGET assertion needs internally consistent evidence."""
    if not isinstance(value, str) or value not in NO_TARGET_CODES:
        return None
    if draft_status == "candidate-search-truncated":
        return "CANDIDATE_CAP" if value == "CANDIDATE_CAP" else None
    if draft_status != "no-target" or value == "CANDIDATE_CAP":
        return None
    if not isinstance(diagnostic, Mapping):
        return None
    total = diagnostic.get("upgradeCandidates")
    if value == "NO_INSTALLABLE_ARTIFACT":
        if not (diagnostic.get("allUpgradeCandidatesRejected") is True and
                type(total) is int and total > 0 and
                diagnostic.get("acceptedUpgradeCandidates") == 0 and
                diagnostic.get("rejectedUpgradeCandidates") == total):
            return None
    elif value == "NO_PUBLISHED_UPGRADE_CANDIDATE":
        if total != 0:
            return None
    elif value == "UNRESOLVED_TARGET_CONSTRAINT_OR_POLICY":
        if not (type(total) is int and total > 0 and
                diagnostic.get("allUpgradeCandidatesRejected") is False):
            return None
    return value


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
