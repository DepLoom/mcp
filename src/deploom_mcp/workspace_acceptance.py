"""Host-only bridge from an owned npm workspace resolver receipt to Core verification."""
from __future__ import annotations

import argparse
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    seal = modes.add_parser("source-key", help="Read the current workspace source fingerprint")
    seal.add_argument("project", type=Path)
    verify = modes.add_parser("verify", help="Run explicitly approved commands in a local workspace")
    verify.add_argument("project", type=Path)
    verify.add_argument("--expected-source-key", required=True)
    verify.add_argument("--baseline-lock", required=True, type=Path)
    verify.add_argument("--resolver-dir", required=True, type=Path)
    verify.add_argument("--cache", required=True, type=Path)
    verify.add_argument("--output", required=True, type=Path)
    verify.add_argument("--check", action="append", required=True)
    verify.add_argument("--deadline-seconds", type=int, default=180)
    verify.add_argument("--install", action="store_true", help="Permit one frozen offline npm ci in a clean workspace")
    verify.add_argument("--registry", help="Same trusted registry used for the resolver receipt; required with --install")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(resolve_core_dir()))
    from source_snapshot import source_snapshot_fingerprint

    project = args.project.absolute()
    if project != project.resolve(strict=True) or not project.is_dir():
        parser.error("project must be a resolved local directory")
    if args.mode == "source-key":
        deadline = time.monotonic() + 30
        print(source_snapshot_fingerprint(project, remaining_budget=lambda: max(0, deadline - time.monotonic())))
        return 0
    if not re.fullmatch(r"[a-f0-9]{32}", args.expected_source_key):
        parser.error("expected-source-key must be the approved source fingerprint")
    if not 1 <= args.deadline_seconds <= 900 or args.install != bool(args.registry):
        parser.error("use a deadline of 1-900 seconds and supply --registry exactly when using --install")
    output = args.output.absolute()
    if output.parent != output.parent.resolve(strict=True) or output.is_relative_to(project):
        parser.error("output must be outside the workspace in an existing resolved directory")

    from materialized_workspace_verifier import verify_current_workspace
    from workspace_npm_obligations import compile_npm_workspace_obligations

    deadline = time.time() + args.deadline_seconds
    resolver = json.loads((args.resolver_dir / "receipt.json").read_bytes())
    plan = compile_npm_workspace_obligations(
        args.baseline_lock.read_bytes(), (args.resolver_dir / "package-lock.json").read_bytes(),
        _candidate_manifests(project, resolver), resolver,
    )
    profile = {"commands": args.check, "materialize": args.install, "obligations": plan["obligationPlanDigest"]}
    profile_digest = hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with output.open("x", encoding="utf-8") as stream:
        try:
            receipt = verify_current_workspace(
                project, expected_source_key=args.expected_source_key, dependency_plan=plan,
                commands=args.check, cache_dir=args.cache, deadline=deadline, final_profile_digest=profile_digest,
                materialize=args.install, workspace_registry=args.registry,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            json.dump({"status": "WORKSPACE_VERIFICATION_UNPROVEN",
                       "error": str(exc), "operations": getattr(exc, "operation_observations", ())}, stream, indent=2)
            print("Workspace verification did not pass. See the local receipt.", file=sys.stderr)
            return 1
        json.dump(receipt, stream, indent=2)
    print(receipt["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
