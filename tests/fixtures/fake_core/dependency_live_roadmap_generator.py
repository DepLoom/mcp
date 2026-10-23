#!/usr/bin/env python3
"""Deterministic FAKE planner core used ONLY by the DepLoom/mcp test-suite.

This file intentionally mimics the CLI shape of the real pinned core
(`dependency_live_roadmap_generator.py`) — it accepts the same headless argv
(`--artifacts-dir`, `--run-id`, ...) and publishes the same Draft artifacts
(`draft/result.json`, `draft/plan.json`, `draft/solver-components.json`,
`draft/prompt.md`). It is NOT a planning implementation: it writes a fixed,
contrived blueprint template so the boundary (blueprint.py/server.py) can be
tested end-to-end WITHOUT network access and WITHOUT depending on planner
behaviour.

Behaviour is controlled by environment variables (never by the project):

  FAKE_GENERATOR_MODE    one of ok / no-plan / bad-plan / broken-plan-json /
                         contract-violation / bad-hash / nonzero / slow
  FAKE_GENERATOR_ROWS    how many PROPOSED rows to emit (default 2)
  FAKE_GENERATOR_EXCLUDED comma-separated package names to emit as excluded
  FAKE_GENERATOR_PARTIAL set to 1 to publish status DRAFT_PARTIAL
  FAKE_GENERATOR_SOLVER  "manifest" or "file" controlling where solver
                         components come from (default manifest)

The manifest hashes are computed from the actual bytes written, so the
fail-closed hash verification normally passes; `bad-hash` deliberately breaks
the declared plan hash.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

MODE = os.environ.get("FAKE_GENERATOR_MODE", "ok")
ROWS = int(os.environ.get("FAKE_GENERATOR_ROWS", "2") or "2")
EXCLUDED = [x.strip() for x in os.environ.get("FAKE_GENERATOR_EXCLUDED", "").split(",") if x.strip()]
PARTIAL = os.environ.get("FAKE_GENERATOR_PARTIAL", "0") == "1"
SOLVER_SOURCE = os.environ.get("FAKE_GENERATOR_SOLVER", "manifest")


def _arg(name: str, default: str = "") -> str:
    argv = sys.argv
    for i, item in enumerate(argv):
        if item == name and i + 1 < len(argv):
            return argv[i + 1]
    return default


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def build_plan() -> dict:
    rows = []
    for k in range(1, ROWS + 1):
        package = f"pkg{k}"
        if package in EXCLUDED:
            rows.append({
                "project": "app", "package": package, "kind": "runtime",
                "requestedSpec": "^1.0.0", "current": "1.0.0", "target": None,
                "status": "excluded", "reason": "scope exclusion", "group": 0,
                "latest": "1.0.0", "candidateSearchTruncated": False,
                "peerCompatUnresolved": False, "breakingChanges": [],
                "migrationNotes": [],
            })
            continue
        rows.append({
            "project": "app", "package": package, "kind": "runtime",
            "requestedSpec": "^1.0.0", "current": "1.0.0", "target": "2.0.0",
            "status": "proposed", "reason": "major update", "group": 1,
            "latest": "2.0.0", "candidateSearchTruncated": False,
            "peerCompatUnresolved": False, "breakingChanges": ["breaking"],
            "migrationNotes": ["migrate"],
        })
    proposed = len(rows) - len([r for r in rows if r["status"] == "excluded"])
    return {
        "schemaVersion": 1,
        "projects": ["app"],
        "proposals": [{"project": "app", "rows": rows,
                       "health": {"total": len(rows), "known": proposed,
                                  "lag_ok_pct": 100.0, "critical": 0, "high": 0,
                                  "moderate": 0, "low": 0, "unknown": 0,
                                  "excluded": len(rows) - proposed,
                                  "insufficient_data": False,
                                  "postPlanTargetLevel": "yellow",
                                  "postPlanLagOkPct": 100.0, "postPlanGoal": "feasible"}}],
        "unknowns": [], "conflicts": [], "unresolved": [],
        "counts": {"total": len(rows), "proposed": proposed,
                   "excluded": len(rows) - proposed},
        "manifests": {}, "scan": {},
    }


def build_manifest(draft_dir: Path) -> dict:
    plan_raw = (draft_dir / "plan.json").read_bytes() if (draft_dir / "plan.json").is_file() else b""
    prompt_raw = (draft_dir / "prompt.md").read_bytes() if (draft_dir / "prompt.md").is_file() else b""
    return {
        "schemaVersion": 2,
        "status": "DRAFT_PARTIAL" if PARTIAL else "DRAFT_READY",
        "runId": _arg("--run-id", "fake-run"),
        "workspaceId": "ws-app",
        "projectId": "app",
        "mode": "draft",
        "generatedAt": "2026-10-23T00:00:00Z",
        "elapsedMs": 5,
        "deadline": {"deadlineSeconds": 60, "remainingMs": 0},
        "policyHash": sha256_bytes(b"policy-snapshot"),
        "verificationStatus": "NOT_VERIFIED",
        "authority": "PLANNING_ONLY",
        "compatibility": "UNKNOWN",
        "partialReason": "deadline budget exhausted" if PARTIAL else None,
        "summary": "fake plan ok",
        "metadata": {
            "metadataKnown": len(plan_raw), "metadataTotal": 1,
            "securityKnown": 0, "securityTotal": 0, "insufficientData": False,
            "processed": ROWS, "processedTotal": ROWS,
            "postPlanGoal": "feasible",
        },
        "inputHashes": {"package.json": sha256_bytes(b"{}")},
        "inputFilesByProject": {"app": [{"name": "./package.json"}]},
        "hashes": {
            "plan": sha256_bytes(plan_raw) if plan_raw else None,
            "prompt": sha256_bytes(prompt_raw) if prompt_raw else None,
        },
        "artifacts": {
            "manifest": str(draft_dir / "result.json"),
            "plan": str(draft_dir / "plan.json"),
            "prompt": str(draft_dir / "prompt.md"),
            "summary": str(draft_dir / "summary.md"),
            "solverComponents": str(draft_dir / "solver-components.json"),
        },
        "solverComponents": [{"solver": "z3", "component": 1}]
        if SOLVER_SOURCE == "manifest" else [],
    }


def main() -> int:
    artifacts_dir = Path(_arg("--artifacts-dir", "artifacts"))
    run_id = _arg("--run-id", "fake-run")
    draft_dir = artifacts_dir / "runs" / run_id / "draft"

    if MODE == "slow":
        time.sleep(120)
        return 0

    if MODE == "nonzero":
        print("fake core: nothing to say", file=sys.stderr)
        return 3

    plan = build_plan()
    if MODE == "no-plan":
        _write(draft_dir / "result.json", json.dumps(build_manifest(draft_dir), indent=2))
        return 0
    if MODE == "bad-plan":
        _write(draft_dir / "plan.json", json.dumps({"counts": {}}, indent=2))
        _write(draft_dir / "result.json", json.dumps(build_manifest(draft_dir), indent=2))
        return 0
    if MODE == "broken-plan-json":
        _write(draft_dir / "plan.json", "this is not json")
        _write(draft_dir / "result.json", json.dumps(build_manifest(draft_dir), indent=2))
        return 0

    _write(draft_dir / "plan.json", json.dumps(plan, indent=2))
    _write(draft_dir / "prompt.md", "# draft prompt\n")
    _write(draft_dir / "summary.md", "# summary\n")
    envelope = {"runId": run_id, "workspaceId": "ws-app", "projectId": "app",
                "mode": "draft", "generatedAt": "2026-10-23T00:00:00Z",
                "solver": "z3", "authoritative": True,
                "components": [{"solver": "z3", "component": 1}]
                if SOLVER_SOURCE == "file" else []}
    _write(draft_dir / "solver-components.json", json.dumps(envelope, indent=2))

    manifest = build_manifest(draft_dir)
    if MODE == "contract-violation":
        manifest["verificationStatus"] = "VERIFIED"
        manifest["authority"] = "ACTIONABLE"
    if MODE == "bad-hash":
        manifest["hashes"]["plan"] = "deadbeef" * 8

    manifest_raw = json.dumps(manifest, indent=2)
    _write(draft_dir / "result.json", manifest_raw)
    print("fake core: published draft artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
