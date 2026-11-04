"""Bounded npm-workspace declarations, not a solver or execution admission."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

from deploom_mcp.decisions import _glob_match
from deploom_mcp.workspace_input_io import bound_directory, open_bound_file, selected_root
from deploom_mcp.goal_contract import GoalContractError, canonical_digest

SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
_NAME = re.compile(r"^(?:@[a-zA-Z0-9_.-]+/)?[a-zA-Z0-9_.-]+$")
_PRUNE = {"node_modules", ".git"}


def _safe(path: Path, *, directory: bool = False) -> os.stat_result:
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise GoalContractError("WORKSPACE_UNSAFE_INPUT")
    return info


def _pairs(rows):
    value = {}
    for key, item in rows:
        if key in value:
            raise GoalContractError("WORKSPACE_DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _read_bytes(path: Path, limit: int, *, root: Path | None = None, root_handle=None) -> bytes:
    before = _safe(path)
    if before.st_size > limit:
        raise GoalContractError("WORKSPACE_INPUT_LIMIT")
    with bound_directory(root or path.parent, path.parent, root_handle) as parent:
        with open_bound_file(path, parent) as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise GoalContractError("WORKSPACE_INPUT_CHANGED")
            raw = stream.read(limit + 1)
            read_end = os.fstat(stream.fileno())
    after = _safe(path)
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if len(raw) > limit or identity(before) != identity(opened) or identity(opened) != identity(read_end) or identity(read_end) != identity(after):
        raise GoalContractError("WORKSPACE_INPUT_CHANGED")
    return raw


def _read(path: Path, limit: int, *, root: Path | None = None, root_handle=None) -> tuple[bytes, dict]:
    raw = _read_bytes(path, limit, root=root, root_handle=root_handle)
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs)
    if not isinstance(value, dict):
        raise GoalContractError("WORKSPACE_INVALID_JSON_OBJECT")
    return raw, value


def _npmrc_inputs(root, root_handle, paths):
    configs = {}
    for manifest in paths:
        path = (Path(manifest).parent / ".npmrc").as_posix()
        try:
            (root / path).lstat()
        except FileNotFoundError:
            continue
        configs[path] = _read_bytes(root / path, 16 * 1024, root=root, root_handle=root_handle)
    return configs


def _relative(value: str) -> bool:
    return (isinstance(value, str) and bool(value) and not value.startswith("/")
            and "\\" not in value and ":" not in value
            and all(part not in {"", ".", "..", "node_modules", ".git"} for part in value.split("/")))


def _patterns(manifest: dict) -> list[str]:
    patterns = manifest.get("workspaces")
    if (not isinstance(patterns, list) or not patterns or len(patterns) > 128
            or any(not isinstance(p, str) or len(p) > 256 or not _relative(p)
                   or any(c in p for c in "!{}[]?()|") or "**" in p for p in patterns)):
        raise GoalContractError("WORKSPACE_PATTERN_UNSUPPORTED")
    return sorted(set(patterns))


def _discover(root: Path, patterns: list[str], max_directories: int, max_manifests: int, root_handle=None) -> list[str]:
    manifests = ["package.json"]
    visited, entries = 0, 0
    matched = set()
    pending = [root]
    while pending:
        folder = pending.pop()
        with bound_directory(root, folder, root_handle) as scan_target:
            _safe(folder, directory=True)
            visited += 1
            if visited > max_directories:
                raise GoalContractError("WORKSPACE_DISCOVERY_LIMIT")
            relative = folder.relative_to(root).as_posix()
            included = [p for p in patterns if _glob_match(relative, p)]
            if included:
                if not (folder / "package.json").is_file():
                    raise GoalContractError("WORKSPACE_MANIFEST_MISSING")
                manifests.append(relative + "/package.json")
                matched.update(included)
                if len(manifests) > max_manifests:
                    raise GoalContractError("WORKSPACE_MANIFEST_LIMIT")
            with os.scandir(scan_target.path if os.name=='nt' else scan_target.fd) as children:
                for child in children:
                    entries += 1
                    if entries > 32768:
                        raise GoalContractError("WORKSPACE_DISCOVERY_LIMIT")
                    if child.name in _PRUNE:
                        continue
                    child_path = folder / child.name
                    info = child.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        raise GoalContractError("WORKSPACE_UNSAFE_INPUT")
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(child_path)
    if matched != set(patterns):
        raise GoalContractError("WORKSPACE_PATTERN_UNMATCHED")
    return sorted(manifests)


def safe_spec(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 2048 and re.fullmatch(r"[A-Za-z0-9.*+^~<>=| -]+", value) is not None


def validate_workspace_targets(targets):
    if not isinstance(targets, list) or not 1 <= len(targets) <= 256:
        raise GoalContractError("INVALID_WORKSPACE_TARGETS")
    for target in targets:
        if (not isinstance(target, dict) or set(target) != {"owner", "section", "name", "fromSpec", "toVersion"}
                or any(not isinstance(v, str) for v in target.values())
                or not (target["owner"] == "." or _relative(target["owner"]))
                or target["section"] not in SECTIONS or not _NAME.fullmatch(target["name"])
                or not safe_spec(target["fromSpec"])
                or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", target["toVersion"])):
            raise GoalContractError("INVALID_WORKSPACE_TARGETS")


def _declarations(manifest: dict, owner: str) -> list[dict]:
    rows = []
    for section in SECTIONS:
        values = manifest.get(section, {})
        if not isinstance(values, dict):
            raise GoalContractError("WORKSPACE_INVALID_DECLARATION")
        for name, spec in sorted(values.items()):
            if not _NAME.fullmatch(name) or not isinstance(spec, str) or not spec or len(spec) > 2048:
                raise GoalContractError("WORKSPACE_INVALID_DECLARATION")
            if not safe_spec(spec):
                raise GoalContractError("WORKSPACE_SENSITIVE_OR_UNSUPPORTED_SPEC")
            rows.append({"owner": owner, "section": section, "name": name, "declaredSpec": spec})
    return rows


def observe_npm_workspace(project: Path, *, max_directories: int = 2048, max_manifests: int = 256, lock_resolver=None, plan_snapshot=None) -> dict:
    """Observe an explicit root only. COMPLETE refers to declarations, never resolution."""
    if (type(max_directories) is not int or not 1 <= max_directories <= 2048
            or type(max_manifests) is not int or not 1 <= max_manifests <= 256):
        raise GoalContractError("WORKSPACE_INVALID_LIMIT")
    project = Path(project)
    if not project.is_absolute() or ".." in project.parts:
        raise GoalContractError("INVALID_PROJECT_PATH")
    original_root = _safe(project, directory=True)
    for component in project.parents:
        _safe(component, directory=True)
    with selected_root(project, original_root) as root_handle:
        return _observe_npm_workspace_bound(project, root_handle, max_directories, max_manifests, lock_resolver, plan_snapshot)


def _observe_npm_workspace_bound(root, root_handle, max_directories, max_manifests, lock_resolver, plan_snapshot):
    if any((root / name).exists() for name in ("npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml")):
        raise GoalContractError("WORKSPACE_LOCK_OWNER_UNSUPPORTED")
    root_raw, manifest = _read(root / "package.json", 2 * 1024 * 1024, root=root, root_handle=root_handle)
    if not isinstance(manifest.get("packageManager", "npm"), str) or manifest.get("packageManager", "npm").split("@")[0] != "npm":
        raise GoalContractError("UNSUPPORTED_WORKSPACE_OR_MANAGER")
    patterns = _patterns(manifest)
    lock_raw, lock = _read(root / "package-lock.json", 32 * 1024 * 1024, root=root, root_handle=root_handle)
    if type(lock.get("lockfileVersion")) is not int or lock["lockfileVersion"] != 3:
        raise GoalContractError("WORKSPACE_LOCK_V3_REQUIRED")
    packages = lock.get("packages")
    if not isinstance(packages, dict) or len(packages) > 100000:
        raise GoalContractError("WORKSPACE_INVALID_LOCK")
    paths = _discover(root, patterns, max_directories, max_manifests, root_handle)
    inputs = {"package.json": root_raw, "package-lock.json": lock_raw}
    configs = _npmrc_inputs(root, root_handle, paths)
    owners, declarations, names = [], [], {}
    for path in paths:
        if path == "package.json":
            raw, member = root_raw, manifest
            owner = "."
        else:
            raw, member = _read(root / path, 2 * 1024 * 1024, root=root, root_handle=root_handle)
            inputs[path] = raw
            owner = path[:-len("/package.json")]
        name = member.get("name")
        if (owner != "." and (not isinstance(name, str) or not _NAME.fullmatch(name))) or (name is not None and (not isinstance(name, str) or not _NAME.fullmatch(name))):
            raise GoalContractError("WORKSPACE_INVALID_NAME")
        if name is not None:
            if name.casefold() in names:
                raise GoalContractError("WORKSPACE_DUPLICATE_NAME")
            names[name.casefold()] = owner
        record = packages.get("" if owner == "." else owner)
        if not isinstance(record, dict) or record.get("link"):
            raise GoalContractError("WORKSPACE_LOCK_MEMBER_MISSING")
        for key in (*SECTIONS, "peerDependenciesMeta", "name", "version", "workspaces"):
            if key == "name" and key not in record and owner != ".":
                continue
            default = {} if key in (*SECTIONS, "peerDependenciesMeta") else None
            if record.get(key, default) != member.get(key, default):
                raise GoalContractError("WORKSPACE_MANIFEST_LOCK_MISMATCH")
        owners.append({"owner": owner, "name": name, "manifestPath": path,
                       "manifestSha256": hashlib.sha256(raw).hexdigest()})
        declarations.extend(_declarations(member, owner))
        if len(declarations) > 4096 or sum(map(len, inputs.values())) > 64 * 1024 * 1024:
            raise GoalContractError("WORKSPACE_INPUT_LIMIT")
    expected_targets = {row["owner"]: row["name"] for row in owners if row["owner"] != "."}
    actual_targets = {key for key in packages if key and "node_modules" not in key.split("/")}
    if actual_targets != set(expected_targets):
        raise GoalContractError("WORKSPACE_LOCK_MEMBERSHIP_MISMATCH")
    links = []
    for path, record in sorted(packages.items()):
        if not isinstance(record, dict):
            raise GoalContractError("WORKSPACE_INVALID_LOCK")
        if not record.get("link"):
            continue
        target = record.get("resolved")
        if record.get("link") is not True or not _relative(target) or target not in expected_targets:
            raise GoalContractError("WORKSPACE_LINK_TARGET_UNSUPPORTED")
        if path != "node_modules/" + expected_targets[target]:
            raise GoalContractError("WORKSPACE_LINK_OWNER_MISMATCH")
        links.append({"path": path, "targetOwner": target})
    if {row["targetOwner"] for row in links} != set(expected_targets):
        raise GoalContractError("WORKSPACE_LINK_MISSING")
    if paths != _discover(root, patterns, max_directories, max_manifests, root_handle):
        raise GoalContractError("WORKSPACE_INPUT_CHANGED")
    for path, raw in inputs.items():
        if _read(root / path, 32 * 1024 * 1024, root=root, root_handle=root_handle)[0] != raw:
            raise GoalContractError("WORKSPACE_INPUT_CHANGED")
    identity = {"manifestInputs": {p: hashlib.sha256(b).hexdigest() for p, b in sorted(inputs.items())},
                "workspaceOwners": [o["owner"] for o in owners], "patterns": patterns}
    if configs != _npmrc_inputs(root, root_handle, paths):
        raise GoalContractError("WORKSPACE_INPUT_CHANGED")
    if configs:
        identity["npmConfigInputs"] = {path: hashlib.sha256(raw).hexdigest() for path, raw in configs.items()}
    if plan_snapshot is not None:
        return plan_snapshot(canonical_digest(identity), declarations, packages, {**inputs, **configs})
    lock_resolutions = None
    if lock_resolver is not None:
        lock_resolutions = []
        for declaration in declarations:
            owner = declaration["owner"]
            instance = lock_resolver(packages, declaration["name"], "" if owner == "." else owner)
            lock_resolutions.append({
                **declaration,
                "lockPath": instance[0] if instance is not None else None,
                "lockVersion": instance[1] if instance is not None else None,
            })
    return {"schemaVersion": 1, "kind": "npm-workspace-declaration-inventory",
            "lockResolutionEvidence": {"authority": "LOCKFILE_ONLY", "rows": lock_resolutions},
            "status": "DECLARATIONS_OBSERVED", "discoveryComplete": True,
            "discovery": {"status": "COMPLETE", "ownerCount": len(owners), "declarationCount": len(declarations), "reasons": []},
            "identity": identity, "inventoryDigest": canonical_digest(identity),
            "owners": owners, "declarations": declarations, "workspaceLinks": links,
            "lockOwner": ".", "lockfileVersion": 3, "jointAssignment": "NOT_COMPUTED",
            "physicalTree": "NOT_OBSERVED", "verification": "NOT_VERIFIED",
            "authority": "READ_ONLY_INVENTORY", "executionReady": False,
            "supportedPatterns": "relative literals and single-segment star; no globstar/negation/braces",
            "sourceContinuity": "READ_TWICE_ONLY_NOT_AN_EXECUTION_BINDING"}


def propose_workspace_targets(project: Path, expected_inventory_digest: str, targets: list[dict]) -> dict:
    """Reobserve before producing an unverified owner-qualified declaration proposal."""
    current = observe_npm_workspace(project)
    if current["inventoryDigest"] != expected_inventory_digest:
        raise GoalContractError("STALE_WORKSPACE_INVENTORY")
    validate_workspace_targets(targets)
    declarations = {(r["owner"], r["section"], r["name"]): r for r in current["declarations"]}
    selected, changes = set(), []
    for target in targets:
        if (not isinstance(target, dict) or set(target) != {"owner", "section", "name", "fromSpec", "toVersion"}
                or any(not isinstance(v, str) for v in target.values())
                or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", target["toVersion"])):
            raise GoalContractError("INVALID_WORKSPACE_TARGETS")
        key = (target["owner"], target["section"], target["name"])
        if key in selected or key not in declarations or declarations[key]["declaredSpec"] != target["fromSpec"]:
            raise GoalContractError("WORKSPACE_TARGET_NOT_CURRENT_DECLARATION")
        if target["name"] in {o["name"] for o in current["owners"] if o["owner"] != "."}:
            raise GoalContractError("WORKSPACE_INTERNAL_TARGET_UNSUPPORTED")
        selected.add(key)
        changes.append(dict(target))
    held = [r for key, r in declarations.items() if key not in selected]
    return {"schemaVersion": 1, "kind": "npm-workspace-declaration-proposal",
            "inventoryDigest": current["inventoryDigest"], "targets": sorted(changes, key=lambda r: (r["owner"], r["section"], r["name"])),
            "heldDeclarationCount": len(held), "heldDeclarationsDigest": canonical_digest(held),
            "heldDeclarationsByOwner": [{"owner": o["owner"], "count": len([r for r in held if r["owner"] == o["owner"]]),
                "digest": canonical_digest([r for r in held if r["owner"] == o["owner"]])} for o in current["owners"]],
            "globalImmutableInstances": "NOT_SPECIFIED_BY_THIS_PROPOSAL",
            "authority": "PLANNING_ONLY", "verification": "NOT_VERIFIED",
            "executionReady": False, "jointAssignment": "NOT_COMPUTED",
            "note": "Declaration intent only. No package availability, peers, installation, source repair or project checks evaluated."}


def main() -> int:
    try:
        if len(sys.argv) == 4 and sys.argv[2] == "plan-snapshot":
            from deploom_mcp.workspace_plan_snapshot import plan_workspace_snapshot

            result = {"ok": True, "plan": plan_workspace_snapshot(Path(sys.argv[1]), json.loads(sys.argv[3]))}
        elif len(sys.argv) not in {2, 3}:
            raise GoalContractError("INVALID_WORKSPACE_REQUEST")
        elif len(sys.argv) == 3:
            if len(sys.argv[2]) > 65536:
                raise GoalContractError("INVALID_WORKSPACE_REQUEST")
            request = json.loads(sys.argv[2])
            if not isinstance(request, dict) or set(request) != {"inventoryDigest", "targets"}:
                raise GoalContractError("INVALID_WORKSPACE_REQUEST")
            result = {"ok": True, "proposal": propose_workspace_targets(Path(sys.argv[1]), request["inventoryDigest"], request["targets"])}
        else:
            from deploom_mcp.distribution import resolve_core_dir

            core_dir = resolve_core_dir()
            lock_resolver = None
            if (core_dir / "resolution_inventory.py").is_file():
                sys.path.insert(0, str(core_dir))
                try:
                    import resolution_inventory
                    lock_resolver = getattr(resolution_inventory, "npm_lock_resolved_instance", None)
                finally:
                    sys.path.pop(0)
            result = {"ok": True, "inventory": observe_npm_workspace(
                Path(sys.argv[1]), lock_resolver=lock_resolver)}
    except GoalContractError as exc:
        result = {"ok": False, "error": {"code": exc.code}}
    except (OSError, ValueError, TypeError):
        result = {"ok": False, "error": {"code": "WORKSPACE_INPUT_UNAVAILABLE"}}
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > 512 * 1024:
        result = {"ok": False, "error": {"code": "WORKSPACE_OUTPUT_LIMIT"}}
        encoded = json.dumps(result)
    print(encoded)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
