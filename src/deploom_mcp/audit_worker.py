"""Isolated audit worker; invokes package managers only in disposable inputs.

Never runs install, audit fix, scripts or project-provided Yarn plugins. The
parent owns the process group and kills all descendants on deadline/cancel.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deploom_mcp.audit_evidence import AuditError, normalize_audit
from deploom_mcp.dependency_audit import LOCKS, checked_registry

MAX_FILE = 16 * 1024 * 1024
MAX_STREAM = 4 * 1024 * 1024
CONFIG_NAMES = (".npmrc", ".yarnrc", ".yarnrc.yml", "lerna.json")
PRUNE = {"node_modules", ".git", ".yarn", ".deploom", ".dependency-roadmap", ".venv", "dist", "build"}


def digest_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_bytes(path: Path) -> bytes:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise AuditError("unsafe-input", "dependency input is a symlink/junction")
    if path.stat().st_size > MAX_FILE:
        raise AuditError("input-too-large", "dependency input exceeds 16 MiB")
    return path.read_bytes()


def load_json(raw: bytes) -> dict:
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise AuditError("invalid-input", "package/lock configuration must be an object")
    return value


def collect_inputs(root: Path, requested: Path) -> tuple[dict[str, bytes], list[str], list[str]]:
    from workspace_glob import match_workspace_pattern, unsupported_workspace_pattern
    inputs = {"package.json": read_bytes(root / "package.json")}
    package = load_json(inputs["package.json"])
    for name in (*LOCKS, *CONFIG_NAMES):
        if (root / name).exists():
            inputs[name] = read_bytes(root / name)
    patterns = package.get("workspaces", [])
    if isinstance(patterns, dict):
        patterns = patterns.get("packages", [])
    issues: list[str] = []
    if not patterns and "lerna.json" in inputs:
        patterns = load_json(inputs["lerna.json"]).get("packages", [])
        if patterns:
            issues.append("LERNA_ONLY_WORKSPACES: native audit may not cover Lerna-only members")
    if not isinstance(patterns, list) or any(not isinstance(p, str) for p in patterns):
        raise AuditError("workspace-config-invalid", "workspace patterns must be strings")
    for pattern in patterns:
        if unsupported_workspace_pattern(pattern) or ".." in pattern.split("/") or pattern.startswith("/"):
            raise AuditError("workspace-config-unsupported", "unsupported workspace pattern")
    manifests = ["package.json"]
    errors: list[OSError] = []
    if patterns:
        visited = 0
        for folder, dirs, files in os.walk(root, followlinks=False, onerror=errors.append):
            visited += 1
            if visited > 2048:
                raise AuditError("workspace-limit", "workspace traversal exceeds 2048 directories")
            for name in list(dirs):
                child = Path(folder) / name
                if name in PRUNE:
                    dirs.remove(name)
                elif child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
                    dirs.remove(name)
                    issues.append("WORKSPACE_LINK_SKIPPED:" + child.relative_to(root).as_posix())
            relative = Path(folder).relative_to(root).as_posix()
            if relative == "." or "package.json" not in files:
                continue
            included = any(match_workspace_pattern(relative, p) for p in patterns if not p.startswith("!"))
            excluded = any(match_workspace_pattern(relative, p[1:]) for p in patterns if p.startswith("!"))
            if included and not excluded:
                name = relative + "/package.json"
                inputs[name] = read_bytes(root / name)
                manifests.append(name)
                if len(manifests) > 256:
                    raise AuditError("workspace-limit", "workspace exceeds 256 manifests")
        if errors:
            raise AuditError("workspace-unreadable", "workspace discovery could not read all paths")
    requested_manifest = (requested / "package.json").relative_to(root).as_posix()
    if requested_manifest not in manifests:
        raise AuditError("workspace-not-member", "requested project is not a member of the lockfile owner")
    if sum(map(len, inputs.values())) > 64 * 1024 * 1024:
        raise AuditError("input-too-large", "combined audit inputs exceed 64 MiB")
    return inputs, sorted(manifests), issues


def detect_manager(inputs: dict[str, bytes], requested: str) -> str:
    package = load_json(inputs["package.json"])
    declared = str(package.get("packageManager") or "")
    if "pnpm-lock.yaml" in inputs or declared.startswith("pnpm@"):
        raise AuditError("unsupported-manager", "pnpm is not supported by this audit tool")
    npm = any(name in inputs for name in ("npm-shrinkwrap.json", "package-lock.json"))
    yarn = "yarn.lock" in inputs
    if npm and yarn:
        raise AuditError("ambiguous-lockfiles", "both npm and Yarn lockfiles are present; select one canonical project")
    if yarn:
        berry = bool(re.search(rb"(?m)^__metadata:\s*$", inputs["yarn.lock"]))
        detected = "yarn-berry" if berry else "yarn-classic"
        if declared.startswith("npm@"):
            raise AuditError("manager-lock-mismatch", "npm declaration conflicts with yarn.lock")
        if declared.startswith("yarn@"):
            match = re.match(r"yarn@(\d+)", declared)
            if not match or (int(match[1]) >= 2) != berry:
                raise AuditError("manager-lock-mismatch", "Yarn version conflicts with lockfile format")
    elif npm:
        detected = "npm"
        if declared.startswith("yarn@"):
            raise AuditError("manager-lock-mismatch", "Yarn declaration conflicts with npm lock")
    else:
        raise AuditError("lockfile-missing", "a supported lockfile is required")
    if requested not in ("auto", detected):
        raise AuditError("manager-lock-mismatch", "requested manager disagrees with canonical inputs")
    return detected


def _digest(inputs: dict[str, bytes]) -> str:
    inventory = {name: digest_bytes(raw) for name, raw in sorted(inputs.items())}
    return digest_bytes(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode())


class Runner:
    def __init__(self, private: Path, env: dict[str, str]):
        self.env, self.private = env, private
        self.commands: list[dict] = []
        self.secrets = {v for k, v in env.items() if re.search(r"token|password|secret|auth", k, re.I) and len(v) >= 4}
        self._lock = threading.Lock()

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): self.redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if not isinstance(value, str):
            return value
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", value)
        value = re.sub(r"(?i)(bearer\s+|(?:_authToken|npmAuthToken|password)\s*[:=]\s*)[^\s,\"']+", r"\1[REDACTED]", value)
        return value

    def run(self, command: list[str], cwd: Path, env=None, timeout=None) -> tuple[int, str, str]:
        # No shell and no arbitrary commands. Core.check_lag is permitted to
        # use npm view; the security paths use audit only, never fix/install.
        if len(command) < 2 or command[0] not in ("npm", "yarn"):
            raise AuditError("command-not-allowed", "unexpected audit subprocess")
        allowed = command[1] == "--version" or (command[0] == "npm" and command[1] in ("audit", "view")) or (command[:3] == ["yarn", "npm", "audit"])
        if not allowed or "fix" in command or "install" in command:
            raise AuditError("command-not-allowed", "audit may not install or repair dependencies")
        if not cwd.resolve().is_relative_to(self.private.resolve()):
            raise AuditError("unsafe-cwd", "subprocess directory is not isolated")
        executable = shutil.which(command[0], path=self.env.get("PATH"))
        if not executable:
            raise AuditError("executable-missing", f"{command[0]} is not available on PATH")
        argv = [executable, *command[1:]]
        if os.name == "nt" and any(re.search(r'[&|<>^%!\r\n"]', arg) for arg in argv):
            raise AuditError("unsafe-argument", "command contains unsafe Windows launcher characters")
        started = time.monotonic()
        # The worker inherits its parent's process group. Do NOT detach native
        # children into new groups: parent cancellation must kill them as well.
        proc = subprocess.Popen(argv, cwd=cwd, env=self.env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = [bytearray(), bytearray()]
        overflow = [False, False]
        def drain(stream, index):
            try:
                while chunk := stream.read(65536):
                    room = MAX_STREAM - len(output[index])
                    output[index].extend(chunk[:max(0, room)])
                    if len(chunk) > room:
                        overflow[index] = True
            finally:
                stream.close()
        threads = [threading.Thread(target=drain, args=(stream, i), daemon=True)
                   for i, stream in enumerate((proc.stdout, proc.stderr))]
        for thread in threads:
            thread.start()
        code = proc.wait()  # bounded by parent's whole-worker deadline
        for thread in threads:
            thread.join()
        out, err = (bytes(raw).decode("utf-8", errors="replace") for raw in output)
        with self._lock:
            self.commands.append({"argv": self.redact(command), "exitCode": code,
                "durationSeconds": round(time.monotonic() - started, 4),
                "stdout": self.redact(out[:32768]), "stderr": self.redact(err[:8192]),
                "captureTruncated": len(out) > 32768 or len(err) > 8192, "outputTruncated": any(overflow)})
        if any(overflow):
            raise AuditError("audit-output-too-large", "native output exceeded the evidence capture limit")
        return code, out, err


def configure(snapshot: Path, inputs: dict[str, bytes], runner: Runner, requested_registry: str | None, manager: str) -> tuple[str, str]:
    """Copy only declarative registry/TLS/auth settings; never Yarn code/plugins."""
    import yaml
    npm_lines: list[str] = []
    npm_registry = runner.env.get("npm_config_registry") or runner.env.get("NPM_CONFIG_REGISTRY")
    paths = [Path.home() / ".npmrc"]
    configured_user = os.environ.get("NPM_CONFIG_USERCONFIG") or os.environ.get("npm_config_userconfig")
    if configured_user:
        paths = [Path(configured_user).expanduser()]
    npm_sources = [p.read_bytes() for p in paths if p.is_file()]
    if ".npmrc" in inputs:
        npm_sources.append(inputs[".npmrc"])
    def expand(text):
        return re.sub(r"\$\{([^}]+)\}", lambda m: runner.env.get(m[1], m[0]), str(text))
    for raw in npm_sources:
        for line in raw.decode("utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith((";", "#")) or "=" not in line:
                continue
            key, value = (v.strip() for v in line.split("=", 1))
            permitted = key in ("registry", "always-auth", "cafile", "strict-ssl", "proxy", "https-proxy") or (key.startswith("@") and key.endswith(":registry")) or (key.startswith("//") and re.search(r":(_authToken|_auth|_password|username|always-auth)$", key))
            if permitted:
                npm_lines.append(f"{key}={value}")
                if key == "registry":
                    npm_registry = expand(value)
                if re.search(r"auth|password", key, re.I) and len(expand(value)) >= 4:
                    runner.secrets.add(expand(value))
    (snapshot / ".npmrc").write_text("\n".join(npm_lines) + "\n", encoding="utf-8")
    empty = runner.private / "empty-npmrc"
    empty.write_text("", encoding="utf-8")
    runner.env.update({"NPM_CONFIG_USERCONFIG": str(empty), "NPM_CONFIG_GLOBALCONFIG": str(empty),
        "npm_config_userconfig": str(empty), "npm_config_globalconfig": str(empty)})
    yarn_config: dict = {}
    allowed = {"npmRegistryServer", "npmAuditRegistry", "npmScopes", "npmRegistries", "npmAlwaysAuth", "npmAuthIdent", "npmAuthToken", "enableStrictSsl", "httpsCaFilePath", "httpsCertFilePath", "httpsKeyFilePath", "httpProxy", "httpsProxy", "unsafeHttpWhitelist"}
    yarn_sources = []
    global_yarn = Path.home() / ".yarnrc.yml"
    if global_yarn.is_file():
        yarn_sources.append(global_yarn.read_bytes())
    if ".yarnrc.yml" in inputs:
        yarn_sources.append(inputs[".yarnrc.yml"])
    for raw in yarn_sources:
        data = yaml.safe_load(raw.decode("utf-8-sig")) or {}
        if not isinstance(data, dict):
            raise AuditError("invalid-yarn-config", "Yarn configuration must be a mapping")
        for key in allowed & data.keys():
            yarn_config[key] = data[key]
    def auth_values(value, key=""):
        if isinstance(value, dict):
            for k, v in value.items():
                auth_values(v, str(k))
        elif isinstance(value, str) and re.search(r"token|auth|password", key, re.I) and len(expand(value)) >= 4:
            runner.secrets.add(expand(value))
    auth_values(yarn_config)
    default = "https://registry.npmjs.org"
    if manager == "yarn-berry":
        metadata_registry = requested_registry or runner.env.get("YARN_NPM_REGISTRY_SERVER") or yarn_config.get("npmRegistryServer") or npm_registry or default
        registry = requested_registry or runner.env.get("YARN_NPM_AUDIT_REGISTRY") or yarn_config.get("npmAuditRegistry") or metadata_registry
    else:
        registry = metadata_registry = requested_registry or npm_registry or default
        # Classic registry can also live in .yarnrc. Do not silently send its
        # dependency inventory to the public fallback instead.
        if not requested_registry and ".yarnrc" in inputs:
            match = re.search(r'(?m)^\s*registry\s+[\"\']?([^\s\"\']+)', inputs[".yarnrc"].decode("utf-8-sig"))
            if match:
                registry = metadata_registry = expand(match[1])
    registry, metadata_registry = checked_registry(expand(registry)), checked_registry(expand(metadata_registry))
    yarn_config.update({"npmRegistryServer": metadata_registry, "npmAuditRegistry": registry,
        "enableScripts": False, "enableTelemetry": False, "enableColors": False,
        "enableProgressBars": False, "ignorePath": True, "httpRetry": 0})
    # Isolated RC filename prevents inherited ancestor/global Yarn plugins.
    rc_name = ".deploom-audit.yarnrc.yml"
    (snapshot / rc_name).write_text(yaml.safe_dump(yarn_config), encoding="utf-8")
    runner.env.update({"YARN_RC_FILENAME": rc_name, "YARN_IGNORE_PATH": "1", "YARN_ENABLE_SCRIPTS": "0",
        "YARN_ENABLE_TELEMETRY": "0", "YARN_ENABLE_COLORS": "0", "YARN_ENABLE_PROGRESS_BARS": "0",
        "YARN_NPM_REGISTRY_SERVER": metadata_registry, "YARN_NPM_AUDIT_REGISTRY": registry,
        "COREPACK_ENABLE_NETWORK": "0", "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"})
    return registry, metadata_registry


def execute(request: dict) -> dict:
    started = time.monotonic()
    private, root, project = (Path(request[k]) for k in ("privateDir", "root", "project"))
    sys.path.insert(0, request["coreDir"])
    import manual_dependency_audit as manual
    env = os.environ.copy()
    for key in list(env):
        lower = key.lower()
        if key in ("NODE_OPTIONS", "NODE_ENV") or (key.startswith("YARN_") and not any(word in key for word in ("AUTH", "REGISTRY"))) or lower.startswith("npm_config_") and not any(word in lower for word in ("token", "auth", "registry", "proxy", "cafile")):
            env.pop(key, None)
    env.update({"npm_config_ignore_scripts": "true", "NPM_CONFIG_IGNORE_SCRIPTS": "true",
                "npm_config_cache": str(private / "npm-cache"), "NPM_CONFIG_CACHE": str(private / "npm-cache"),
                "npm_config_update_notifier": "false", "npm_config_fetch_retries": "0", "CI": "1"})
    runner = Runner(private, env)
    report: dict[str, Any] = {"ok": False, "protocol": "dependency-audit", "schemaVersion": 1,
        "runId": request["runId"], "status": "ERROR", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scope": {"kind": "LOCKFILE_ROOT", "requestedProject": str(project), "root": str(root)},
        "provenance": request["provenance"], "projectModified": False, "runtimeVerified": False,
        "security": {"complete": False, "verdict": "UNKNOWN", "counts": None},
        "lag": {"requested": request["includeLag"], "complete": False, "status": "NOT_RUN"}}
    try:
        inputs, manifests, issues = collect_inputs(root, project)
        report["sourceFiles"] = {k: digest_bytes(v) for k, v in inputs.items()}
        report["sourceInputSha256"] = _digest(inputs)
        manager = detect_manager(inputs, request["packageManager"])
        report["packageManager"] = manager
        snapshot = private / "project"
        snapshot.mkdir()
        for name, raw in inputs.items():
            path = snapshot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            # Config is rebuilt from declarative allowlists below.
            if name not in (".npmrc", ".yarnrc", ".yarnrc.yml"):
                path.write_bytes(raw)
        registry, metadata_registry = configure(snapshot, inputs, runner, request["registry"], manager)
        report["registry"] = {"audit": registry, "metadata": metadata_registry, "fallbackUsed": False}
        report["coverage"] = {"manifests": manifests, "complete": not issues, "unresolvedEdges": issues,
            "basis": "CANONICAL_LOCKFILE", "installedNodeModulesExamined": False,
            "manifestLockConsistency": "NOT_VERIFIED"}
        # Never materialize external local packages/links or run any package
        # code. Report a capability limit rather than an apparently clean tree.
        for name in manifests:
            manifest = load_json(inputs[name])
            for section in manual.AUDIT_ROOT_SECTIONS:
                for package, spec in (manifest.get(section) or {}).items():
                    if not re.fullmatch(r"(?:@[A-Za-z0-9_.~-]+/)?[A-Za-z0-9_.~-]+", package):
                        raise AuditError("invalid-package-name", "unsupported package identifier")
                    if str(spec).startswith(("file:", "link:", "portal:", "git:", "git+", "http:", "https:")):
                        issues.append(f"NON_REGISTRY_DEPENDENCY:{name}:{package}")
        manual.run = runner.run
        inventory = None
        path_map = {}
        if manager == "yarn-classic":
            inventories = [manual.canonical_yarn_inventory((snapshot / name).parent) for name in manifests]
            inventory = {"pairs": sorted(set(tuple(p) for data in inventories for p in data["pairs"])),
                "directPackages": sorted(set(p for data in inventories for p in data["directPackages"])),
                "unresolvedEdges": sorted(set(p for data in inventories for p in data["unresolvedEdges"])),
                "complete": all(data["complete"] for data in inventories)}
            issues.extend(inventory["unresolvedEdges"])
            report["coverage"]["basis"] = "YARN_LOCK_EXACT_PACKAGE_VERSION_INVENTORY"
            report["coverage"]["packageVersionPairs"] = len(inventory["pairs"])
            audit_cwd = private / "inventory"
            audit_cwd.mkdir()
            path_map = manual._write_yarn_inventory_lock(audit_cwd, inventory)
            shutil.copyfile(snapshot / ".npmrc", audit_cwd / ".npmrc")
        else:
            audit_cwd = snapshot
        executable = "yarn" if manager == "yarn-berry" else "npm"
        code, version, _ = runner.run([executable, "--version"], audit_cwd)
        match = re.fullmatch(r"(\d+)\.\d+\.\d+(?:[-+][\w.-]+)?", version.strip())
        if code != 0 or not match or (executable == "npm" and int(match[1]) < 7) or (executable == "yarn" and int(match[1]) < 2):
            raise AuditError("unsupported-cli-version", "require npm >= 7 or a prepared Yarn Berry >= 2; no CLI will be downloaded")
        report["provenance"]["auditCli"] = {"name": executable, "version": version.strip()}
        if manager == "yarn-berry":
            command = ["yarn", "npm", "audit", "--all", "--recursive", "--json"]
            if int(match[1]) >= 4:
                command.append("--no-deprecations")
        else:
            command = ["npm", "audit", "--json", "--package-lock-only", "--ignore-scripts", "--audit-level=info",
                       "--include=dev", "--include=optional", "--include=peer", "--registry", registry]
            if manager == "yarn-classic":
                command.append("--legacy-peer-deps")
        code, stdout, stderr = runner.run(command, audit_cwd)
        security = normalize_audit(stdout, code, manager)
        security["auditExitCode"] = code
        if inventory is not None:
            for item in security["packages"]:
                mapped = [path_map[path] for path in item["nodes"] if path in path_map]
                item["versions"] = sorted({p["version"] for p in mapped})
                item["nodes"] = [f'yarn.lock:{p["name"]}@{p["version"]}' for p in mapped]
                item["fixAvailable"] = "NOT_EVALUATED"
            security["counts"]["reportedNodesBySeverity"] = None
            security["nodeUnit"] = "PACKAGE_VERSION_PAIRS_NOT_INSTALL_PATHS"
        else:
            security["nodeUnit"] = "LOCKFILE_PATHS" if manager == "npm" else "NOT_REPORTED"
        report["security"] = security
        if request["includeLag"]:
            rows = []
            for name in manifests:
                directory = (snapshot / name).parent
                found = manual.check_lag(directory, manual.direct_dependencies(directory), metadata_registry,
                                         request["lagMonths"], {}, None)
                rows.extend({**row, "manifest": name} for row in found)
            known = sum(row.get("status") in ("ok", "lagging") for row in rows)
            report["lag"] = {"requested": True, "status": "COMPLETE" if known == len(rows) else "INCOMPLETE",
                "complete": known == len(rows), "basis": "MANIFEST_DECLARED_VERSION_NOT_INSTALLED",
                "months": request["lagMonths"], "totalDeclarations": len(rows), "unknownDeclarations": len(rows) - known,
                "okDeclarations": sum(row.get("status") == "ok" for row in rows), "rows": rows,
                "policyAttainment": "NOT_ASSESSED"}
        after, _, after_issues = collect_inputs(root, project)
        report["inputIntegrity"] = "UNCHANGED" if _digest(after) == report["sourceInputSha256"] else "CHANGED"
        if report["inputIntegrity"] != "UNCHANGED":
            issues.append("SOURCE_CHANGED_DURING_AUDIT")
        issues.extend(after_issues)
        report["coverage"]["unresolvedEdges"] = sorted(set(issues))
        report["coverage"]["complete"] = not issues
        if issues:
            security["complete"], security["verdict"] = False, "UNKNOWN"
            security["observedFindingsRetained"] = True
        report["ok"] = True
        report["status"] = "COMPLETE" if security["complete"] and (not request["includeLag"] or report["lag"]["complete"]) else "INCOMPLETE"
    except AuditError as exc:
        report["error"] = {"code": exc.code, "message": str(exc)}
    except Exception as exc:
        report["error"] = {"code": "audit-worker-error", "message": type(exc).__name__}
    report["commands"] = runner.commands
    report["durationSeconds"] = round(time.monotonic() - started, 4)
    report["limitations"] = ["Known-advisory evidence, not exploitability or runtime verification.",
                             "No install, audit fix, automatic upgrade, or Yellow/Green policy claim."]
    return runner.redact(report)


def main() -> int:
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    report = execute(request)
    Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
