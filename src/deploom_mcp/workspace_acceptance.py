"""Host-only bridge from an owned npm workspace resolver receipt to Core verification."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import sys
import time

from deploom_mcp.distribution import resolve_core_dir


def _candidate_manifests(project, resolver):
    owners = resolver["identity"]["candidateManifestSha256"]
    if not isinstance(owners, dict) or "." not in owners or not 2 <= len(owners) <= 256:
        raise ValueError("invalid resolver manifest inventory")
    from reparse_materialization import is_reparse
    from workspace_npm_obligations import OWNER_PATH

    result = {}
    for owner in owners:
        if owner != "." and (not OWNER_PATH.fullmatch(owner) or "node_modules" in owner.casefold().split("/")):
            raise ValueError("invalid workspace owner")
        member = project
        for part in owner.split("/") if owner != "." else ():
            member /= part
            if is_reparse(member) or getattr(member.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("workspace owner is a reparse point")
        path = member / "package.json"
        if is_reparse(path) or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("workspace manifest is a reparse point")
        with path.open("rb") as stream:
            result[owner] = stream.read(2 * 1024 * 1024 + 1)
    return result


async def audit_workspace_candidate(
    project: Path, *, baseline_lock: Path, resolver_dir: Path, expected_lock_sha256: str,
    expected_resolver_identity_sha256: str, registry: str, output_dir: Path, deadline_seconds: int = 120,
    use_system_proxy: bool = False, policy: dict | None = None,
) -> dict:
    """Run independent npm audit on the exact compiled candidate dependency inputs."""
    from deploom_mcp.audit_worker import read_bytes
    from deploom_mcp.blueprint import resolve_output_outside
    from deploom_mcp.dependency_audit import audit_dependencies, checked_registry
    from deploom_mcp.workspace_inventory import observe_npm_workspace
    from workspace_npm_obligations import compile_npm_workspace_obligations

    if (not re.fullmatch(r"[a-f0-9]{64}", expected_lock_sha256)
            or not re.fullmatch(r"[a-f0-9]{64}", expected_resolver_identity_sha256)):
        raise ValueError("INVALID_CANDIDATE_AUDIT_IDENTITY")
    if (resolver_dir != resolver_dir.resolve(strict=True) or not resolver_dir.is_dir()
            or resolve_output_outside(resolver_dir, output_dir) is None):
        raise ValueError("INVALID_CANDIDATE_AUDIT_ARTIFACT_PATH")
    registry = checked_registry(registry)
    if registry is None:
        raise ValueError("CANDIDATE_AUDIT_REGISTRY_REQUIRED")
    registry_sha256 = hashlib.sha256(registry.encode("utf-8")).hexdigest()
    resolver = json.loads(read_bytes(resolver_dir / "receipt.json"))
    resolver_lock = read_bytes(resolver_dir / "package-lock.json")
    receipt = resolver["receipt"]
    if (receipt.get("candidateLockSha256") != expected_lock_sha256
            or receipt.get("resolverIdentitySha256") != expected_resolver_identity_sha256
            or hashlib.sha256(resolver_lock).hexdigest() != expected_lock_sha256
            or resolver["identity"].get("registrySha256") != registry_sha256):
        raise ValueError("CANDIDATE_AUDIT_RESOLVER_IDENTITY_MISMATCH")
    before = read_bytes(baseline_lock)

    def bind(_digest, _rows, _packages, inputs):
        if inputs["package-lock.json"] != resolver_lock:
            raise ValueError("CANDIDATE_AUDIT_LOCK_MISMATCH")
        manifests = {Path(name).parent.as_posix(): raw for name, raw in inputs.items() if name.endswith("package.json")}
        compile_npm_workspace_obligations(before, resolver_lock, manifests, resolver)
        return {name: hashlib.sha256(raw).hexdigest() for name, raw in inputs.items()}

    files = observe_npm_workspace(project, plan_snapshot=bind)
    binding = {"files": files, "lockSha256": expected_lock_sha256, "registrySha256": registry_sha256,
               "resolverIdentitySha256": expected_resolver_identity_sha256}
    if policy is not None:
        from deploom_mcp.workspace_policy_request import validate_workspace_policy

        binding["policy"] = validate_workspace_policy(policy)
    return await audit_dependencies(
        project_path=str(project), package_manager="npm", registry=registry, output_dir=str(output_dir),
        deadline_seconds=deadline_seconds, use_system_proxy=use_system_proxy,
        _candidate_binding=binding,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    seal = modes.add_parser("source-key", help="Read the current workspace source fingerprint")
    seal.add_argument("project", type=Path)
    verify = modes.add_parser("verify", help="Run explicitly approved commands in a local workspace")
    verify.add_argument("project", type=Path)
    verify.add_argument("--expected-source-key", required=True)
    verify.add_argument("--expected-lock-sha256", required=True)
    verify.add_argument("--expected-resolver-identity-sha256", required=True)
    verify.add_argument("--baseline-lock", required=True, type=Path)
    verify.add_argument("--resolver-dir", required=True, type=Path)
    verify.add_argument("--cache", required=True, type=Path)
    verify.add_argument("--output", required=True, type=Path)
    verify.add_argument("--check", action="append", required=True)
    verify.add_argument("--deadline-seconds", type=int, default=180)
    verify.add_argument("--install", action="store_true", help="Permit one frozen offline npm ci in a clean workspace")
    verify.add_argument("--registry", help="Same trusted registry used for the resolver receipt; required with --install")
    status = modes.add_parser("verify-status", help="Read an existing workspace verification attempt")
    status.add_argument("output", type=Path)
    audit = modes.add_parser("audit-candidate", help="Audit the exact resolved npm candidate for known advisories")
    audit.add_argument("project", type=Path)
    audit.add_argument("--baseline-lock", required=True, type=Path)
    audit.add_argument("--resolver-dir", required=True, type=Path)
    audit.add_argument("--expected-lock-sha256", required=True)
    audit.add_argument("--expected-resolver-identity-sha256", required=True)
    audit.add_argument("--registry", required=True)
    audit.add_argument("--output-dir", required=True, type=Path)
    audit.add_argument("--deadline-seconds", type=int, default=120)
    audit.add_argument("--use-system-proxy", action="store_true")
    audit.add_argument("--policy-json", type=Path, help="Host-approved workspace Yellow/Green policy")
    args = parser.parse_args(argv)

    if args.mode == "verify-status":
        from deploom_mcp.workspace_verification_attempt import read_workspace_attempt

        try:
            result = read_workspace_attempt(args.output)
        except (OSError, ValueError):
            result = {"status": "WORKSPACE_ATTEMPT_UNPROVEN"}
        print(json.dumps(result))
        return 0 if result["status"] == "CURRENT_WORKSPACE_PROFILE_OBSERVED" else 1

    sys.path.insert(0, str(resolve_core_dir()))
    from source_snapshot import source_snapshot_fingerprint

    project = args.project.absolute()
    if project != project.resolve(strict=True) or not project.is_dir():
        parser.error("project must be a resolved local directory")
    if args.mode == "source-key":
        deadline = time.monotonic() + 30
        print(source_snapshot_fingerprint(project, remaining_budget=lambda: max(0, deadline - time.monotonic())))
        return 0
    if args.mode == "audit-candidate":
        from deploom_mcp.goal_contract import GoalContractError

        try:
            policy = None
            if args.policy_json is not None:
                from deploom_mcp.audit_worker import read_bytes

                policy = json.loads(read_bytes(args.policy_json))
                if not isinstance(policy, dict):
                    raise ValueError("INVALID_WORKSPACE_HEALTH_POLICY")
            result = asyncio.run(audit_workspace_candidate(
                project, baseline_lock=args.baseline_lock, resolver_dir=args.resolver_dir.absolute(),
                expected_lock_sha256=args.expected_lock_sha256,
                expected_resolver_identity_sha256=args.expected_resolver_identity_sha256,
                registry=args.registry, output_dir=args.output_dir.absolute(),
                deadline_seconds=args.deadline_seconds, use_system_proxy=args.use_system_proxy, policy=policy,
            ))
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            code = exc.code if isinstance(exc, GoalContractError) else (
                str(exc) if re.fullmatch(r"[A-Z_]+", str(exc)) else "CANDIDATE_AUDIT_INPUT_UNPROVEN")
            result = {"ok": False, "error": {"code": code}, "security": {"verdict": "UNKNOWN"}}
        status = result.get("candidateAudit", {}).get("status", "UNKNOWN")
        print(json.dumps({"status": status, "security": result.get("security", {}).get("verdict", "UNKNOWN"),
                          "finalPolicy": result.get("candidateAudit", {}).get("finalPolicy", "NOT_ASSESSED"),
                          "artifacts": result.get("artifacts"),
                          "error": (result.get("error") or {}).get("code")}))
        policy_passed = args.policy_json is None or result.get("candidateAudit", {}).get("finalPolicy") == "PASS"
        return 0 if status == "BOUND_SECURITY_AUDIT_COMPLETE" and policy_passed else 1

    if (not re.fullmatch(r"[a-f0-9]{32}", args.expected_source_key)
            or not all(re.fullmatch(r"[a-f0-9]{64}", digest) for digest in
                       (args.expected_lock_sha256, args.expected_resolver_identity_sha256))):
        parser.error("supply the approved source, lock and resolver identities")
    if not 1 <= args.deadline_seconds <= 900 or args.install != bool(args.registry):
        parser.error("use a deadline of 1-900 seconds and supply --registry exactly when using --install")
    output = args.output.absolute()
    if output.parent != output.parent.resolve(strict=True) or output.is_relative_to(project):
        parser.error("output must be outside the workspace in an existing resolved directory")

    from deploom_mcp.audit_worker import read_bytes
    from deploom_mcp.workspace_verification_attempt import run_workspace_attempt
    from materialized_workspace_verifier import verify_current_workspace
    from workspace_npm_obligations import compile_npm_workspace_obligations

    deadline = time.time() + args.deadline_seconds
    try:
        resolver = json.loads(read_bytes(args.resolver_dir / "receipt.json"))
        plan = compile_npm_workspace_obligations(
            read_bytes(args.baseline_lock), read_bytes(args.resolver_dir / "package-lock.json"),
            _candidate_manifests(project, resolver), resolver,
        )
        if (plan["approvedLockSha256"] != args.expected_lock_sha256
                or plan["resolverIdentitySha256"] != args.expected_resolver_identity_sha256):
            raise ValueError("WORKSPACE_VERIFICATION_APPROVAL_MISMATCH")
        receipt = run_workspace_attempt(
            output, project, source_key=args.expected_source_key, plan=plan, commands=args.check,
            install=args.install, verify=lambda profile_digest: verify_current_workspace(
                project, expected_source_key=args.expected_source_key, dependency_plan=plan,
                commands=args.check, cache_dir=args.cache, deadline=deadline, final_profile_digest=profile_digest,
                materialize=args.install, workspace_registry=args.registry,
            ),
        )
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        message = str(exc)
        print(message if re.fullmatch(r"[A-Z_]+", message) else
              "WORKSPACE_VERIFICATION_ADMISSION_UNPROVEN", file=sys.stderr)
        return 1
    print(receipt["status"])
    return 0 if receipt["status"] == "CURRENT_WORKSPACE_PROFILE_OBSERVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
