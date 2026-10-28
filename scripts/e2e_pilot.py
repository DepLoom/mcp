"""OpenCode E2E pilot driver for the deploom-upgrade skill.

Owns the whole lifecycle so nothing (registry, npm, git, opencode) leaks
between tool calls:

  python scripts/e2e_pilot.py --phase all --out <dir> [--fixture <dir>]

Phases:
  make   - build the disposable fixture (npm install + git baseline)
  prep   - install the canonical skill into the fixture + write opencode.json (MCP)
  plan   - preflight: real Core through the real boundary, save plan.json
  pilot  - run `opencode run` headless with the pilot prompt (transcript saved)
  verify - check worktree/manifest/lockfile/tests/evidence against the plan
  all    - make -> prep -> plan -> pilot -> verify (default)

The registry (scripts/e2e_registry.py) runs as this driver's child on
127.0.0.1:PORT and is stopped at the end. All checks are derived from the real
plan.json (what the pinned Core actually decided), so a changed Core verdict
changes expectations instead of producing a false pass.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "src"
REGISTRY_SCRIPT = REPO / "scripts" / "e2e_registry.py"
SEED = REPO / "scripts" / "e2e_seed.json"
FAKE = REPO / "tests" / "fixtures" / "fake_core" / "dependency_live_roadmap_generator.py"
REAL_GENERATOR = REPO / "upstream" / "deploom" / "dependency_live_roadmap_generator.py"
INSTALLER = REPO / "scripts" / "install_skill.py"
BASE_NAME = "deploom-upgrade"

sys.path.insert(0, str(SRC))


def run(cmd: list[str], cwd: pathlib.Path | None = None, timeout: int = 900,
        capture: bool = False) -> subprocess.CompletedProcess:
    if capture:
        return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, timeout=timeout)


class Registry:
    def __init__(self, port: int, log: pathlib.Path, err: pathlib.Path, python: str) -> None:
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.proc = None
        self.python = python

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [self.python, str(REGISTRY_SCRIPT), "--port", str(self.port), "--seed", str(SEED)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"{self.base}/-/ping", timeout=1) as r:  # noqa: S310 - loopback
                    if r.status == 200:
                        return
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        raise RuntimeError("registry did not become ready")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                self.proc.kill()


def build_fixture(fixture: pathlib.Path, registry: str, python: str) -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    from e2e_fixture import make_fixture
    if fixture.exists():
        shutil.rmtree(fixture)
    fixture.mkdir(parents=True)
    make_fixture(str(fixture), registry)


def prep_project(fixture: pathlib.Path, repo: pathlib.Path, python: str, out: pathlib.Path) -> None:
    skills_root = fixture / ".opencode" / "skills"
    run([python, str(INSTALLER), "--scope", "project", "--target", str(skills_root / BASE_NAME)],
        cwd=fixture, capture=True)
    cfg = {
        "mcp": {
            "deploom": {
                "type": "local",
                "command": [python, "-m", "deploom_mcp"],
                "enabled": True,
                "environment": {
                    "PYTHONPATH": str(repo / "src"),
                    "DEPLOOM_MCP_GENERATOR_PATH": str(REAL_GENERATOR),
                },
            }
        }
    }
    (fixture / "opencode.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    run(["git", "add", ".opencode", "opencode.json"], cwd=fixture, capture=True)
    run(["git", "commit", "-m", "pilot scaffolding (skill + mcp config)"], cwd=fixture, capture=True)


def plan_fixture(fixture: pathlib.Path, registry: str, out: pathlib.Path) -> dict:
    from deploom_mcp.server import get_upgrade_plan
    result = get_upgrade_plan(
        project_path=str(fixture),
        target_level="yellow",
        min_lag_ok_pct=80,
        registry=registry,
        registry_metadata_ttl=3600.0,
        exclude=["kontur-fixture-c"],
    )
    if not result.get("ok"):
        raise SystemExit(f"plan failed: {json.dumps(result, indent=2)}")
    save = {k: result.get(k) for k in ("ok", "status", "summary", "policy", "boundaryNote", "dependencies", "truncation")}
    save["recommendationGroups"] = result.get("recommendationGroups")
    (out / "plan.json").write_text(json.dumps(save, indent=2), encoding="utf-8")
    print(json.dumps({"ok": result.get("ok"), "status": result.get("status"),
                      "summary": result.get("summary"), "policy": result.get("policy"),
                      "groups": result.get("recommendationGroups")}, indent=2))
    return result


def _opencode_exe() -> str:
    explicit = os.environ.get("OPENCODE_BIN")
    if explicit:
        return explicit
    shim = shutil.which("opencode")
    if shim:
        p = pathlib.Path(shim)
        candidate = (p.parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe")
        if candidate.exists():
            return str(candidate)
        if p.suffix.lower() == ".exe":
            return str(p)
    return "opencode"


def run_pilot(fixture: pathlib.Path, prompt: pathlib.Path, out: pathlib.Path,
              model: str) -> subprocess.CompletedProcess:
    exe = _opencode_exe()
    transcript = (out / "transcript.txt").open("w", encoding="utf-8", errors="replace")
    proc = subprocess.run(
        [exe, "run", "-m", model, "--auto", "--dir", str(fixture), prompt.read_text(encoding="utf-8")],
        cwd=str(fixture),
        stdout=transcript,
        stderr=subprocess.STDOUT,
        timeout=1200,
    )
    transcript.close()
    return proc


def rows_from_plan(plan: dict) -> list[dict]:
    return list(plan.get("dependencies") or [])


def _deduce_worktree(lines: list[str]) -> pathlib.Path | None:
    current: str | None = None
    for line in lines:
        if line.startswith("worktree "):
            current = line[len("worktree "):].strip()
        elif line.startswith("branch refs/heads/deploom-upgrade") and current:
            return pathlib.Path(current)
    return None


def verify_e2e(fixture: pathlib.Path, out: pathlib.Path, plan: dict) -> int:
    checks: list[tuple[str, bool, str]] = []
    deps = rows_from_plan(plan)
    proposed = {d["package"]: d.get("target") for d in deps if d.get("status") == "PROPOSED"}
    excluded = {d["package"]: d.get("current") for d in deps if d.get("status") == "EXCLUDED"}
    tree = _deduce_worktree(run(["git", "-C", str(fixture), "worktree", "list", "--porcelain"], capture=True).stdout.splitlines())
    checks.append(("worktree created outside project", tree is not None, str(tree)))

    fixture_manifest = json.loads((fixture / "package.json").read_text(encoding="utf-8"))["dependencies"]
    unchanged = {name: fixture_manifest.get(name) for name in list(proposed) + list(excluded)}
    checks.append(("original checkout deps untouched", unchanged == fixture_manifest, str(fixture_manifest)))

    proc = run(["git", "-C", str(fixture), "status", "--porcelain"], capture=True)
    dirty = [l for l in proc.stdout.splitlines() if l.strip()
             and not l.startswith("?? .opencode") and not l.startswith("?? opencode.json")]
    checks.append(("original checkout clean (only scaffolding)", not dirty, "; ".join(dirty[:4])))

    if tree:
        wt = json.loads((tree / "package.json").read_text(encoding="utf-8"))["dependencies"]
        for name, target in proposed.items():
            checks.append((f"worktree {name} -> {target}", wt.get(name) == target, f"actual {wt.get(name)}"))
        for name, current in excluded.items():
            checks.append((f"excluded {name} unchanged in worktree", wt.get(name) == current,
                           f"actual {wt.get(name)}"))
        test = run(["cmd", "/c", "npm", "test"], cwd=tree, capture=True, timeout=300)
        ok = test.returncode == 0 and "SMOKE_test_OK" in test.stdout
        checks.append(("npm test in worktree (exclusion invariant)", ok,
                       (test.stdout.strip().splitlines()[-1] if test.stdout.strip() else repr(test.stderr[-300:]))))
        evidence_candidates = [p for p in tree.rglob("*.json") if "evidence" in p.name.lower()]
        evidence = evidence_candidates[0] if evidence_candidates else None
        ev = None
        if evidence:
            try:
                ev = json.loads(evidence.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                checks.append(("evidence JSON parses", False, repr(exc)))
        checks.append(("evidence JSON present", evidence is not None and ev is not None, str(evidence)))
        if ev:
            checks.append(("evidence outcome PASSED", ev.get("outcome") == "PASSED", str(ev.get("outcome"))))
            updates = ev.get("updatedDependencies") or []
            checks.append(("evidence records both updates",
                           {u.get("package") for u in updates}.issuperset(set(proposed)),
                           json.dumps(updates)))
            checks.append(("evidence records c excluded",
                           any(d.get("package") in excluded and d.get("status") == "EXCLUDED"
                               for d in ev.get("skippedDependencies") or []),
                           json.dumps(ev.get("skippedDependencies"))))
    transcript = out / "transcript.txt"
    checks.append(("transcript captured", transcript.exists() and transcript.stat().st_size > 2000, "size"))

    failed = 0
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  [{detail[:160]}]")
        failed += 0 if ok else 1
    (out / "verify.json").write_text(json.dumps([c for c in checks], indent=2), encoding="utf-8")
    return 0 if failed == 0 else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--port", type=int, default=4873)
    ap.add_argument("--model", help="Configured provider/model identifier for pilot execution")
    ap.add_argument("--prompt", default=str(REPO / "scripts" / "e2e_prompt.md"))
    args = ap.parse_args()
    if args.phase in ("all", "pilot") and not args.model:
        ap.error("--model is required for phases that execute a model")
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fixture = pathlib.Path(args.fixture).resolve()
    registry = Registry(args.port, out / "reg.log", out / "reg.err",
                        sys.executable)
    python = sys.executable
    try:
        registry.start()
        base = registry.base
        if args.phase in ("all", "make"):
            build_fixture(fixture, base, python)
        if args.phase in ("all", "prep"):
            prep_project(fixture, REPO, python, out)
        if args.phase in ("all", "plan"):
            plan = plan_fixture(fixture, base, out)
        elif (out / "plan.json").exists():
            plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
        if args.phase in ("all", "pilot"):
            proc = run_pilot(fixture, pathlib.Path(args.prompt), out, args.model)
            print(f"opencode exit={proc.returncode}")
        if args.phase in ("all", "verify"):
            if not (out / "plan.json").exists():
                plan = plan_fixture(fixture, base, out)
            else:
                plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
            code = verify_e2e(fixture, out, plan)
            print(f"VERIFY={'PASS' if code == 0 else 'FAIL'}")
            raise SystemExit(code)
    finally:
        registry.stop()


if __name__ == "__main__":
    main()
