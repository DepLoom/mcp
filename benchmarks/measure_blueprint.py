#!/usr/bin/env python3
"""Stage 1 performance baseline for the headless Upgrade Blueprint boundary.

Builds a synthetic single-project fixture (N runtime deps), then runs the
real planner via `upgrade_blueprint.py` (production subprocess boundary) and
records, per run: wall-clock, the planner's own elapsedMs (deadline clock),
metadata/security coverage, and the published status.

Modes:
  * cold -- no registry metadata cache (every run re-fetches or fails registry).
  * warm -- reuses a persistent --cache-dir across runs (a real registry must
            be reachable to populate it first; if the host has no registry
            access the honest rows show registryFailed=sn and cold==warm).

Usage examples:
  python scripts/measure_upgrade_blueprint.py --deps 40 --repeats 3
  python scripts/measure_upgrade_blueprint.py --deps 40 --repeats 3 --warm --cache-dir .tmp-reg-cache
  python scripts/measure_upgrade_blueprint.py --deps 0   # pure fixed overhead floor
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON = str(VENV) if VENV.is_file() else sys.executable

# (name, requested spec, pinned lock version) -- real packages at known
# versions so the inventory is realistic; exact registry behavior depends on
# whether the current host can reach the configured npm registry.
CANDIDATES = [
    ("uuid", "^10.0.0", "10.0.0"),
    ("is-number", "^7.0.0", "7.0.0"),
    ("lodash", "^4.17.21", "4.17.21"),
    ("react", "^18.3.1", "18.3.1"),
    ("axios", "^1.7.7", "1.7.7"),
    ("chalk", "^5.3.0", "5.3.0"),
    ("commander", "^12.1.0", "12.1.0"),
    ("date-fns", "^4.1.0", "4.1.0"),
    ("debug", "^4.3.7", "4.3.7"),
    ("dotenv", "^16.4.5", "16.4.5"),
    ("express", "^4.21.1", "4.21.1"),
    ("fast-glob", "^3.3.2", "3.3.2"),
    ("glob", "^10.4.5", "10.4.5"),
    ("inquirer", "^9.3.6", "9.3.6"),
    ("js-yaml", "^4.1.0", "4.1.0"),
    ("minimatch", "^9.0.5", "9.0.5"),
    ("node-fetch", "^3.3.2", "3.3.2"),
    ("pino", "^9.5.0", "9.5.0"),
    ("semver", "^7.6.3", "7.6.3"),
    ("zod", "^3.23.8", "3.23.8"),
    ("tslib", "^2.8.0", "2.8.0"),
    ("rxjs", "^7.8.1", "7.8.1"),
    ("yauzl", "^3.1.3", "3.1.3"),
    ("adm-zip", "^0.5.16", "0.5.16"),
    ("iconv-lite", "^0.6.3", "0.6.3"),
    ("jsonwebtoken", "^9.0.2", "9.0.2"),
    ("log4js", "^6.9.1", "6.9.1"),
    ("moment", "^2.30.1", "2.30.1"),
    ("nanoid", "^5.0.7", "5.0.7"),
    ("pg", "^8.13.1", "8.13.1"),
    ("qs", "^6.13.0", "6.13.0"),
    ("rimraf", "^6.0.1", "6.0.1"),
    ("tar", "^7.4.3", "7.4.3"),
    ("undici", "^6.20.1", "6.20.1"),
    ("ws", "^8.18.0", "8.18.0"),
    ("xml2js", "^0.6.2", "0.6.2"),
    ("yargs", "^17.7.2", "17.7.2"),
    ("zlib", "^1.0.5", "1.0.5"),  # node builtin shim used by legacy fixtures
    ("cli-progress", "^3.12.0", "3.12.0"),
    ("find-up", "^7.0.0", "7.0.0"),
]


def build_fixture(deps: int) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="ub-bench-"))
    proj = tmp / "proj"
    proj.mkdir(parents=True)
    chosen = CANDIDATES[:deps]
    dependencies = {name: spec for name, spec, _ in chosen}
    package = {"name": "ub-bench", "version": "1.0.0", "dependencies": dependencies}
    (proj / "package.json").write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    packages = {"": {"name": "ub-bench", "version": "1.0.0", "dependencies": dependencies}}
    for name, _, version in chosen:
        packages[f"node_modules/{name}"] = {"version": version}
    lock = {"name": "ub-bench", "version": "1.0.0", "lockfileVersion": 3, "requires": True, "packages": packages}
    (proj / "package-lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    return proj


def run_once(
    project: Path,
    run_id: str,
    output_dir: Path,
    cache_dir: Path | None,
    deadline: float,
) -> dict:
    argv = [
        PYTHON,
        str(ROOT / "upgrade_blueprint.py"),
        "--project", str(project),
        "--run-id", run_id,
        "--output-dir", str(output_dir),
        "--deadline-seconds", str(deadline),
        "--registry-metadata-ttl", "0" if cache_dir is None else "3600",
    ]
    if cache_dir is not None:
        argv += ["--registry-metadata-cache", str(cache_dir)]
    start = time.perf_counter()
    proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    wall = time.perf_counter() - start
    if proc.returncode != 0:
        raise RuntimeError(f"boundary failed rc={proc.returncode}: {proc.stdout} {proc.stderr[-2000:]}")
    bp_path = output_dir / "upgrade-blueprint.json"
    bp = json.loads(bp_path.read_text(encoding="utf-8"))
    meta = bp.get("summary") or {}
    status = bp.get("status") or {}
    return {
        "runId": run_id,
        "wallSec": round(wall, 3),
        "plannerElapsedMs": bp.get("elapsedMs"),
        "draftStatus": status.get("draftStatus"),
        "total": meta.get("totalDependencies"),
        "metadataKnown": meta.get("metadataKnown"),
        "metadataTotal": meta.get("metadataTotal"),
        "draftRegistryFailed": (bp.get("summary") or {}).get("draftCounts", {}).get("registry-failed"),
        "proposed": meta.get("proposed"),
        "needsReview": meta.get("needsReview"),
    }


def summarize(name: str, rows: list[dict]) -> dict:
    walls = [r["wallSec"] for r in rows]
    planner = [r["plannerElapsedMs"] for r in rows if r["plannerElapsedMs"] is not None]
    return {
        "label": name,
        "count": len(rows),
        "wallSec": {"min": round(min(walls), 3), "median": round(statistics.median(walls), 3), "max": round(max(walls), 3)},
        "plannerElapsedMs": {"min": min(planner), "median": round(statistics.median(planner), 1), "max": max(planner)} if planner else None,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deps", type=int, default=40)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--deadline", type=float, default=120.0, help="Draft deadline per run.")
    parser.add_argument("--warm", action="store_true", help="Also run the warm-cache pass (needs reachable registry to differ).")
    parser.add_argument("--cache-dir", default="", help="Registry metadata cache dir for warm runs (default: temp under output).")
    parser.add_argument("--json-out", default="", help="Optional JSON report path.")
    args = parser.parse_args()

    project = build_fixture(args.deps)
    out = Path(tempfile.mkdtemp(prefix="ub-bench-out-"))
    cache = Path(args.cache_dir).resolve() if args.cache_dir else out / "reg-cache"
    report: dict = {
        "python": sys.version.split()[0],
        "generator": "dependency_live_roadmap_generator.py (Draft mode)",
        "deps": args.deps,
        "deadlineSeconds": args.deadline,
        "hostRegistryReachable": "unknown-until-runs",
        "cold": None,
    }

    cold_rows, warm_rows = [], []
    # Prime the cache with one warm run before the measured warm repeats.
    if args.warm:
        run_once(project, "prime", out / "p", cache, args.deadline)
    for i in range(args.repeats):
        cold_rows.append(run_once(project, f"cold-{i}", out / f"cold-{i}", None, args.deadline))
    if args.warm:
        for i in range(args.repeats):
            warm_rows.append(run_once(project, f"warm-{i}", out / f"warm-{i}", cache, args.deadline))

    report["cold"] = summarize("cold", cold_rows)
    if args.warm:
        report["warm"] = summarize("warm", warm_rows)
    # Infer registry reachability from the evidence: no reachable registry,
    # every row's draftCounts reports registry-failed == deps.
    any_registry_failed = any((r.get("draftRegistryFailed") or 0) > 0 for r in cold_rows)
    report["hostRegistryReachable"] = not any_registry_failed

    def line(row: dict) -> str:
        return (
            f"{row['runId']:<8} wall={row['wallSec']:>7.2f}s "
            f"plannerElapsed={row['plannerElapsedMs']:>6}ms "
            f"{row['draftStatus']:<13} meta={row['metadataKnown']}/{row['metadataTotal']} "
            f"regFailed={row['draftRegistryFailed']} proposed={row['proposed']} review={row['needsReview']}"
        )

    print(f"Python {report['python']} | {generator_banner()}")
    print(f"deps={args.deps} deadline={args.deadline:g}s repeats={args.repeats} hostRegistryReachable={report['hostRegistryReachable']}")
    for row in cold_rows:
        print("COLD " + line(row))
    if args.warm:
        for row in warm_rows:
            print("WARM " + line(row))
    c = report["cold"]["wallSec"]["median"]
    print(f"cold median wall {c}s; planner elapsed median {report['cold']['plannerElapsedMs']['median']}ms")
    if args.warm:
        w = report["warm"]["wallSec"]["median"]
        print(f"warm median wall {w}s; planner elapsed median {report['warm']['plannerElapsedMs']['median']}ms")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report: {Path(args.json_out).resolve()}")
    print(f"fixture: {project}")
    return 0


def generator_banner() -> str:
    return "upgrade_blueprint.py -> dependency_live_roadmap_generator.py --draft-baseline"


if __name__ == "__main__":
    raise SystemExit(main())
