"""Conservative npm v2 instance/edge scope gate; never a resolver compatibility proof."""
from __future__ import annotations
from pathlib import PurePosixPath
import re
from deploom_mcp.goal_contract import GoalContractError, _roots, validate_goal, validate_manifest_change, validate_root_lock_versions, npm_lock_format


# Historical instance identities, not new goal-target name admission.
_PACKAGE_SEGMENT=re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _instance_identity(location,record):
    """Parse the entire nested npm instance path, never guess its final segment."""
    if not isinstance(location,str) or PurePosixPath(location).as_posix()!=location:
        raise GoalContractError("UNSUPPORTED_INSTANCE_IDENTITY")
    parts=PurePosixPath(location).parts;index=0;name=None
    while index<len(parts):
        if parts[index]!="node_modules":raise GoalContractError("UNSUPPORTED_INSTANCE_IDENTITY")
        index+=1
        if index>=len(parts):raise GoalContractError("UNSUPPORTED_INSTANCE_IDENTITY")
        if parts[index].startswith("@"):
            if (not _PACKAGE_SEGMENT.fullmatch(parts[index][1:]) or index+1>=len(parts)
                    or not _PACKAGE_SEGMENT.fullmatch(parts[index+1])):
                raise GoalContractError("UNSUPPORTED_INSTANCE_IDENTITY")
            name=parts[index]+"/"+parts[index+1];index+=2
        else:
            if not _PACKAGE_SEGMENT.fullmatch(parts[index]):raise GoalContractError("UNSUPPORTED_INSTANCE_IDENTITY")
            name=parts[index];index+=1
    version=record.get("version","")
    if (not name or record.get("name",name)!=name or not isinstance(version,str)
            or version.startswith(("npm:","file:","workspace:","git+","http:","https:"))):
        raise GoalContractError("UNSUPPORTED_INSTANCE_IDENTITY")
    return name


def _packages(lock, *, unchanged_aliases=False):
    packages=lock.get("packages")
    if not isinstance(packages,dict) or len(packages)>10000:
        raise GoalContractError("UNSUPPORTED_LOCK_GRAPH")
    if len({n.casefold() for n in packages if isinstance(n,str)})!=len(packages):
        raise GoalContractError('UNSUPPORTED_LOCK_GRAPH')
    for location,record in packages.items():
        if (not isinstance(location,str) or "\\" in location or ".." in PurePosixPath(location).parts
                or PurePosixPath(location).is_absolute() or not isinstance(record,dict) or record.get("link")):
            raise GoalContractError("UNSUPPORTED_LOCK_GRAPH")
        if location and not location.startswith("node_modules/"):
            raise GoalContractError("UNSUPPORTED_LOCK_GRAPH")
        if location:
            identity_record=record
            if unchanged_aliases and npm_lock_format(lock) in (2,3):
                identity_record={k:v for k,v in record.items() if k!='name'}
                canonical=record.get('name')
                if 'name' in record:
                    if not isinstance(canonical,str):raise GoalContractError('UNSUPPORTED_INSTANCE_IDENTITY')
                    if _instance_identity('node_modules/'+canonical,{'version':record.get('version')})!=canonical:
                        raise GoalContractError('UNSUPPORTED_INSTANCE_IDENTITY')
            _instance_identity(location,identity_record)
    return packages

def _alias_edges(packages):
    """Declared explicit aliases only; not publisher/physical verification."""
    from semantic_version import NpmSpec, Version
    aliases={path:row for path,row in packages.items() if path and row.get('name',
        _instance_identity(path,{k:v for k,v in row.items() if k!='name'})) !=
        _instance_identity(path,{k:v for k,v in row.items() if k!='name'})}
    incoming={path:[] for path in aliases}
    for owner,row in packages.items():
        for section in ('dependencies','devDependencies','optionalDependencies','peerDependencies'):
            edges=row.get(section,{})
            if not isinstance(edges,dict):raise GoalContractError('INVALID_LOCK_EDGE')
            for name,spec in edges.items():
                if not isinstance(name,str):raise GoalContractError('INVALID_LOCK_EDGE')
                if _instance_identity('node_modules/'+name,{'version':'0.0.0'})!=name:
                    raise GoalContractError('INVALID_LOCK_EDGE')
                parent=owner
                if section=='peerDependencies' and owner:
                    parent=owner.rsplit('/node_modules/',1)[0] if '/node_modules/' in owner else ''
                target=_resolve(packages,parent,name)
                if target not in aliases:continue
                canonical=aliases[target]['name']
                prefix='npm:'+canonical+'@'
                try:
                    if not isinstance(spec,str) or not spec.startswith(prefix):raise ValueError('explicit alias required')
                    requirement=spec[len(prefix):]
                    if not requirement or not NpmSpec(requirement).match(Version(aliases[target]['version'])):raise ValueError('alias range')
                except (ValueError,TypeError) as exc:
                    raise GoalContractError('UNSUPPORTED_ALIAS_EDGE',target) from exc
                incoming[target].append((owner,section,name,spec,target))
    if any(not rows for rows in incoming.values()):raise GoalContractError('UNSUPPORTED_ORPHAN_ALIAS')
    return {path:(aliases[path],sorted(rows)) for path,rows in incoming.items()}


def _scope_packages(before,after):
    """Only unchanged alias records AND complete incoming edges are admitted."""
    allow=npm_lock_format(before)==npm_lock_format(after) and npm_lock_format(before) in (2,3)
    old,new=_packages(before,unchanged_aliases=allow),_packages(after,unchanged_aliases=allow)
    for path,row in new.items():
        if not path:continue
        install=_instance_identity(path,{k:v for k,v in row.items() if k!='name'})
        canonical=row.get('name',install)
        if any(c.isupper() for c in install+canonical) and old.get(path)!=row:
            raise GoalContractError('UNSUPPORTED_HISTORICAL_INSTANCE_DELTA')
    if allow:
        old_aliases,new_aliases=_alias_edges(old),_alias_edges(new)
        if old_aliases!=new_aliases:raise GoalContractError('UNSUPPORTED_ALIAS_DELTA')
        if npm_lock_format(before)==2 and old_aliases:
            from npm_platform_obligations import v2_alias_bindings,NpmPlatformEvidenceError
            try:
                before_views,after_views=v2_alias_bindings(before),v2_alias_bindings(after)
            except NpmPlatformEvidenceError as exc:
                raise GoalContractError('UNSUPPORTED_ALIAS_V2_VIEW') from exc
            if before_views!=after_views:raise GoalContractError('UNSUPPORTED_ALIAS_DELTA')
            legacy_old,legacy_new=_legacy(before),_legacy(after)
            for alias in old_aliases:
                subtree=lambda rows:{p:r for p,r in rows.items() if p==alias or p.startswith(alias+'/node_modules/')}
                if subtree(legacy_old)!=subtree(legacy_new):raise GoalContractError('UNSUPPORTED_ALIAS_DELTA')
    return old,new


def _resolve(packages,location,name):
    if not isinstance(name,str) or "/../" in "/"+name+"/" or name.startswith("/") or "\\" in name:
        raise GoalContractError("INVALID_LOCK_EDGE")
    ancestors=[location]+[p.as_posix() for p in PurePosixPath(location).parents]
    for ancestor in ancestors:
        if ancestor==".":ancestor=""
        if ancestor and ancestor not in packages:continue
        candidate=(ancestor+"/" if ancestor else "")+"node_modules/"+name
        if candidate in packages:return candidate
    return None

def _closure(packages,roots):
    seen=set();pending=list(roots)
    while pending:
        location=pending.pop()
        if location in seen:continue
        if location not in packages:raise GoalContractError("MISSING_ROOT_INSTANCE",location)
        seen.add(location)
        record=packages[location]
        for section in ("dependencies","optionalDependencies","peerDependencies"):
            edges=record.get(section,{})
            if not isinstance(edges,dict):raise GoalContractError("INVALID_LOCK_EDGE")
            for name in edges:
                resolved=_resolve(packages,location,name)
                if resolved is not None:pending.append(resolved)
    return seen

def _legacy(lock):
    result={};folded=set();pending=[("",lock.get("dependencies",{}))]
    while pending:
        parent,children=pending.pop()
        if not isinstance(children,dict):raise GoalContractError("INVALID_LOCK_LEGACY_TREE")
        for name,record in children.items():
            if not isinstance(record,dict):raise GoalContractError("INVALID_LOCK_LEGACY_TREE")
            location=(parent+"/" if parent else "")+"node_modules/"+name
            if len(result)>10000 or location in result or location.casefold() in folded:raise GoalContractError("UNSUPPORTED_LOCK_GRAPH")
            folded.add(location.casefold())
            identity_record=record
            version=record.get('version')
            if isinstance(version,str) and version.startswith('npm:'):
                try:
                    from semantic_version import Version
                    canonical,pin=version[4:].rsplit('@',1);Version(pin)
                    _instance_identity('node_modules/'+canonical,{'version':pin})
                    if 'name' in record and record['name']!=canonical:raise ValueError('alias identity')
                except (ValueError,TypeError) as exc:raise GoalContractError('UNSUPPORTED_INSTANCE_IDENTITY') from exc
                identity_record={k:v for k,v in record.items() if k!='name'}
                identity_record['version']=pin
            _instance_identity(location,identity_record)
            result[location]={k:v for k,v in record.items() if k!="dependencies"}
            pending.append((location,record.get("dependencies",{})))
    return result

def validate_npm_lock_scope(before,after,goal,*,host_policy=None,check_profile=None):
    """Allow only target-owned closure deltas; shared/ambiguous rewrites fail closed.

    Root and nested instances are distinct. Existing shared instances may be
    reused unchanged, including dedup of a redundant target-exclusive child.
    A change to a shared non-target instance requires a broader explicit contract.
    A changed candidate lock is not published dependency metadata. Without a
    separate trusted metadata check this returns DECLARED_LOCK_CLOSURE_ONLY,
    never a sufficient scope/necessity proof for accepting an upgrade.
    """
    goal=validate_goal(goal);validate_root_lock_versions(before,after,goal)
    old,new=_scope_packages(before,after)
    if goal["goalKind"] == "baseline-observation":
        if before!=after:raise GoalContractError("BASELINE_OBSERVATION_CHANGED_LOCK")
        return {"status":"UNCHANGED_LOCK_SCOPE_PASS","necessityVerified":True,"changedInstances":[],"compatibility":"NOT_VERIFIED","sharedRewritePolicy":"REJECT"}
    validate_manifest_change(old[""],new[""],goal)
    from deploom_mcp.goal_artifact_scope import declared_host_artifact_scope
    preservation = declared_host_artifact_scope(before,after,goal,host_policy,check_profile)
    if preservation is not None:return preservation
    old_meta={k:v for k,v in before.items() if k not in {"packages","dependencies"}}
    new_meta={k:v for k,v in after.items() if k not in {"packages","dependencies"}}
    if old_meta!=new_meta:raise GoalContractError("LOCK_METADATA_SCOPE_VIOLATION")
    targets={"node_modules/"+n for n in goal["exactTargets"]}
    held={"node_modules/"+n for n in _roots(old[""]) if n not in goal["exactTargets"]}
    owned=_closure(old,targets)|_closure(new,targets)
    shared=(_closure(old,held)|_closure(new,held))-targets
    from deploom_mcp.goal_shared_scope import validate_host_shared_scope
    authorized_shared=validate_host_shared_scope(before,after,goal,host_policy,check_profile)
    if not authorized_shared.issubset(shared & owned):
        raise GoalContractError('SHARED_TRANSITION_NOT_TARGET_OWNED_SHARED')
    allowed=targets|(owned-shared)|authorized_shared
    changed=[]
    protected=set(goal["immutablePackages"])|set(goal["excludedPackages"])
    views=[("packages",old,new)]
    if npm_lock_format(before)==2:views.append(("dependencies",_legacy(before),_legacy(after)))
    for label,a,b in views:
        for location in set(a)|set(b):
            if location=="" or a.get(location)==b.get(location):continue
            identities={_instance_identity(location,record) for record in (a.get(location),b.get(location)) if record is not None}
            if identities & protected:
                raise GoalContractError("IMMUTABLE_OR_EXCLUDED_INSTANCE_SCOPE_VIOLATION",location)
            if location not in allowed:raise GoalContractError("TRANSITIVE_SCOPE_VIOLATION",location)
            changed.append({"view":label,"instance":location})
    return {"status":"DECLARED_LOCK_CLOSURE_ONLY","necessityVerified":False,
            "missingEvidence":["PUBLISHED_METADATA_FOR_CHANGED_INSTANCES"],
            "changedInstances":sorted(changed,key=lambda x:(x["view"],x["instance"])),
            "compatibility":"NOT_VERIFIED","sharedRewritePolicy":"EXACT_HOST_ENUMERATED" if authorized_shared else "REJECT",
            "authorizedSharedInstances":sorted(authorized_shared)}


def _preservation_artifact(location, record):
    """Declared registry artifact identity, not publisher authenticity."""
    import base64
    from urllib.parse import urlsplit
    from semantic_version import Version
    name = _instance_identity(location, record)
    try:
        Version(record["version"])
        sri = record["integrity"]
        if not isinstance(sri, str) or not sri.startswith("sha512-") or len(base64.b64decode(sri[7:], validate=True)) != 64:
            raise ValueError("invalid SRI")
        resolved = record["resolved"]
        if not isinstance(resolved, str):
            raise ValueError("unsupported artifact source")
        url = urlsplit(resolved)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("unsupported artifact source")
    except (KeyError, ValueError, TypeError) as exc:
        raise GoalContractError("UNSUPPORTED_PRESERVATION_ARTIFACT_IDENTITY") from exc
    return (name, record["version"], sri, resolved)


def _preservation_view_identity(location, record, view, *, legacy=False):
    """Identity of a declared untouched bundle member is anchored to its owner.

    A member without its own SRI is NOT represented as a verified registry
    artifact. No member contents or publisher authenticity are proven here.
    """
    from semantic_version import Version
    flag = "bundled" if legacy else "inBundle"
    if record.get(flag) is not True:
        return ("REGISTRY_DECLARATION", *_preservation_artifact(location, record))
    if record.get("resolved") or record.get("integrity"):
        raise GoalContractError("UNSUPPORTED_BUNDLED_ARTIFACT_IDENTITY")
    name = _instance_identity(location, record)
    try:
        Version(record["version"])
    except (ValueError, KeyError, TypeError) as exc:
        raise GoalContractError("UNSUPPORTED_BUNDLED_ARTIFACT_IDENTITY") from exc
    for ancestor in PurePosixPath(location).parents:
        owner_path = ancestor.as_posix()
        owner = view.get(owner_path)
        if owner is None or not owner_path or owner_path == "." or owner.get(flag) is True:
            continue
        owner_identity = _preservation_artifact(owner_path, owner)
        return ("BUNDLED_MEMBER_DECLARATION", name, record["version"], owner_identity,
                PurePosixPath(location).relative_to(ancestor).as_posix())
    raise GoalContractError("UNSUPPORTED_BUNDLED_ARTIFACT_OWNER")


def _preservation_edge_maps(record):
    result = {}
    for section in ("dependencies", "optionalDependencies", "peerDependencies"):
        edges = record.get(section, {})
        if not isinstance(edges, dict):
            raise GoalContractError("INVALID_LOCK_EDGE")
        for name, spec in edges.items():
            if not isinstance(name, str) or not isinstance(spec, str):
                raise GoalContractError("UNSUPPORTED_PRESERVATION_EDGE")
            # The entire grammar is parsed; a slash cannot become a path lookup.
            if _instance_identity("node_modules/"+name, {"version": "0.0.0"}) != name:
                raise GoalContractError("UNSUPPORTED_PRESERVATION_EDGE")
        result[section] = edges
    return result


def _preservation_views(lock):
    """Cross-check identity/ordinary requirements in npm v2's two declared views."""
    if type(lock.get("lockfileVersion")) is not int or lock["lockfileVersion"] != 2:
        raise GoalContractError("UNSUPPORTED_PRESERVATION_LOCK_VERSION")
    packages, legacy = _packages(lock), _legacy(lock)
    if set(packages)-{""} != set(legacy):
        raise GoalContractError("PRESERVATION_LOCK_VIEW_MISMATCH")
    for location, record in packages.items():
        if not location:
            continue
        other = legacy[location]
        pieces = location.split("/node_modules/")
        ancestor = pieces[0]
        for piece in pieces[1:]:
            if ancestor not in packages:
                raise GoalContractError("UNSUPPORTED_PRESERVATION_ORPHAN_INSTANCE")
            ancestor += "/node_modules/"+piece
        if _preservation_view_identity(location, record, packages) != _preservation_view_identity(location, other, legacy, legacy=True):
            raise GoalContractError("PRESERVATION_LOCK_VIEW_MISMATCH")
        if record.get("inBundle", False) != other.get("bundled", False):
            raise GoalContractError("PRESERVATION_LOCK_VIEW_MISMATCH")
        if any(type(rec.get(flag, False)) is not bool for rec, flag in ((record, "inBundle"), (other, "bundled"))):
            raise GoalContractError("UNSUPPORTED_BUNDLED_ARTIFACT_IDENTITY")
        _preservation_edge_maps(record)
        for key in ("dev", "optional", "devOptional", "link", "peer"):
            if record.get(key) != other.get(key):
                raise GoalContractError("PRESERVATION_LOCK_VIEW_MISMATCH")
        for key in ("dependencies", "optionalDependencies"):
            if not isinstance(record.get(key, {}), dict):
                raise GoalContractError("INVALID_LOCK_EDGE")
        requires = {**record.get("dependencies", {}), **record.get("optionalDependencies", {})}
        if requires != other.get("requires", {}):
            raise GoalContractError("PRESERVATION_LOCK_VIEW_MISMATCH")
    return packages, legacy


def _preservation_conditional(record):
    return any(record.get(key) for key in (
        "optional", "devOptional", "peer", "optionalDependencies", "peerDependencies",
        "peerDependenciesMeta", "bundleDependencies", "bundledDependencies", "inBundle",
        "os", "cpu", "libc", "link"))


def evaluate_npm_artifact_preservation(before, after, goal, contract, *, before_manifest=None, after_manifest=None):
    """Read-only scope comparison for one explicitly enumerated artifact copy.

    This function CANNOT authorize apply, validate a publisher, confirm an npm
    install or publish a proof. Semantic digests bind the supplied lock objects;
    the existing source/delta owner must separately bind actual file bytes.
    The default validate_npm_lock_scope path is deliberately unchanged.
    """
    from deploom_mcp.goal_contract import canonical_digest
    base = {"fullAcceptance": False, "executionAuthority": "NONE",
            "compatibility": "NOT_VERIFIED", "necessity": "UNKNOWN",
            "metadata": "NOT_VERIFIED", "physicalResolution": "NOT_VERIFIED"}
    def result(status, reason, **fields):
        return {**base, "status": status, "reason": reason, **fields}
    try:
        if not all(isinstance(value, dict) for value in (before, after, goal, contract)):
            return result("UNKNOWN", "INVALID_PRESERVATION_INPUT")
        goal = validate_goal(goal)
        if not isinstance(before_manifest, dict) or not isinstance(after_manifest, dict):
            return result("UNKNOWN", "PRESERVATION_MANIFEST_INPUTS_REQUIRED")
        fields = {"schemaVersion", "mode", "beforeLockDigest", "afterLockDigest", "beforeManifestDigest", "afterManifestDigest", "goalDigest", "preservations"}
        if (not isinstance(contract, dict) or set(contract) != fields or type(contract["schemaVersion"]) is not int or contract["schemaVersion"] != 1
                or contract["mode"] != "PRESERVE_HELD_ARTIFACT_V1"
                or not isinstance(contract["preservations"], list) or len(contract["preservations"]) != 1):
            return result("FAIL", "INVALID_PRESERVATION_CONTRACT")
        digests = {"beforeLockDigest": canonical_digest(before), "afterLockDigest": canonical_digest(after),
                   "beforeManifestDigest": canonical_digest(before_manifest), "afterManifestDigest": canonical_digest(after_manifest),
                   "goalDigest": canonical_digest(goal)}
        if any(contract[k] != value for k, value in digests.items()):
            return result("FAIL", "STALE_PRESERVATION_CONTRACT")
        if goal["goalKind"] != "functional-maintenance":
            return result("FAIL", "BASELINE_OBSERVATION_HAS_NO_EXECUTION_AUTHORITY")
        validate_root_lock_versions(before, after, goal)
        old, old_legacy = _preservation_views(before)
        new, new_legacy = _preservation_views(after)
        for manifest in (before_manifest, after_manifest, old[""], new[""]):
            for root_name in _roots(manifest):
                if _instance_identity("node_modules/"+root_name, {"version": "0.0.0"}) != root_name:
                    raise GoalContractError("UNSUPPORTED_PRESERVATION_ROOT_NAME")
        validate_manifest_change(old[""], new[""], goal)
        validate_manifest_change(before_manifest, after_manifest, goal)
        if _roots(before_manifest) != _roots(old[""]) or _roots(after_manifest) != _roots(new[""]):
            return result("FAIL", "PRESERVATION_MANIFEST_LOCK_ROOT_MISMATCH")
        if any(old[""].get(k) or new[""].get(k) for k in ("overrides", "workspaces")):
            return result("UNKNOWN", "PRESERVATION_OVERRIDES_OR_WORKSPACES_UNSUPPORTED")
        if ({k:v for k,v in before.items() if k not in {"packages", "dependencies"}}
                != {k:v for k,v in after.items() if k not in {"packages", "dependencies"}}):
            return result("FAIL", "LOCK_METADATA_SCOPE_VIOLATION")
        mapping = contract["preservations"][0]
        if not isinstance(mapping, dict) or set(mapping) != {"package", "version", "beforePath", "afterPath", "contextFlags"}:
            return result("FAIL", "INVALID_PRESERVATION_CONTRACT")
        name, version, src, dst = (mapping[k] for k in ("package", "version", "beforePath", "afterPath"))
        if not all(isinstance(v, str) for v in (name, version, src, dst)):
            return result("FAIL", "INVALID_PRESERVATION_CONTRACT")
        if (name not in goal["exactTargets"] or src != "node_modules/"+name or src == dst
                or dst in old or src not in old or src not in new or dst not in new
                or dst == "node_modules/"+name):
            return result("FAIL", "PRESERVATION_LAYOUT_NOT_ENUMERATED")
        if (name in set(goal["immutablePackages"])|set(goal["excludedPackages"])
                or _instance_identity(dst, new[dst]) != name or old[src].get("version") != version):
            return result("FAIL", "PRESERVATION_TARGET_NOT_AUTHORIZED")
        if version == goal["exactTargets"][name]:
            return result("FAIL", "PRESERVATION_NOT_VERSION_TRANSITION")
        if _preservation_artifact(src, old[src]) != _preservation_artifact(dst, new[dst]):
            return result("FAIL", "PRESERVATION_ARTIFACT_CHANGED")
        if _preservation_conditional(old[src]) or _preservation_conditional(new[dst]):
            return result("UNKNOWN", "PRESERVATION_CONDITIONAL_ARTIFACT_UNSUPPORTED")
        if any(type(rec.get("dev", False)) is not bool for rec in (old[src], new[dst])):
            return result("UNKNOWN", "PRESERVATION_CONTEXT_FLAG_UNSUPPORTED")
        context_flags = {"dev": {"before": old[src].get("dev"), "after": new[dst].get("dev")}}
        flags = mapping["contextFlags"]
        if (not isinstance(flags, dict) or set(flags) != {"dev"} or not isinstance(flags["dev"], dict)
                or set(flags["dev"]) != {"before", "after"}
                or any(value is not None and type(value) is not bool for value in flags["dev"].values())
                or flags != context_flags):
            return result("FAIL", "PRESERVATION_CONTEXT_FLAG_NOT_ENUMERATED")
        if {k:v for k,v in old[src].items() if k != "dev"} != {k:v for k,v in new[dst].items() if k != "dev"}:
            return result("FAIL", "PRESERVATION_ARTIFACT_RECORD_CHANGED")
        allowed = {src, dst}
        changes = []
        for view, a, b in (("packages", old, new), ("dependencies", old_legacy, new_legacy)):
            changed = {n for n in set(a)|set(b) if n and a.get(n) != b.get(n)}
            if changed != allowed:
                return result("FAIL", "PRESERVATION_UNENUMERATED_DELTA")
            changes.extend({"view": view, "instance": n} for n in sorted(changed))
        held = {"node_modules/"+n for n in _roots(old[""]) if n not in goal["exactTargets"]}
        old_closure, new_closure = _closure(old, held), _closure(new, held)
        remap = lambda path: dst if path == src else path
        if src not in old_closure or dst not in new_closure or {remap(n) for n in old_closure} != new_closure:
            return result("FAIL", "PRESERVATION_HELD_CLOSURE_CHANGED")
        comparisons, affected, bundled_contexts, unresolved_conditional = 0, [], [], 0
        for consumer in sorted(old_closure):
            counterpart = remap(consumer)
            a, b = old[consumer], new[counterpart]
            if a.get("inBundle"):
                if consumer != counterpart or a != b:
                    return result("UNKNOWN", "PRESERVATION_AFFECTED_BUNDLED_CONTEXT_UNSUPPORTED")
                bundled_contexts.append(consumer)
            if _preservation_view_identity(consumer, a, old) != _preservation_view_identity(counterpart, b, new):
                return result("FAIL", "PRESERVATION_HELD_ARTIFACT_CHANGED")
            for section in ("dependencies", "optionalDependencies", "peerDependencies"):
                edges = a.get(section, {})
                if not isinstance(edges, dict) or edges != b.get(section, {}):
                    return result("FAIL", "PRESERVATION_HELD_DECLARATION_CHANGED")
                for child, spec in sorted(edges.items()):
                    if section == "dependencies" and child in a.get("optionalDependencies", {}):
                        continue
                    left, right = _resolve(old, consumer, child), _resolve(new, counterpart, child)
                    if left is None or right is None:
                        if left != right:
                            return result("UNKNOWN", "PRESERVATION_EDGE_PRESENCE_CHANGED")
                        # Unchanged missing conditional edges confer no compatibility.
                        if section == "dependencies":
                            return result("UNKNOWN", "PRESERVATION_REQUIRED_EDGE_UNRESOLVED")
                        unresolved_conditional += 1
                        continue
                    comparisons += 1
                    if _preservation_view_identity(left, old[left], old) != _preservation_view_identity(right, new[right], new):
                        return result("FAIL", "PRESERVATION_HELD_EDGE_CHANGED")
                    if left != right or consumer != counterpart:
                        if (section != "dependencies" or a.get("inBundle") or b.get("inBundle")
                                or old[left].get("inBundle") or new[right].get("inBundle")
                                or _preservation_conditional(a) or _preservation_conditional(b)):
                            return result("UNKNOWN", "PRESERVATION_AFFECTED_CONDITIONAL_EDGE_UNSUPPORTED")
                        if any(not isinstance(v, str) or v.startswith(("npm:", "file:", "git+", "http:", "https:", "workspace:")) for v in (child, spec)):
                            return result("UNKNOWN", "PRESERVATION_AFFECTED_EDGE_SPEC_UNSUPPORTED")
                        affected.append({"consumer": consumer, "afterConsumer": counterpart,
                            "kind": section, "package": child, "declaredRange": spec,
                            "beforePath": left, "afterPath": right,
                            "artifactDigest": canonical_digest(_preservation_artifact(left, old[left]))})
        if not affected:
            return result("FAIL", "PRESERVATION_HAS_NO_HELD_CONSUMER")
        return result("DECLARED_HELD_ARTIFACTS_PRESERVED_ONLY", "DECLARED_LAYOUT_PRESERVATION_MATCH",
            semanticInputDigests=digests, contractDigest=canonical_digest(contract),
            heldInstanceCount=len(old_closure), comparedHeldEdges=comparisons, affectedEdges=affected,
            changedInstances=changes, contextFlagChanges=context_flags,
            unchangedBundledContexts=bundled_contexts, unresolvedUnchangedConditionalEdges=unresolved_conditional,
            missingEvidence=["AUTHORIZED_REGISTRY_ARTIFACT_CONSISTENCY", "STRICT_INSTALL_AND_FULL_TREE",
                             "PHYSICAL_AFFECTED_EDGE_RESOLUTION", "CONFIGURATION_SPECIFIC_INSTALL_STATE",
                             "SOURCE_LOCK_TOOLCHAIN_CONTINUITY", "OWNED_INVOCATION_CLOSURE"])
    except GoalContractError as exc:
        unknown = exc.code.startswith(("UNSUPPORTED_", "INVALID_LOCK_"))
        return result("UNKNOWN" if unknown else "FAIL", exc.code)
    except (TypeError, ValueError, KeyError):
        return result("UNKNOWN", "INVALID_PRESERVATION_INPUT")
