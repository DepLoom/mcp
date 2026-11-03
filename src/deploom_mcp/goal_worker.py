"""Bounded caller-owned process for read-only goal/source identity. No migration authority."""
from __future__ import annotations
import hashlib
import json
import shutil
import sys
from pathlib import Path
from deploom_mcp.distribution import release_manifest_path, resolve_core_dir
from deploom_mcp.goal_contract import GoalContractError, bind_goal, canonical_digest, validate_goal, _roots, npm_lock_format
from deploom_mcp.process_runner import run_process
import os

def observe_goal_source(project: Path, policy_digest: str, *, exact_goal: dict | None = None) -> dict:
    """Source identity only, independent of migration-target version grammar.

    No assignment, verification authority or authorization is returned. Legacy
    exact planning passes its validated goal to preserve early target guards.
    """
    if not isinstance(policy_digest,str) or len(policy_digest)!=64 or any(c not in '0123456789abcdef' for c in policy_digest):
        raise GoalContractError('INVALID_IDENTITY_POLICY_DIGEST')
    if sys.version_info[:2] not in {(3, 12), (3, 13)}:
        raise GoalContractError("UNSUPPORTED_PYTHON_RUNTIME")
    if not project.is_absolute() or project.is_symlink():
        raise GoalContractError("INVALID_PROJECT_PATH")
    project = project.resolve(strict=True)
    manifest_path, lock_path = project / "package.json", project / "package-lock.json"
    manifest_bytes, lock_bytes = manifest_path.read_bytes(), lock_path.read_bytes()
    manifest, lock = json.loads(manifest_bytes), json.loads(lock_bytes)
    if "workspaces" in manifest or manifest.get("packageManager", "npm").split("@")[0] != "npm":
        raise GoalContractError("UNSUPPORTED_WORKSPACE_OR_MANAGER")
    version=npm_lock_format(lock)
    if version==3:
        packages=lock.get('packages')
        root=packages.get('') if isinstance(packages,dict) else None
        if (not isinstance(root,dict) or _roots(root)!=_roots(manifest)
                or any(key in manifest and manifest[key]!=root.get(key) for key in ('name','version'))):
            raise GoalContractError('MANIFEST_LOCK_ROOT_MISMATCH')
    roots = _roots(manifest)
    for name in (exact_goal["exactTargets"] if exact_goal is not None else {}):
        if name not in roots or "node_modules/" + name not in lock.get("packages", {}):
            raise GoalContractError("TARGET_NOT_DECLARED", name)
    core = resolve_core_dir()
    release = json.loads(release_manifest_path().read_text(encoding="utf-8"))
    for name, expected in release["core"]["files"].items():
        file = core / name
        if not file.resolve().is_relative_to(core.resolve()) or file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise GoalContractError("CORE_INTEGRITY_MISMATCH")
    if exact_goal is not None and exact_goal["goalKind"] == "baseline-observation" and any(lock["packages"]["node_modules/"+n].get("version")!=v for n,v in exact_goal["exactTargets"].items()):
        raise GoalContractError("BASELINE_OBJECTIVE_MISMATCH")
    sys.path.insert(0, str(core))
    from source_snapshot import source_snapshot_fingerprint
    before = source_snapshot_fingerprint(project)
    from draft_npm_preflight import _npm_command
    try:
        node, npm_entry = _npm_command()
        npm = Path(npm_entry)
    except OSError:
        raise GoalContractError("TOOLCHAIN_UNAVAILABLE")
    versions = []
    for command in ([node, "--version"], [node, str(npm), "--version"]):
        result = run_process(command, cwd=str(project), env=dict(os.environ), timeout=10)
        if result.returncode or result.timed_out:
            raise GoalContractError("TOOLCHAIN_UNAVAILABLE")
        versions.append(result.stdout.strip())
    if manifest_bytes != manifest_path.read_bytes() or lock_bytes != lock_path.read_bytes() or before != source_snapshot_fingerprint(project):
        raise GoalContractError("SOURCE_CHANGED_DURING_BINDING")
    package = Path(__file__).parent
    implementation = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.glob("*.py"))}
    implementation["releaseManifest"] = hashlib.sha256(release_manifest_path().read_bytes()).hexdigest()
    identity = {"sourceDigest": canonical_digest({"coreSourceSnapshotKey": before}),
        "manifestDigest": hashlib.sha256(manifest_bytes).hexdigest(), "lockDigest": hashlib.sha256(lock_bytes).hexdigest(),
        "toolchainDigest": canonical_digest({"node": versions[0], "npm": versions[1], "python":sys.version, "nodeExecutableSha256": hashlib.sha256(Path(node).read_bytes()).hexdigest(), "npmCliSha256": hashlib.sha256(npm.read_bytes()).hexdigest()}),
        "policyDigest": policy_digest, "implementationDigest": canonical_digest(implementation)}
    return {"identity":identity,"manifest":manifest,"lock":lock,"roots":roots}


def prepare_goal_plan(project: Path, goal: dict) -> dict:
    goal = validate_goal(goal)
    observation=observe_goal_source(project,goal['policyDigest'],exact_goal=goal)
    identity,lock,roots=observation['identity'],observation['lock'],observation['roots']
    binding = bind_goal(goal, identity)
    return {"ok": True, "kind": "exact-goal-plan", "planId": binding["bindingDigest"], "binding": binding,
        "assignment": goal["exactTargets"], "lockedTargets": {name: lock["packages"]["node_modules/"+name].get("version") for name in goal["exactTargets"]}, "heldRootCount": len(roots)-len(goal["mutablePackages"]),
        "modelStatus": "NOT_EVALUATED", "verification": "NOT_VERIFIED", "authority": "PLANNING_ONLY",
        "health": {"policy": "REPORT_ONLY", "observation": "NOT_EVALUATED"},
        "hardPolicyStatus": "NOT_EVALUATED", "executionReady": False,
        "legacyPublication": "NOT_PERFORMED",
        "note": "Exact maintenance proposal only. Hard policy admission, transitive scope and all required checks remain mandatory; no resolver/install/runtime evidence was produced."}

def main() -> int:
    try:
        if len(sys.argv) != 3 or len(sys.argv[2]) > 65536:
            raise GoalContractError("INVALID_GOAL_REQUEST")
        result = prepare_goal_plan(Path(sys.argv[1]), json.loads(sys.argv[2]))
    except GoalContractError as exc:
        result = {"ok": False, "error": {"code": exc.code, "detail": str(exc)}}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"ok": False, "error": {"code": "GOAL_INPUT_UNAVAILABLE", "detail": type(exc).__name__}}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["ok"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
