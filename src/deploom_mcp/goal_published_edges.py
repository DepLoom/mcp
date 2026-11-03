"""Exact public npm edge observations, never instance-necessity or execution proof.

Only the existing Core exact-version HTTP primitive fetches metadata. Its planning
packument/durable caches are deliberately not used by this verification helper.
The supplier is trusted local code; MCP request JSON cannot provide one.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
import sys
import time
from urllib.parse import quote

from semantic_version import NpmSpec, Version
from deploom_mcp.distribution import resolve_core_dir
from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.goal_lock_scope import _packages, _resolve, validate_npm_lock_scope, _scope_packages

PUBLIC_REGISTRY = "https://registry.npmjs.org"
_NAME = re.compile(r"(?:@[a-z0-9][a-z0-9._-]{0,100}/)?[a-z0-9][a-z0-9._-]{0,100}")
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
_RANGE = re.compile(r"[0-9xX*<>=^~|. +\-]{1,200}")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sri(value):
    if not isinstance(value, str) or not value.startswith("sha512-"):
        return False
    try:
        return len(base64.b64decode(value[7:], validate=True)) == 64
    except ValueError:
        return False


def _out(status, reason, **fields):
    return {"status": status, "reason": reason, "fullAcceptance": False,
            "instanceNecessity": "UNKNOWN", "executionAuthority": "NONE", **fields}


class PublishedMetadataSupplier:
    """Bounded per-attempt memo of fresh exact HTTP reads; no persistent proof cache.

    An injected client is always labelled engineering control. It cannot be
    represented as a live official-registry observation by a constructor flag.
    """
    def __init__(self, *, deadline_utc, client=None, clock=time.time, ttl_seconds=60, max_requests=64,
                 use_system_proxy=False):
        self.clock = clock
        self.deadline = float(deadline_utc)
        self.ttl = min(60.0, max(0.0, float(ttl_seconds)))
        if not math.isfinite(self.deadline) or not 0 < self.deadline - clock() <= 3600:
            raise ValueError("INVALID_METADATA_ATTEMPT_DEADLINE")
        if type(use_system_proxy) is not bool:
            raise ValueError("INVALID_HOST_TRANSPORT_CONFIGURATION")
        self.engineering = client is not None
        if client is None:
            sys.path.insert(0, str(resolve_core_dir()))
            from dependency_live_roadmap_generator import DeadlineClock, LiveDataClient
            client = LiveDataClient(PUBLIC_REGISTRY, timeout=5, batch_size=1, sleep_sec=0,
                                    use_system_proxy=use_system_proxy, metadata_cache_dir=None)
            client.set_deadline(DeadlineClock(self.deadline - clock()))
        self.client = client
        self.max_requests = min(64, max(1, int(max_requests)))
        self.requests = 0
        self._cache = {}
        self._transport = None
        self._digests = {}

    def _context(self):
        if self.engineering:
            return "ENGINEERING_CONTROL_TRANSPORT"
        from registry_request_context import request_context
        return request_context(self.client.session, PUBLIC_REGISTRY, "published-ordinary-edge-v1")[0]

    def fetch(self, name, version, lock_record):
        if (not isinstance(name, str) or not _NAME.fullmatch(name) or not isinstance(version, str)
                or not _VERSION.fullmatch(version) or not isinstance(lock_record, dict)):
            return _out("UNKNOWN", "UNSUPPORTED_EXACT_PACKAGE_IDENTITY")
        if self.client.registry.rstrip("/") != PUBLIC_REGISTRY:
            return _out("UNKNOWN", "UNSUPPORTED_REGISTRY_IDENTITY")
        if (lock_record.get("version") != version or not _sri(lock_record.get("integrity"))
                or lock_record.get("name", name) != name):
            return _out("UNKNOWN", "LOCK_IDENTITY_UNBOUND")
        now = self.clock()
        if not math.isfinite(now) or now >= self.deadline:
            return _out("UNKNOWN", "METADATA_ATTEMPT_DEADLINE")
        context = self._context()
        if not context:
            return _out("UNKNOWN", "UNVERIFIED_TRANSPORT_CONTEXT")
        if self._transport is not None and context != self._transport:
            return _out("UNKNOWN", "TRANSPORT_CONTEXT_CHANGED_WITHIN_ATTEMPT")
        self._transport = context
        key = (name, version)
        cached = self._cache.get(key)
        if cached is not None and now < cached[0]:
            return _out("UNKNOWN", "METADATA_CLOCK_WENT_BACKWARD")
        fresh = cached is not None and 0 <= now - cached[0] <= self.ttl
        if not fresh:
            if self.requests >= self.max_requests:
                return _out("UNKNOWN", "METADATA_REQUEST_LIMIT")
            self.requests += 1
            try:
                payload, reason = self.client.registry_exact_version_manifest(name, version)
            except Exception:
                return _out("UNKNOWN", "METADATA_UNAVAILABLE")
            if reason != "EXACT_VERSION_METADATA_ADMITTED" or not isinstance(payload, dict):
                return _out("UNKNOWN", "METADATA_UNAVAILABLE")
            fetched = self.clock()
            if not now <= fetched < self.deadline:
                return _out("UNKNOWN", "METADATA_ATTEMPT_DEADLINE")
            try:raw = _canonical(payload)
            except (TypeError, ValueError):return _out("UNKNOWN", "INVALID_METADATA_PAYLOAD")
            if len(raw) > 2 * 1024 * 1024 or payload.get("name") != name or payload.get("version") != version:
                return _out("UNKNOWN", "METADATA_IDENTITY_OR_SIZE_INVALID")
            digest = hashlib.sha256(raw).hexdigest()
            if key in self._digests and self._digests[key] != digest:
                return _out("UNKNOWN", "CONFLICTING_METADATA_WITHIN_ATTEMPT")
            self._digests[key] = digest
            cached = (fetched, copy.deepcopy(payload), digest)
            self._cache[key] = cached
        fetched, payload, digest = cached
        dist = payload.get("dist", {})
        if (not isinstance(dist, dict) or dist.get("integrity") != lock_record.get("integrity")
                or not _sri(dist.get("integrity")) or dist.get("tarball") != lock_record.get("resolved")):
            return _out("UNKNOWN", "LOCK_REGISTRY_ARTIFACT_IDENTITY_MISMATCH")
        # The Core primitive rejects foreign/query-bearing tarball origins;
        # recheck here so injected engineering clients cover the same boundary.
        sys.path.insert(0, str(resolve_core_dir()))
        from registry_candidate_diagnostics import safe_registry_artifact_url
        if not safe_registry_artifact_url(PUBLIC_REGISTRY, dist.get("tarball")):
            return _out("UNKNOWN", "FOREIGN_ARTIFACT_ORIGIN")
        dependencies = payload.get("dependencies", {})
        if (not isinstance(dependencies, dict) or len(dependencies) > 256
                or any(not isinstance(n, str) or not _NAME.fullmatch(n) or not isinstance(r, str)
                       or not _RANGE.fullmatch(r) for n, r in dependencies.items())):
            return _out("UNKNOWN", "UNSUPPORTED_DEPENDENCY_DECLARATIONS")
        if any(payload.get(field) for field in ("optionalDependencies", "peerDependencies", "peerDependenciesMeta",
                                               "bundleDependencies", "bundledDependencies", "os", "cpu")):
            return _out("UNKNOWN", "CONDITIONAL_OR_BUNDLED_DEPENDENCIES_UNSUPPORTED")
        evidence = {"package": name, "version": version, "registry": PUBLIC_REGISTRY,
                    "source": PUBLIC_REGISTRY + "/" + quote(name, safe="@") + "/" + version,
                    "payloadSha256": digest, "digestEncoding": "canonical-json-not-wire-bytes",
                    "fetchedAtUnix": fetched, "freshUntilUnix": min(fetched + self.ttl, self.deadline),
                    "transportContextSha256": context, "integrity": dist["integrity"],
                    "resolved": dist["tarball"], "dependencies": copy.deepcopy(dependencies),
                    "origin": "TEST_DOUBLE_CONTROL_FLOW_ONLY" if self.engineering else "CORE_EXACT_PUBLIC_HTTP",
                    "cacheOrigin": "SAME_ATTEMPT_FRESH_MEMO" if fresh else "FRESH_EXACT_REQUEST"}
        return _out("PASS", "EXACT_PUBLISHED_MANIFEST_OBSERVED", evidence=evidence)


def _instance_name(location, record):
    name = location.rsplit("node_modules/", 1)[-1]
    if not _NAME.fullmatch(name) or (record.get("name") is not None and record["name"] != name):
        return None
    return name


def witness_published_ordinary_edge(parent_name, parent_version, child_name, child_version,
                                    lockfile_v2_context, supplier):
    """Confirm one published requirement; never claim necessary npm placement."""
    context = lockfile_v2_context
    if (not isinstance(context, dict) or type(context.get("lockfileVersion")) is not int
            or context['lockfileVersion'] not in (2,3)):
        return _out("UNKNOWN", "UNSUPPORTED_LOCK_FORMAT")
    parent, child = context.get("parent"), context.get("child")
    if not isinstance(parent, dict) or not isinstance(child, dict):
        return _out("UNKNOWN", "MISSING_LOCK_INSTANCE")
    first = supplier.fetch(parent_name, parent_version, parent)
    if first["status"] != "PASS":
        return first
    declarations = first["evidence"]["dependencies"]
    if child_name not in declarations:
        return _out("FAIL", "PUBLISHED_ORDINARY_EDGE_ABSENT")
    spec = declarations[child_name]
    if not isinstance(parent.get("dependencies", {}), dict):
        return _out("UNKNOWN", "INVALID_LOCK_DEPENDENCY_MAP")
    if parent.get("dependencies", {}).get(child_name) != spec:
        return _out("FAIL", "CANDIDATE_EDGE_DIFFERS_FROM_PUBLISHED_DECLARATION")
    try:
        compatible = bool(NpmSpec(spec).match(Version(child_version)))
    except (ValueError, TypeError):
        return _out("UNKNOWN", "UNSUPPORTED_RANGE_SEMANTICS")
    if not compatible:
        return _out("FAIL", "CHILD_VERSION_OUTSIDE_PUBLISHED_RANGE")
    second = supplier.fetch(child_name, child_version, child)
    if second["status"] != "PASS":
        return second
    return _out("PASS", "PUBLISHED_ORDINARY_EDGE_WITNESSED",
                parent=first["evidence"], child=second["evidence"], declaredRange=spec)


def witness_npm_target_paths(before, after, goal, supplier):
    """Supplement declared closure with exact published paths in both snapshots.

    A PASS concerns metadata edges only, not minimality, hoisting, installability,
    package behavior, or authorization of a specific changed instance.
    """
    try:
        declared = validate_npm_lock_scope(before, after, goal)
    except GoalContractError as exc:
        return _out("FAIL", exc.code, declaredLockClosure="FAIL", publishedEdges="NOT_EVALUATED")
    if declared.get("status") not in {"DECLARED_LOCK_CLOSURE_ONLY", "UNCHANGED_LOCK_SCOPE_PASS"}:
        return _out("UNKNOWN", "DECLARED_SCOPE_NOT_ESTABLISHED", publishedEdges="NOT_EVALUATED")
    observations = []
    paths = {}
    for label, lock in (("before", before), ("after", after)):
        packages = _scope_packages(lock,lock)[0]
        if packages[""].get("overrides") or packages[""].get("workspaces"):
            return _out("UNKNOWN", "OVERRIDES_OR_WORKSPACES_UNSUPPORTED")
        pending = ["node_modules/" + name for name in goal["exactTargets"]]
        seen = set()
        while pending:
            location = pending.pop()
            if location in seen:
                continue
            if len(seen) >= 64:
                return _out("UNKNOWN", "PUBLISHED_PATH_NODE_LIMIT")
            seen.add(location)
            record = packages.get(location)
            if (not isinstance(record, dict) or record.get("link") or record.get("optional")
                    or any(record.get(k) for k in ("optionalDependencies", "peerDependencies"))):
                return _out("UNKNOWN", "UNSUPPORTED_LOCK_INSTANCE")
            name = _instance_name(location, record)
            manifest = supplier.fetch(name, record.get("version"), record)
            if manifest["status"] != "PASS":
                return manifest
            deps = record.get("dependencies", {})
            if deps != manifest["evidence"]["dependencies"]:
                return _out("FAIL", "CANDIDATE_DEPENDENCIES_DIFFER_FROM_PUBLISHED_MANIFEST")
            observations.append(manifest["evidence"])
            for child_name in deps:
                target = _resolve(packages, location, child_name)
                if target is None:
                    return _out("UNKNOWN", "MISSING_RESOLVED_CHILD_INSTANCE")
                child = packages[target]
                if _instance_name(target, child) != child_name:
                    return _out("UNKNOWN", "ALIAS_INSTANCE_UNSUPPORTED")
                edge = witness_published_ordinary_edge(name, record["version"], child_name, child.get("version"),
                    {"lockfileVersion": lock["lockfileVersion"], "parent": record, "child": child}, supplier)
                if edge["status"] != "PASS":
                    return edge
                pending.append(target)
        paths[label] = seen
    for change in declared["changedInstances"]:
        location = change["instance"]
        if not any(location in paths[label] for label in ("before", "after")):
            return _out("UNKNOWN", "CHANGED_INSTANCE_HAS_NO_PUBLISHED_TARGET_PATH")
    return _out("PASS", "ORDINARY_PUBLISHED_TARGET_PATHS_WITNESSED",
                declaredLockClosure=declared["status"],
                publishedEdges="ENGINEERING_CONTROL_PASS" if supplier.engineering else "PASS",
                evidenceOrigin="ENGINEERING_CONTROL" if supplier.engineering else "LIVE_SOURCE_CONFIRMED",
                liveSourceConfirmed=not supplier.engineering,
                evidenceDigest=canonical_digest(observations), metadataObservations=observations,
                changedInstances=declared["changedInstances"], mutableInstancesAuthorized=False,
                verification="NOT_VERIFIED", executionReady=False)
