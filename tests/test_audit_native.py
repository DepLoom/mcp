"""Real npm/Yarn executables against a localhost registry; no package installs.

Opt in with DEPLOOM_NATIVE_AUDIT_TESTS=1 after provisioning the CLI binaries.
The dedicated audit workflow executes these tests from an installed wheel.
"""
from __future__ import annotations

import asyncio
import gzip
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
