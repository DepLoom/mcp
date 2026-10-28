#!/usr/bin/env python3
"""Real OSV via an instrumented HTTP CONNECT proxy and the actual MCP/Core.

Unlike a fake-Core regression, this smoke uses a real npm package, real OSV
and real registry metadata. It does not install dependencies or migrate code.
A network refusal is LIVE_OSV_UNVERIFIED, not a manufactured success.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import select
import socket
import socketserver
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

import requests
from mcp import ClientSession, StdioServerParameters, stdio_client

ALLOWED = {"api.osv.dev", "registry.npmjs.org"}


class ProxyHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        remote = None
        try:
            request = self.rfile.readline(8192)
            fields = request.decode("ascii", "strict").strip().split(" ")
            if len(fields) < 3 or fields[0] != "CONNECT" or ":" not in fields[1]:
                self.wfile.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                return
            hostname, port_text = fields[1].rsplit(":", 1)
            if hostname not in ALLOWED or port_text != "443":
                self.wfile.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                return
            while True:
                line = self.rfile.readline(8192)
                if not line or line in (b"\r\n", b"\n"):
                    break
            remote = socket.create_connection((hostname, 443), timeout=20)
            self.server.record(hostname)
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            sockets = (self.connection, remote)
            while True:
                readable, _, _ = select.select(sockets, [], [], 50)
                if not readable:
                    break
                for stream in readable:
                    data = stream.recv(65536)
                    if not data:
                        return
                    (remote if stream is self.connection else self.connection).sendall(data)
        except (OSError, UnicodeError, ValueError):
            try:
                self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except OSError:
                pass
        finally:
            if remote:
                remote.close()

    def log_message(self, *args):  # no URLs or credentials in logs
        pass


class Proxy(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), ProxyHandler)
        self.hosts: list[str] = []
        self.mutex = threading.Lock()

    def record(self, host: str) -> None:
        with self.mutex:
            self.hosts.append(host)

    def snapshot(self) -> dict[str, int]:
        with self.mutex:
            return {host: self.hosts.count(host) for host in sorted(set(self.hosts))}

    def clear(self) -> None:
        with self.mutex:
            self.hosts.clear()


def make_project(path: Path) -> dict[str, str]:
    path.mkdir()
    package = {
        "name": "deploom-proxy-osv-realworld-smoke", "version": "1.0.0",
        "private": True, "dependencies": {"lodash": "4.17.20"},
    }
    lock = {
        "name": package["name"], "version": "1.0.0",
        "lockfileVersion": 3, "requires": True,
        "packages": {
            "": {"name": package["name"], "version": "1.0.0",
                 "dependencies": {"lodash": "4.17.20"}},
            "node_modules/lodash": {"version": "4.17.20"},
        },
    }
    (path / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (path / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()}


async def inspect_mcp(project: Path, env: dict[str, str]) -> dict:
    params = StdioServerParameters(
        command=os.sys.executable, args=["-m", "deploom_mcp"],
        cwd=str(project.parent), env=env,
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            tools = await client.list_tools()
            assert "get_upgrade_plan" in {tool.name for tool in tools.tools}
            response = await client.call_tool("get_upgrade_plan", {
                "project_path": str(project), "target_level": "yellow",
                "deadline_seconds": 120, "draft_max_candidates": 3,
                "use_system_proxy": True,
                "registry": "https://registry.npmjs.org",
                "output_dir": str(project.parent / "blueprints"),
            })
            return response.structured_content or json.loads(response.content[0].text)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="deploom-live-osv-") as directory:
        root = Path(directory)
        project = root / "project"
        original = make_project(project)
        with Proxy() as proxy:
            thread = threading.Thread(target=proxy.serve_forever, daemon=True)
            thread.start()
            try:
                endpoint = f"http://127.0.0.1:{proxy.server_address[1]}"
                env = dict(os.environ)
                env.update({
                    "HTTPS_PROXY": endpoint, "HTTP_PROXY": endpoint,
                    "https_proxy": endpoint, "http_proxy": endpoint,
                    "ALL_PROXY": "", "all_proxy": "",
                    "NO_PROXY": "127.0.0.1,localhost",
                    "no_proxy": "127.0.0.1,localhost",
                    "DEPLOOM_MCP_STATE_DIR": str(root / "state"),
                    "DEPLOOM_MCP_USE_SYSTEM_PROXY": "true",
                })
                # Validate the external service independently before asking
                # Core to use the same proxy. Do NOT count preflight CONNECTs
                # as evidence that Core/MCP contacted OSV.
                try:
                    preflight = requests.post(
                        "https://api.osv.dev/v1/query",
                        json={"package": {"name": "lodash", "ecosystem": "npm"},
                              "version": "4.17.20"},
                        proxies={"http": endpoint, "https": endpoint},
                        timeout=25,
                    )
                    preflight.raise_for_status()
                    preflight_vulns = (preflight.json() or {}).get("vulns") or []
                    if not preflight_vulns:
                        raise RuntimeError("OSV preflight returned no known lodash 4.17.20 vulnerability")
                except Exception as exc:
                    print(json.dumps({"status": "LIVE_OSV_UNVERIFIED", "stage": "network-preflight",
                                      "reasonType": type(exc).__name__,
                                      "preflightProxyConnections": proxy.snapshot()}, sort_keys=True))
                    return 2
                proxy.clear()
                try:
                    bp = asyncio.run(inspect_mcp(project, env))
                    hosts = proxy.snapshot()
                    # Read published Core security evidence from the exact
                    # result.json/targetAssessment, not the MCP compact summary.
                    # The compact may omit numeric security detail.
                    status = ((bp.get("status") or {}).get("draftStatus"))
                    valid = bool(bp.get("ok")) and status in ("DRAFT_READY", "DRAFT_PARTIAL")
                    changed = any(
                        hashlib.sha256((project / name).read_bytes()).hexdigest() != digest
                        for name, digest in original.items()
                    )
                    if changed or len(list(project.iterdir())) != len(original):
                        raise RuntimeError("project dependency inputs changed during read-only planning")
                    # Diagnostics are package-evidence fields only; never
                    # dump proxy configuration or file paths containing secrets.
                    plan_path = ((bp.get("artifacts") or {}).get("plan"))
                    manifest_path = ((bp.get("artifacts") or {}).get("manifest"))
                    plan_data = json.loads(Path(plan_path).read_text(encoding="utf-8")) if plan_path else {}
                    manifest_data = json.loads(Path(manifest_path).read_text(encoding="utf-8")) if manifest_path else {}
                    plan_rows = [x for p in (plan_data.get("proposals") or []) for x in (p.get("rows") or [])]
                    first = plan_rows[0] if plan_rows else {}
                    metadata = manifest_data.get("metadata") or {}
                    assessment = (bp.get("targetAssessment") or {}).get("aggregate") or {}
                    current_known = int(metadata.get("securityKnown") or 0)
                    security_total = int(metadata.get("securityTotal") or 0)
                    target_known = int(assessment.get("postPlanSecurityKnown") or 0)
                    target_total = int(assessment.get("postPlanSecurityTotal") or 0)
                    target_unknown = int(assessment.get("postPlanSecurityUnknown") or 0)
                    osv_unknown = int(metadata.get("osvUnknown") or 0)
                    passed = (
                        valid and hosts.get("api.osv.dev", 0) > 0
                        and current_known > 0 and security_total > 0
                        and target_known > 0 and target_total > 0
                        and target_unknown == 0 and osv_unknown == 0
                    )
                    evidence_keys = (
                        "package", "status", "current", "target", "currentVulns",
                        "vulnEvidenceByVersion", "vuln_evidence_by_version",
                        "scanState", "draftUnknown", "reason", "notes",
                    )
                    result = {
                        "diagnosticPlanRow": {key: first.get(key) for key in evidence_keys if key in first},
                        "diagnosticManifestMetadata": {
                            key: manifest_data.get("metadata", {}).get(key)
                            for key in ("securityKnown", "securityUnknown", "securityTotal",
                                        "osvUnknown", "metadataKnown", "metadataTotal",
                                        "postPlanSecurityKnown", "postPlanSecurityUnknown",
                                        "postPlanSecurityTotal", "postPlanGoal")
                        },
                        "diagnosticBlueprintAssessment": (bp.get("targetAssessment") or {}).get("aggregate") or {},
                        "status": "LIVE_OSV_CONFIRMED" if passed else "LIVE_OSV_UNVERIFIED",
                        "stage": "mcp-core-osv",
                        "mcpOk": bp.get("ok"),
                        "draftStatus": status,
                        "securityKnown": current_known,
                        "securityTotal": security_total,
                        "statusCounts": (bp.get("summary") or {}).get("statusCounts"),
                        "proxyConnects": hosts,
                        "projectUnchanged": True,
                        "preflightVulnerabilityRecords": len(preflight_vulns),
                        "note": "Planning evidence only: no installed-tree or runtime security proof.",
                    }
                    print(json.dumps(result, sort_keys=True))
                    return 0 if passed else 2
                except Exception as exc:
                    print(json.dumps({"status": "LIVE_OSV_UNVERIFIED", "stage": "mcp-core-osv",
                                      "reasonType": type(exc).__name__,
                                      "proxyConnects": proxy.snapshot()}, sort_keys=True))
                    return 2
            finally:
                proxy.shutdown()
                proxy.server_close()
                thread.join(5)


if __name__ == "__main__":
    raise SystemExit(main())
