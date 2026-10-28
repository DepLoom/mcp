"""Read-only project audit boundary with an isolated, cancellable worker.

The experiment's planning-only server is unchanged. audit_server opts in to
this tool alongside get_upgrade_plan. Core and Skill pins are not modified.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from deploom_mcp.audit_evidence import AuditError

LOCKS = ("npm-shrinkwrap.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml")
MAX_REPORT_BYTES = 8 * 1024 * 1024


def checked_registry(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise AuditError("invalid-registry", "registry must be a nonempty HTTP(S) URL")
    try:
        url = urlsplit(value)
        valid = url.scheme in ("http", "https") and url.hostname and url.port != 0
    except ValueError:
        valid = False
    if not valid or url.username or url.password or url.query or url.fragment or any(c.isspace() for c in value):
        raise AuditError("invalid-registry", "registry must have no credentials, query, fragment or whitespace")
    if any(c in value for c in '\\"\'<>|&^%!'):
        raise AuditError("invalid-registry", "registry URL contains unsafe command characters")
    return value.rstrip("/")


def lock_root(project: Path) -> Path:
    """Find a canonical lock owner without walking past a Git/project boundary.

    Membership in the parent's workspace is checked again by the worker. A
    missing lock is not permission to resolve/install a new dependency tree.
    """
    for depth, folder in enumerate((project, *project.parents)):
        if any((folder / name).is_file() for name in LOCKS):
            if not (folder / "package.json").is_file():
                raise AuditError("manifest-missing", "lockfile owner has no package.json")
            return folder
        if (folder / ".git").exists() or depth >= 16:
            break
    raise AuditError("lockfile-missing", "audit requires an existing canonical lockfile; nothing will be installed")


def validate_request(project_path: str, *, package_manager: str = "auto",
                     registry: str | None = None, deadline_seconds: float = 120,
                     include_lag: bool = False, lag_months: int = 12) -> dict[str, Any]:
    if not isinstance(project_path, str) or not project_path or not Path(project_path).expanduser().is_absolute():
        raise AuditError("invalid-project", "project_path must be an absolute local directory")
    project = Path(project_path).expanduser().resolve()
    if not project.is_dir() or not (project / "package.json").is_file():
        raise AuditError("manifest-missing", "project_path must contain package.json")
    if package_manager not in ("auto", "npm", "yarn-classic", "yarn-berry"):
        raise AuditError("unsupported-manager", "supported managers: npm, yarn-classic, yarn-berry")
    if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float)) or not math.isfinite(deadline_seconds) or not 5 <= deadline_seconds <= 600:
        raise AuditError("invalid-deadline", "deadline_seconds must be finite and in [5, 600]")
    if not isinstance(include_lag, bool) or type(lag_months) is not int or lag_months not in (3, 6, 9, 12):
        raise AuditError("invalid-args", "include_lag must be boolean; lag_months must be 3, 6, 9 or 12")
    return {"project": str(project), "root": str(lock_root(project)),
            "packageManager": package_manager, "registry": checked_registry(registry),
            "deadlineSeconds": float(deadline_seconds), "includeLag": include_lag, "lagMonths": lag_months}


def compact_audit(report: dict[str, Any], report_path: str, *, max_items: int = 40,
                  max_bytes: int = 128 * 1024) -> dict[str, Any]:
    """Bound display only; never discard aggregate counters or uncertainty."""
    result = json.loads(json.dumps(report, ensure_ascii=False))
    security = result.get("security") or {}
    findings = security.pop("advisories", [])
    packages = security.pop("packages", [])
    lag = result.get("lag") or {}
    lag_rows = lag.pop("rows", [])
    commands = result.pop("commands", [])
    coverage = result.get("coverage") or {}
    manifests = coverage.pop("manifests", [])
    unresolved = coverage.pop("unresolvedEdges", [])
    result.pop("sourceFiles", None)
    result.pop("diagnostics", None)
    result["artifacts"] = {"report": report_path, "sha256": hashlib.sha256(Path(report_path).read_bytes()).hexdigest()}
    limit = max(0, max_items)
    while True:
        security["advisories"], security["packages"] = findings[:limit], packages[:limit]
        lag["rows"] = lag_rows[:limit]
        coverage["manifests"], coverage["unresolvedEdges"] = manifests[:limit], unresolved[:limit]
        result["commands"] = commands[:limit]
        result["security"], result["lag"], result["coverage"] = security, lag, coverage
        omitted = any(len(x) > limit for x in (findings, packages, lag_rows, manifests, unresolved, commands))
        result["projection"] = {"complete": not omitted, "requiresArtifactRead": omitted,
                                "totalAdvisories": len(findings), "inlineAdvisories": min(limit, len(findings)),
                                "totalPackages": len(packages), "totalCommands": len(commands),
                                "totalManifests": len(manifests), "totalUnresolvedEdges": len(unresolved),
                                "byteLimit": max_bytes}
        if len(json.dumps(result, ensure_ascii=False).encode()) <= max_bytes:
            return result
        if limit == 0:
            raise AuditError("response-too-large", "minimal audit summary exceeds the response budget")
        limit //= 2


def _core_provenance() -> tuple[Path, dict[str, Any]]:
    from deploom_mcp.distribution import resolve_core_dir, release_manifest_path
    core = resolve_core_dir()
    manifest = json.loads(release_manifest_path().read_text(encoding="utf-8"))
    inventory = manifest["core"]["files"]
    for relative, expected in inventory.items():
        file = core / relative
        if file.is_symlink() or not file.resolve().is_relative_to(core.resolve()) or not file.is_file():
            raise AuditError("core-integrity", "Core release resource missing or unsafe")
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise AuditError("core-integrity", "Core resource differs from its pinned release inventory")
    return core, {"coreRevision": manifest["core"]["revision"],
                  "coreInventorySha256": manifest["core"]["contentSha256"],
                  "manualAuditSha256": inventory["manual_dependency_audit.py"]}


async def audit_dependencies(*, project_path: str, package_manager: str = "auto",
                             registry: str | None = None, deadline_seconds: float = 120,
                             include_lag: bool = False, lag_months: int = 12,
                             output_dir: str | None = None) -> dict[str, Any]:
    """Audit an input snapshot, not the mutable checkout or installed runtime.

    Cancellation propagates; the shared async process runner kills the entire
    worker/npm/Yarn process tree. No synthetic successful report on timeout.
    """
    try:
        request = validate_request(project_path, package_manager=package_manager, registry=registry,
            deadline_seconds=deadline_seconds, include_lag=include_lag, lag_months=lag_months)
        from deploom_mcp.blueprint import resolve_output_outside
        root = Path(request["root"])
        if output_dir is not None and (not isinstance(output_dir, str) or not output_dir):
            raise AuditError("invalid-output", "output_dir must be a nonempty path")
        base = Path(os.path.abspath(os.path.expanduser(output_dir or os.environ.get("DEPLOOM_MCP_STATE_DIR") or str(Path.home() / ".deploom-mcp"))))
        if resolve_output_outside(root, base) is None:
            raise AuditError("output-inside-project", "audit state/output must not overlap the canonical workspace root")
        core, provenance = _core_provenance()
        run_id = "audit-" + uuid.uuid4().hex
        run_dir = base / "audits" / run_id
        run_dir.mkdir(parents=True, mode=0o700)
        report_path = run_dir / "audit.json"
        request.update({"coreDir": str(core.resolve()), "runId": run_id, "provenance": provenance})
        # Temp files include copied registry credentials; only sanitized evidence
        # survives this context, including on timeout and client cancellation.
        with tempfile.TemporaryDirectory(prefix="private-", dir=run_dir) as temporary:
            private = Path(temporary)
            request["privateDir"] = str(private)
            request_file = private / "request.json"
            request_file.write_text(json.dumps(request), encoding="utf-8")
            result_file = private / "result.json"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
            env["PYTHONIOENCODING"], env["PYTHONDONTWRITEBYTECODE"] = "utf-8", "1"
            env.pop("PYTHONSTARTUP", None)
            env.pop("NODE_OPTIONS", None)
            from deploom_mcp.process_runner import run_process_async
            outcome = await run_process_async(
                [sys.executable, "-B", "-m", "deploom_mcp.audit_worker", str(request_file), str(result_file)],
                cwd=str(private), env=env, timeout=request["deadlineSeconds"], max_output_bytes=65536,
            )
            if outcome.timed_out:
                raise AuditError("audit-timeout", "audit deadline exceeded; no clean result inferred")
            if not result_file.is_file() or result_file.stat().st_size > MAX_REPORT_BYTES:
                raise AuditError("audit-worker-failed", "audit worker did not publish a bounded report")
            report = json.loads(result_file.read_text(encoding="utf-8"))
            if not isinstance(report, dict) or report.get("runId") != run_id:
                raise AuditError("audit-worker-failed", "audit worker published an invalid report")
            report["workerExitCode"] = outcome.returncode
            if outcome.returncode != 0:
                report["ok"] = False
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return compact_audit(report, str(report_path))
    except AuditError as exc:
        return {"ok": False, "protocol": "dependency-audit", "schemaVersion": 1,
                "status": "ERROR", "error": {"code": exc.code, "message": str(exc)},
                "security": {"complete": False, "verdict": "UNKNOWN", "counts": None},
                "projectModified": False, "runtimeVerified": False}
    except (OSError, ValueError, KeyError) as exc:
        # Do not echo exception text: paths/config/CLI diagnostics can hold
        # credentials. Detailed evidence is sanitized by the worker.
        return {"ok": False, "protocol": "dependency-audit", "schemaVersion": 1,
                "status": "ERROR", "error": {"code": "audit-boundary-failed", "message": type(exc).__name__},
                "security": {"complete": False, "verdict": "UNKNOWN", "counts": None},
                "runtimeVerified": False}
