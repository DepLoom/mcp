#!/usr/bin/env python3
"""Run native audit and MCP protocol gates from an installed wheel, outside checkout."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    wheels = sorted((ROOT / "dist").glob("deploom_mcp-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected exactly one built MCP wheel in dist/")
    with tempfile.TemporaryDirectory(prefix="deploom-native-audit-wheel-") as temporary:
        outside = Path(temporary).resolve()
        venv.EnvBuilder(with_pip=True).create(outside / "venv")
        python = outside / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME"):
            env.pop(key, None)
        env["DEPLOOM_NATIVE_AUDIT_TESTS"] = "1"
        subprocess.run([str(python), "-m", "pip", "install", str(wheels[0])], cwd=outside, env=env, check=True)
        subprocess.run([str(python), "-c", "import pathlib, deploom_mcp; p=pathlib.Path(deploom_mcp.__file__).resolve(); print(p); assert not p.is_relative_to(pathlib.Path(" + repr(str(ROOT)) + ").resolve())"], cwd=outside, env=env, check=True)
        for pattern in ("test_dependency_audit.py", "test_audit_native.py", "test_audit_stdio.py"):
            subprocess.run([str(python), "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-p", pattern, "-v"], cwd=outside, env=env, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
