"""Build the disposable E2E pilot fixture project and preflight its plan.

Usage:
  python scripts/e2e_fixture.py make --dir <fixture> --registry http://127.0.0.1:PORT
      - writes package.json / src / verify.js / build.js / .gitignore / README / .npmrc
      - runs `npm install --registry=<registry>` so node_modules + a real
        package-lock.json (v3, resolved URLs under the local registry) exist
      - git init + baseline commit (clean tree for the skill's worktree logic)
  python scripts/e2e_fixture.py plan --dir <fixture> --registry http://127.0.0.1:PORT
      - runs the REAL pinned Core through the REAL MCP boundary
        (server.get_upgrade_plan) with registry=<registry> and
        exclude=kontur-fixture-c, prints the compact response. This is the
        deterministic preflight: the OpenCode pilot must reproduce these rows.

The fixture pins three tiny local packages (served by scripts/e2e_registry.py):
  kontur-fixture-a 1.2.0 -> 1.9.0  (major, publish-lag ok -> GREEN update)
  kontur-fixture-b 2.0.0 -> 2.1.0  (minor, publish-lag ok -> update)
  kontur-fixture-c 3.0.0 (4.0.0 exists but is excluded via `exclude`)
build.js/verify.js are the project's declared build+test gates; verify.js makes
the exclusion invariant executable: it FAILS if kontur-fixture-c ever leaves
3.0.0, so the E2E proves "excluded package untouched" at runtime, not just by
diff.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

EXCLUDED = "kontur-fixture-c"
PACKAGES = {
    "kontur-fixture-a": ("1.2.0", "1.9.0"),
    "kontur-fixture-b": ("2.0.0", "2.1.0"),
    "kontur-fixture-c": ("3.0.0", "4.0.0"),
}

PACKAGE_JSON = {
    "name": "fixture-app",
    "version": "1.0.0",
    "private": True,
    "description": "E2E pilot fixture for the deploom-upgrade skill",
    "license": "MIT",
    "scripts": {
        "test": "node verify.js",
        "build": "node build.js",
    },
    "dependencies": {
        "kontur-fixture-a": PACKAGES["kontur-fixture-a"][0],
        "kontur-fixture-b": PACKAGES["kontur-fixture-b"][0],
        "kontur-fixture-c": PACKAGES["kontur-fixture-c"][0],
    },
}

SRC_INDEX_JS = """\
'use strict';
const a = require('kontur-fixture-a');
const b = require('kontur-fixture-b');
const c = require('kontur-fixture-c');
module.exports = { a, b, c, versions: { a: a.version, b: b.version, c: c.version } };
"""

VERIFY_JS = """\
'use strict';
const fs = require('fs');
const path = require('path');
const { versions } = require('./src/index.js');
const pkg = name => JSON.parse(fs.readFileSync(path.join('node_modules', name, 'package.json'), 'utf8'));
const want = {};
for (const name of ['kontur-fixture-a', 'kontur-fixture-b', 'kontur-fixture-c']) {
  want[name] = pkg(name).version;
}
if (want['kontur-fixture-c'] !== '3.0.0') {
  console.error('EXCLUSION_VIOLATED excluded kontur-fixture-c left 3.0.0 -> ' + want['kontur-fixture-c']);
  process.exit(1);
}
if (versions.a !== want['kontur-fixture-a'] || versions.b !== want['kontur-fixture-b'] || versions.c !== want['kontur-fixture-c']) {
  console.error('MISMATCH src vs installed', versions, want);
  process.exit(1);
}
console.log('SMOKE_test_OK a=' + want['kontur-fixture-a'] + ' b=' + want['kontur-fixture-b'] + ' c=' + want['kontur-fixture-c']);
"""

BUILD_JS = """\
'use strict';
const fs = require('fs');
const fsx = require('fs');
const { versions } = require('./src/index.js');
fsx.mkdirSync('dist', { recursive: true });
fs.writeFileSync('dist/result.json', JSON.stringify({ app: 'fixture-app', versions }, null, 2) + '\\n');
console.log('BUILD_OK ' + JSON.stringify(versions));
"""

GITIGNORE = "node_modules/\ndist/\n"

README = """\
# fixture-app

Disposable E2E fixture for the deploom-upgrade skill. Uses a private local
registry (see .npmrc). `npm run build` -> dist/result.json, `npm test` ->
verify.js (also enforces the kontur-fixture-c exclusion invariant).
"""


def _npm(args: list[str], cwd: pathlib.Path) -> None:
    proc = subprocess.run(_npm_cmd() + args, cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"npm failed rc={proc.returncode}\nSTDOUT:\n{proc.stdout[-800:]}\nSTDERR:\n{proc.stderr[-800:]}")


def _npm_cmd() -> list[str]:
    exe = shutil.which("npm.cmd") or shutil.which("npm")
    if sys.platform == "win32":
        return ["cmd", "/c", exe or "npm"]
    return [exe or "npm"]


def make_fixture(directory: str, registry: str) -> None:
    root = pathlib.Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps(PACKAGE_JSON, indent=2) + "\n", encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "index.js").write_text(SRC_INDEX_JS, encoding="utf-8")
    (root / "verify.js").write_text(VERIFY_JS, encoding="utf-8")
    (root / "build.js").write_text(BUILD_JS, encoding="utf-8")
    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (root / "README.md").write_text(README, encoding="utf-8")
    (root / ".npmrc").write_text(f"registry={registry}\n", encoding="utf-8")
    _npm(["install", "--registry", registry], cwd=root)
    subprocess.run(["git", "init", "-b", "main"], cwd=str(root), capture_output=True, text=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=str(root), capture_output=True, text=True)
    print(json.dumps({"fixture": str(root), "registry": registry, "packages": PACKAGES}, indent=2))


def preflight(directory: str, registry: str, exclude: str) -> None:
    from deploom_mcp.server import get_upgrade_plan

    result = get_upgrade_plan(
        project_path=directory,
        target_level="yellow",
        min_lag_ok_pct=80,
        registry=registry,
        exclude=[exclude],
    )
    if not result.get("ok"):
        print(json.dumps(result, indent=2))
        raise SystemExit(1)
    summary = result.get("summary") or {}
    rows = result.get("recommendationGroups") or []
    print(json.dumps(
        {
            "ok": result.get("ok"),
            "status": result.get("status"),
            "rowsTotal": summary.get("rowsTotal"),
            "verdicts": (summary.get("verdicts") or {}),
            "groups": rows,
            "boundaryNote": result.get("boundaryNote"),
        },
        indent=2,
    ))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make")
    mk.add_argument("--dir", required=True)
    mk.add_argument("--registry", required=True)
    pl = sub.add_parser("plan")
    pl.add_argument("--dir", required=True)
    pl.add_argument("--registry", required=True)
    pl.add_argument("--exclude", default=EXCLUDED)
    args = ap.parse_args()
    if args.cmd == "make":
        make_fixture(args.dir, args.registry)
    else:
        preflight(args.dir, args.registry, args.exclude)


if __name__ == "__main__":
    main()
