"""Real npm/Yarn executables against a localhost registry; no package installs.

Opt in with DEPLOOM_NATIVE_AUDIT_TESTS=1 after provisioning the CLI binaries.
The dedicated audit workflow executes these tests from an installed wheel.
"""
from __future__ import annotations

import asyncio
import gzip
from hashlib import sha256
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from deploom_mcp.dependency_audit import audit_dependencies


class Registry:
    def __init__(self, findings=True, fail=False):
        self.findings, self.fail = findings, fail
        self.requests, self.payloads = [], []

    def __enter__(self):
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def emit(self, status, value):
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def do_POST(self):
                outer.requests.append(self.path)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                outer.payloads.append(json.loads(raw))
                if outer.fail:
                    return self.emit(503, {"error": "synthetic audit unavailable"})
                if self.path.endswith("/advisories/bulk"):
                    advisory = {"id": 123456, "name": "sample-dep", "url": "https://example.invalid/advisory", "title": "Synthetic test advisory", "severity": "high", "vulnerable_versions": "<2.0.0", "cwe": [], "cvss": {"score": 7.5, "vectorString": None}}
                    return self.emit(200, {"sample-dep": [advisory]} if outer.findings else {})
                self.emit(404, {"error": "unsupported test endpoint"})
            def do_GET(self):
                outer.requests.append(self.path)
                name = self.path.lstrip("/").split("?")[0]
                self.emit(200, {"name": name, "dist-tags": {"latest": "2.0.0"}, "time": {"created": "2023-01-01T00:00:00Z", "modified": "2026-01-01T00:00:00Z", "1.0.0": "2023-01-01T00:00:00Z", "2.0.0": "2026-01-01T00:00:00Z"}, "versions": {version: {"name": name, "version": version, "dist": {"tarball": outer.url + "/never-download.tgz"}} for version in ("1.0.0", "2.0.0")}})
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


@unittest.skipUnless(os.environ.get("DEPLOOM_NATIVE_AUDIT_TESTS") == "1", "native audit CLI gate is opt-in")
class NativeAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.outputs = self.base / "evidence"

    def fixture(self, manager, registry):
        package = {"name": "fixture", "private": True, "version": "1.0.0", "dependencies": {"sample-dep": "1.0.0"}, "scripts": {"preinstall": "node -e \"require('fs').writeFileSync('SHOULD_NOT_EXIST','bad')\""}}
        if manager == "yarn-berry":
            package["packageManager"] = "yarn@4.9.2"
            (self.project / ".yarnrc.yml").write_text('unsafeHttpWhitelist: ["127.0.0.1"]\n', encoding="utf-8")
            (self.project / "yarn.lock").write_text('''__metadata:
  version: 8
  cacheKey: 10c0

"fixture@workspace:.":
  version: 0.0.0-use.local
  resolution: "fixture@workspace:."
  dependencies:
    sample-dep: "npm:1.0.0"
  languageName: unknown
  linkType: soft

"sample-dep@npm:1.0.0":
  version: 1.0.0
  resolution: "sample-dep@npm:1.0.0"
  languageName: node
  linkType: hard
''', encoding="utf-8")
        elif manager == "yarn-classic":
            package["packageManager"] = "yarn@1.22.22"
            (self.project / "yarn.lock").write_text('# yarn lockfile v1\n\n"sample-dep@1.0.0":\n  version "1.0.0"\n  resolved "' + registry + '/sample-dep-1.0.0.tgz"\n', encoding="utf-8")
        else:
            (self.project / "package-lock.json").write_text(json.dumps({"name": "fixture", "version": "1.0.0", "lockfileVersion": 3, "requires": True, "packages": {"": {"name": "fixture", "version": "1.0.0", "dependencies": package["dependencies"]}, "node_modules/sample-dep": {"version": "1.0.0", "resolved": registry + "/sample-dep-1.0.0.tgz"}}}), encoding="utf-8")
        (self.project / "package.json").write_text(json.dumps(package), encoding="utf-8")
        return {p.name: p.read_bytes() for p in self.project.iterdir()}

    def run_audit(self, manager, registry):
        return asyncio.run(audit_dependencies(project_path=str(self.project), package_manager=manager,
            registry=registry, output_dir=str(self.outputs), deadline_seconds=60))

    def assert_preserved(self, before):
        after = {p.name: p.read_bytes() for p in self.project.iterdir() if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.project / "node_modules").exists())
        self.assertFalse((self.project / "SHOULD_NOT_EXIST").exists())
        self.assertEqual([], list(self.outputs.glob("audits/*/private-*")))

    def test_npm_finding_real_command_and_read_only(self):
        with Registry() as registry:
            before = self.fixture("npm", registry.url)
            report = self.run_audit("npm", registry.url)
        self.assertTrue(report["ok"], report)
        self.assertEqual("COMPLETE", report["status"], report)
        self.assertEqual("FINDINGS", report["security"]["verdict"], report)
        self.assertGreater(report["security"]["counts"]["uniqueAdvisories"], 0)
        self.assertEqual(1, report["security"]["auditExitCode"])
        self.assertFalse(any(path.endswith(".tgz") for path in registry.requests))
        self.assert_preserved(before)

    def test_candidate_binding_audits_transitives_and_rejects_changed_inputs_before_network(self):
        def write(path, data):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")

        with Registry() as registry:
            root = {"name": "candidate", "version": "1.0.0", "private": True, "workspaces": ["packages/*"]}
            member = {"name": "candidate-member", "version": "1.0.0", "dependencies": {"sample-parent": "1.0.0"}}
            write(self.project / "package.json", root)
            write(self.project / "packages/app/package.json", member)
            lock = {"name": "candidate", "version": "1.0.0", "lockfileVersion": 3, "requires": True,
                    "packages": {"": root, "packages/app": member,
                        "node_modules/candidate-member": {"resolved": "packages/app", "link": True},
                        "node_modules/sample-parent": {"version": "1.0.0", "dependencies": {"sample-dep": "1.0.0"}},
                        "node_modules/sample-dep": {"version": "1.0.0"}}}
            write(self.project / "package-lock.json", lock)
            files = {p.relative_to(self.project).as_posix(): sha256(p.read_bytes()).hexdigest()
                     for p in self.project.rglob("*.json")}
            binding = {"files": files, "lockSha256": files["package-lock.json"],
                       "registrySha256": sha256(registry.url.encode()).hexdigest(),
                       "resolverIdentitySha256": "a" * 64}

            def audit(candidate):
                return asyncio.run(audit_dependencies(
                    project_path=str(self.project), package_manager="npm", registry=registry.url,
                    output_dir=str(self.outputs), deadline_seconds=30, _candidate_binding=candidate,
                ))

            report = audit(binding)
            self.assertTrue(report["ok"], report)
            self.assertEqual("BOUND_SECURITY_AUDIT_COMPLETE", report["candidateAudit"]["status"])
            self.assertEqual("FINDINGS", report["security"]["verdict"])
            self.assertEqual("NOT_ASSESSED", report["candidateAudit"]["finalPolicy"])
            self.assertFalse(report["runtimeVerified"])
            self.assertTrue(any(payload.get("sample-parent") == ["1.0.0"] and payload.get("sample-dep") == ["1.0.0"]
                                for payload in registry.payloads), registry.payloads)
            self.assertEqual(files, {p.relative_to(self.project).as_posix(): sha256(p.read_bytes()).hexdigest()
                                     for p in self.project.rglob("*.json")})
            before_requests = len(registry.requests)
            mismatch = audit({**binding, "registrySha256": "b" * 64})
            self.assertEqual("candidate-audit-registry-mismatch", mismatch["error"]["code"])
            self.assertEqual("UNKNOWN", mismatch["candidateAudit"]["status"])
            lock["packages"]["node_modules/sample-dep"]["version"] = "2.0.0"
            write(self.project / "package-lock.json", lock)
            rejected = audit(binding)
            self.assertEqual("candidate-audit-input-mismatch", rejected["error"]["code"])
            self.assertEqual("UNKNOWN", rejected["candidateAudit"]["status"])
            self.assertEqual(before_requests, len(registry.requests))
            self.assertFalse((self.project / "node_modules").exists())
            self.assertEqual([], list(self.outputs.glob("audits/*/private-*")))

    def test_candidate_policy_reads_exact_versions_from_each_workspace(self):
        with Registry() as registry:
            root = {"name": "candidate", "version": "1.0.0", "private": True,
                    "workspaces": ["packages/*"], "dependencies": {"sample-dep": ">=1.0.0"}}
            member = {"name": "candidate-member", "version": "1.0.0", "dependencies": {"sample-dep": ">=1.0.0"}}
            lock = {"name": "candidate", "version": "1.0.0", "lockfileVersion": 3, "requires": True,
                    "packages": {"": root, "packages/app": member,
                        "node_modules/candidate-member": {"resolved": "packages/app", "link": True},
                        "node_modules/sample-dep": {"version": "1.0.0"},
                        "packages/app/node_modules/sample-dep": {"version": "2.0.0"}}}
            for name, value in (("package.json", root), ("package-lock.json", lock),
                                ("packages/app/package.json", member)):
                path = self.project / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf-8")
            files = {p.relative_to(self.project).as_posix(): sha256(p.read_bytes()).hexdigest()
                     for p in self.project.rglob("*.json")}
            binding = {"files": files, "lockSha256": files["package-lock.json"],
                       "registrySha256": sha256(registry.url.encode()).hexdigest(),
                       "resolverIdentitySha256": "c" * 64}

            def run(policy):
                return asyncio.run(audit_dependencies(
                    project_path=str(self.project), package_manager="npm", registry=registry.url,
                    output_dir=str(self.outputs), deadline_seconds=30, _candidate_binding={**binding, "policy": policy},
                ))

            yellow = run({"targetLevel": "yellow", "minLagOkPct": 50})
            self.assertTrue(yellow["ok"], yellow)
            self.assertEqual("PASS", yellow["candidateAudit"]["finalPolicy"])
            self.assertEqual("EXACT_OWNER_RESOLVED_CANDIDATE_LOCK", yellow["lag"]["basis"])
            self.assertEqual([(".", "1.0.0", "lagging"), ("packages/app", "2.0.0", "ok")],
                             [(row["owner"], row["version"], row["status"]) for row in yellow["lag"]["rows"]])
            self.assertEqual(2, yellow["lag"]["totalDeclarations"])
            self.assertEqual(1, yellow["candidateAudit"]["policyAssessment"]["knownBySeverity"]["H"])
            self.assertFalse(yellow["runtimeVerified"])
            green = run({"targetLevel": "green"})
            self.assertTrue(green["ok"], green)
            self.assertEqual("FAIL", green["candidateAudit"]["finalPolicy"])
            self.assertEqual(["H"], green["candidateAudit"]["policyAssessment"]["exceededSeverityCaps"])
            self.assertEqual(2, green["lag"]["requiredDeclarations"])

            import sys
            from deploom_mcp.distribution import resolve_core_dir
            from deploom_mcp.workspace_policy_delivery import load_delivery_policy

            plan = {"approvedLockSha256": binding["lockSha256"], "registrySha256": binding["registrySha256"],
                    "resolverIdentitySha256": binding["resolverIdentitySha256"],
                    "workspaceOwners": {"": {"manifestSha256": files["package.json"]},
                                        "packages/app": {"manifestSha256": files["packages/app/package.json"]}}}
            policy = self.base / "approved-policy.json"
            yellow_audit = yellow["artifacts"]
            green_audit = green["artifacts"]
            policy.write_text('{"targetLevel":"yellow","minLagOkPct":50}', encoding="utf-8")
            with patch.object(sys, "path", [str(resolve_core_dir()), *sys.path]):
                evidence = load_delivery_policy(yellow_audit["report"], yellow_audit["sha256"], policy, plan)
                self.assertEqual((1, 2), (evidence["lagConfirmed"], evidence["lagDenominator"]))
                with self.assertRaisesRegex(ValueError, "WORKSPACE_DELIVERY_AUDIT_DIGEST_MISMATCH"):
                    load_delivery_policy(yellow_audit["report"], "0" * 64, policy, plan)
                with self.assertRaisesRegex(ValueError, "WORKSPACE_DELIVERY_AUDIT_BINDING_MISMATCH"):
                    load_delivery_policy(yellow_audit["report"], yellow_audit["sha256"], policy,
                                         {**plan, "resolverIdentitySha256": "d" * 64})
                policy.write_text('{"targetLevel":"green"}', encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "WORKSPACE_DELIVERY_AUDIT_BINDING_MISMATCH"):
                    load_delivery_policy(yellow_audit["report"], yellow_audit["sha256"], policy, plan)
                with self.assertRaisesRegex(ValueError, "WORKSPACE_DELIVERY_POLICY_NOT_MET"):
                    load_delivery_policy(green_audit["report"], green_audit["sha256"], policy, plan)
            self.assertEqual(files, {p.relative_to(self.project).as_posix(): sha256(p.read_bytes()).hexdigest()
                                     for p in self.project.rglob("*.json")})

    def test_classic_uses_exact_inventory_no_install_or_reresolution(self):
        with Registry() as registry:
            before = self.fixture("yarn-classic", registry.url)
            report = self.run_audit("yarn-classic", registry.url)
        self.assertTrue(report["ok"], report)
        self.assertEqual("COMPLETE", report["status"], report)
        self.assertEqual("FINDINGS", report["security"]["verdict"], report)
        self.assertEqual("YARN_LOCK_EXACT_PACKAGE_VERSION_INVENTORY", report["coverage"]["basis"])
        self.assertTrue(any(payload.get("sample-dep") == ["1.0.0"] for payload in registry.payloads), registry.payloads)
        self.assertFalse(any(path.endswith(".tgz") for path in registry.requests))
        for command in report["commands"]:
            self.assertNotIn("install", command["argv"])
        self.assert_preserved(before)

    def test_berry_native_complete_transitive_audit(self):
        with Registry() as registry:
            before = self.fixture("yarn-berry", registry.url)
            report = self.run_audit("yarn-berry", registry.url)
        self.assertTrue(report["ok"], report)
        self.assertEqual("COMPLETE", report["status"], report)
        self.assertEqual("FINDINGS", report["security"]["verdict"], report)
        args = next(c["argv"] for c in report["commands"] if c["argv"][:3] == ["yarn", "npm", "audit"])
        self.assertIn("--recursive", args)
        self.assertIn("--all", args)
        self.assert_preserved(before)

    def test_npm_clean_positive_control(self):
        with Registry(findings=False) as registry:
            before = self.fixture("npm", registry.url)
            report = self.run_audit("npm", registry.url)
        self.assertTrue(report["ok"], report)
        self.assertEqual("NO_KNOWN_FINDINGS", report["security"]["verdict"], report)
        self.assertFalse(report["runtimeVerified"])
        self.assert_preserved(before)

    def test_berry_clean_positive_control(self):
        with Registry(findings=False) as registry:
            before = self.fixture("yarn-berry", registry.url)
            report = self.run_audit("yarn-berry", registry.url)
        self.assertTrue(report["ok"], report)
        self.assertEqual("NO_KNOWN_FINDINGS", report["security"]["verdict"], report)
        self.assert_preserved(before)

    def test_berry_registry_error_never_becomes_clean(self):
        with Registry(fail=True) as registry:
            before = self.fixture("yarn-berry", registry.url)
            report = self.run_audit("yarn-berry", registry.url)
        self.assertFalse(report["ok"], report)
        self.assertEqual("UNKNOWN", report["security"]["verdict"], report)
        self.assertIsNone(report["security"]["counts"], report)
        self.assert_preserved(before)


    def test_native_audit_proxy_option_and_report_precedence(self):
        with Registry(findings=False) as registry:
            before = self.fixture("npm", registry.url)
            with patch.dict(os.environ, {"DEPLOOM_MCP_USE_SYSTEM_PROXY": "true"}):
                denied = asyncio.run(audit_dependencies(
                    project_path=str(self.project), package_manager="npm",
                    registry=registry.url, output_dir=str(self.outputs),
                    deadline_seconds=60, use_system_proxy=False,
                ))
            self.assertTrue(denied["ok"], denied)
            self.assertFalse(denied["network"]["useSystemProxy"])
            self.assertEqual("tool", denied["network"]["source"])
            allowed = asyncio.run(audit_dependencies(
                project_path=str(self.project), package_manager="npm",
                registry=registry.url, output_dir=str(self.outputs),
                deadline_seconds=60, use_system_proxy=True,
            ))
            self.assertTrue(allowed["ok"], allowed)
            self.assertTrue(allowed["network"]["useSystemProxy"])
            self.assert_preserved(before)

    def test_registry_error_never_becomes_clean(self):
        with Registry(fail=True) as registry:
            before = self.fixture("npm", registry.url)
            report = self.run_audit("npm", registry.url)
        self.assertFalse(report["ok"], report)
        self.assertEqual("UNKNOWN", report["security"]["verdict"], report)
        self.assertIsNone(report["security"]["counts"])
        self.assert_preserved(before)


if __name__ == "__main__":
    unittest.main()
