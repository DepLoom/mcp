"""Minimal npm-compatible registry served on 127.0.0.1, nothing else.

Purpose: let the E2E pilot run fully offline. The pinned Core's roadmap
generator is told --registry=http://127.0.0.1:PORT and npm is told
--registry=http://127.0.0.1:PORT, so metadata, artifact probes and real
installs all hit this loopback server. It serves only the packages seeded via
--seed (JSON) and nothing else, failing loudly (404) on unknown names so any
accidental real-registry dependency is caught instead of silently falling back.

Seed format (JSON):
  {
    "packages": {
      "<name>": {
        "main": "index.js",
        "files": {"index.js": "...", "README.md": "..."},   // optional extra files
        "versions": [
          {"version": "1.2.0", "time": "2020-03-01T00:00:00Z"},
          {"version": "1.9.0", "time": "2024-01-15T00:00:00Z"}
        ]
      }
    }
  }

For every (name, version) it builds a gzip'd tar <name>-<version>.tgz
containing the package/ dir (package.json + main + extra files), computes
sha512 integrity + sha1 shasum, and serves npm metadata with dist-tags.latest,
per-version dist {tarball, integrity, shasum} and the npm "time" map so the
Core can judge publish age. Installs use --registry pointing here, so the
resulting package-lock.json carries resolved URLs under this server, which is
exactly what the Core expects (FOREIGN_REGISTRY_URL is never hit).
"""
from __future__ import annotations

import argparse
import binascii
import gzip
import hashlib
import io
import json
import pathlib
import tarfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"


class PackageStore:
    def __init__(self, seed: dict, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.main = {}
        self.files: dict[str, dict[str, str]] = {}
        self.versions: dict[str, list[dict]] = {}
        self.tarball_cache: dict[tuple[str, str], bytes] = {}
        self.integrity_cache: dict[tuple[str, str], tuple[str, str]] = {}
        self.hits: list[str] = []
        for name, spec in (seed.get("packages") or {}).items():
            self.main[name] = spec.get("main", "index.js")
            self.files[name] = spec.get("files") or {}
            versions = []
            for v in spec.get("versions") or []:
                versions.append(
                    {"version": v["version"], "time": v.get("time", "2023-01-01T00:00:00Z")}
                )
            self.versions[name] = versions

    def names(self) -> list[str]:
        return sorted(self.versions)

    def _tarball_bytes(self, name: str, version: str) -> bytes:
        key = (name, version)
        if key in self.tarball_cache:
            return self.tarball_cache[key]
        manifest = {
            "name": name,
            "version": version,
            "private": False,
            "description": f"E2E fixture package {name}@{version}",
            "main": self.main[name],
            "license": "MIT",
        }
        tar_bytes = io.BytesIO()
        with tarfile.open(fileobj=tar_bytes, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for rel, content in [("package/package.json", json.dumps(manifest, indent=2)),
                                 (f"package/{self.main[name]}", self.files[name].get(self.main[name]) or
                                  f"module.exports = {{ name: {name!r}, version: {version!r} }};\n")]:
                data = content.replace("__VERSION__", version).encode("utf-8")
                info = tarfile.TarInfo(rel)
                info.size = len(data)
                info.mtime = 0
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
            for extra, content in self.files[name].items():
                if extra == self.main[name]:
                    continue
                rel = f"package/{extra}"
                data = content.replace("__VERSION__", version).encode("utf-8")
                info = tarfile.TarInfo(rel)
                info.size = len(data)
                info.mtime = 0
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
        raw = tar_bytes.getvalue()
        gz = io.BytesIO()
        with gzip.GzipFile(fileobj=gz, mode="wb", mtime=0) as g:
            g.write(raw)
        result = gz.getvalue()
        self.tarball_cache[key] = result
        return result

    def tarball(self, name: str, version: str) -> tuple[bytes, str, str]:
        data = self._tarball_bytes(name, version)
        if (name, version) not in self.integrity_cache:
            integrity = "sha512-" + _b64u(hashlib.sha512(data).digest())
            shasum = hashlib.sha1(data).hexdigest()
            self.integrity_cache[(name, version)] = (integrity, shasum)
        integrity, shasum = self.integrity_cache[(name, version)]
        return data, integrity, shasum

    def metadata(self, name: str) -> dict | None:
        versions = self.versions.get(name)
        if not versions:
            return None
        by_version = {v["version"]: v for v in versions}
        latest = max((v["version"] for v in versions), key=_semver_key)
        versions_out: dict[str, dict] = {}
        time_map = {}
        for version, meta in by_version.items():
            _, integrity, shasum = self.tarball(name, version)
            versions_out[version] = {
                "name": name,
                "version": version,
                "main": self.main[name],
                "dist": {
                    "tarball": f"{self.base_url}/{name}/-/{name}-{version}.tgz",
                    "integrity": integrity,
                    "shasum": shasum,
                },
            }
            time_map[version] = meta["time"]
        return {
            "_id": name,
            "name": name,
            "description": f"E2E fixture package {name}",
            "dist-tags": {"latest": latest},
            "time": {**{name: time_map[latest]}, **time_map},
            "versions": versions_out,
        }

    def record_hit(self, path: str) -> None:
        if len(self.hits) < 5000:
            self.hits.append(f"{time.strftime('%H:%M:%S')} {path}")


def _semver_key(v: str) -> tuple:
    parts = []
    for token in v.split(".")[:3]:
        nums = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(nums) if nums else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def base64_urlsafe_no_padding(raw: bytes) -> str:
    return _base64().encode(raw.decode("latin1")).decode("latin1")


_b64_table = None


def _base64():
    global _b64_table
    if _b64_table is None:
        _b64_table = _B64()
    return _b64_table


def _b64u(raw: bytes) -> str:
    return binascii.b2a_base64(raw).rstrip(b"\n").decode("ascii")


class Handler(BaseHTTPRequestHandler):
    store: PackageStore | None = None

    def log_message(self, *args):  # silence default logging
        pass

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        store = self.__class__.store
        if store is None:
            self._send_json({"error": "not configured"}, 500)
            return
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path).lstrip("/")
        store.record_hit(self.path)
        if path == "-/ping" or path == "favicon.ico":
            self._send_json({"ok": True})
            return
        if path.endswith(".tgz"):
            parts = path.split("/")
            if len(parts) == 3 and parts[1] == "-":
                name, _, fname = parts
                version = fname[len(name) + 1:-4]
                data, _, _ = store.tarball(name, version)
                self._send_bytes(data, "application/octet-stream")
                return
        meta = store.metadata(path)
        if meta is None:
            self._send_json({"error": f"package {path} not found"}, 404)
            return
        self._send_json(meta)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4873)
    ap.add_argument("--seed", required=True, help="JSON file with the package store")
    ap.add_argument("--pidfile", help="write this server's pid to a file")
    args = ap.parse_args()
    seed_path = pathlib.Path(args.seed)
    seed = json.loads(seed_path.read_text(encoding="utf-8-sig"))
    base = f"http://{HOST}:{args.port}"
    store = PackageStore(seed, base)
    Handler.store = store
    server = ThreadingHTTPServer((HOST, args.port), Handler)
    if args.pidfile:
        pathlib.Path(args.pidfile).write_text(str(server.server_address[1]), encoding="ascii")
    print(f"registry seed={seed_path} packages={store.names()} url={base}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
