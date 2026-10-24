"""Installer behavior tests for scripts/install_skill.py.

Run the real installer (subprocess, no network, no planner) against temp
projects to pin: reproducible byte-identical installs, idempotent re-run,
safe handling of a foreign existing skill, dry-run writing nothing, user-scope
with an overridden home, --verify, and that MCP config is never written by the
installer (it is a separate, explicit step).
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = REPO / "scripts" / "install_skill.py"
CANONICAL = REPO / "skills" / "deploom-upgrade"


def run_installer(target_root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    project = str(target_root)
    return subprocess.run(
        [sys.executable, str(INSTALLER), *args],
        cwd=str(target_root),
        capture_output=True,
        text=True,
        timeout=120,
    )


def canonical_files() -> list[pathlib.Path]:
    return sorted(p for p in CANONICAL.rglob("*") if p.is_file())


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="skill-install-")
        self.project = pathlib.Path(self._tmp.name) / "proj"
        self.project.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def assert_installed_identical(self, target: pathlib.Path) -> None:
        missing = []
        mismatched = []
        for src in canonical_files():
            rel = src.relative_to(CANONICAL)
            dst = target / rel
            if not dst.exists():
                missing.append(str(rel))
            elif dst.read_bytes() != src.read_bytes():
                mismatched.append(str(rel))
        self.assertEqual((missing, mismatched), ([], []), "installed tree must be byte-identical to canonical")

    def test_fresh_install_is_byte_identical(self) -> None:
        result = run_installer(self.project, "--client", "opencode", "--scope", "project", "--project", str(self.project))
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.project / ".opencode" / "skills" / "deploom-upgrade"
        self.assertTrue(target.is_dir())
        self.assert_installed_identical(target)
        self.assertEqual(len(canonical_files()), len(list(target.rglob("*.md"))))

    def test_reinstall_is_idempotent(self) -> None:
        for _ in range(2):
            result = run_installer(self.project, "--client", "opencode", "--scope", "project", "--project", str(self.project))
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("installed", result.stdout.lower())
        self.assert_installed_identical(self.project / ".opencode" / "skills" / "deploom-upgrade")

    def test_dry_run_writes_nothing(self) -> None:
        result = run_installer(self.project, "--client", "opencode", "--scope", "project", "--project", str(self.project), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry-run", result.stdout.lower())
        target = self.project / ".opencode" / "skills" / "deploom-upgrade"
        self.assertFalse(target.exists(), "dry-run must not create the target")

    def test_foreign_skill_aborts_without_force_and_replaces_with_force(self) -> None:
        target = self.project / ".opencode" / "skills" / "deploom-upgrade"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("---\nname: other-skill\n---\n# other\n", encoding="utf-8")
        blocked = run_installer(self.project, "--client", "opencode", "--scope", "project", "--project", str(self.project))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertNotEqual((target / "SKILL.md").read_text(encoding="utf-8"), (CANONICAL / "SKILL.md").read_text(encoding="utf-8"), "must not overwrite a foreign skill without consent")
        forced = run_installer(self.project, "--client", "opencode", "--scope", "project", "--project", str(self.project), "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assert_installed_identical(target)

    def test_user_scope_respects_config_home(self) -> None:
        home = pathlib.Path(self._tmp.name) / "home"
        for client, rel in (("claude-code", pathlib.Path(".claude") / "skills"), ("codex", pathlib.Path(".agents") / "skills")):
            result = run_installer(self.project, "--client", client, "--scope", "user", "--config-home", str(home))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_installed_identical(home / rel / "deploom-upgrade")
        opened = run_installer(self.project, "--client", "opencode", "--scope", "user", "--config-home", str(home))
        self.assertEqual(opened.returncode, 0, opened.stderr)
        self.assert_installed_identical(home / ".config" / "opencode" / "skills" / "deploom-upgrade")

    def test_verify_passes_in_place(self) -> None:
        run_installer(self.project, "--client", "opencode", "--scope", "project", "--project", str(self.project))
        result = run_installer(self.project, "--client", "opencode", "--scope", "project", "--project", str(self.project), "--verify")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout.lower())

    def test_installer_writes_no_mcp_config(self) -> None:
        run_installer(self.project, "--client", "opencode", "--scope", "project", "--project", str(self.project))
        for candidate in ("opencode.json", ".mcp.json", "config.toml"):
            self.assertFalse((self.project / candidate).exists(), f"installer must not write {candidate}")

    def test_print_mcp_config_writes_nothing(self) -> None:
        result = run_installer(self.project, "--client", "codex", "--print-mcp-config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("deploom_mcp", result.stdout)
        self.assertEqual(list(self.project.iterdir()), [], "print-mcp-config must not create files")

    def test_target_override(self) -> None:
        custom = pathlib.Path(self._tmp.name) / "custom-skill"
        result = run_installer(self.project, "--scope", "project", "--target", str(custom))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_installed_identical(custom)


class InstallerDeterminismTests(unittest.TestCase):
    def test_canonical_files_digest_stable(self) -> None:
        digests: dict[str, str] = {}
        for src in sorted(CANONICAL.rglob("*")):
            if src.is_file():
                digests[src.relative_to(CANONICAL).as_posix()] = sha256(src.read_bytes())
        self.assertGreaterEqual(len(digests), 9)
        self.assertIn("SKILL.md", digests)
        for rel, digest in digests.items():
            self.assertEqual(len(digest), 64, f"unexpected digest for {rel}")

    def test_every_example_json_block_is_valid_json(self) -> None:
        for path in (CANONICAL / "references").glob("*.md"):
            text = path.read_text(encoding="utf-8")
            for i, match in enumerate(re.finditer(r"```jsonc?\n(.*?)\n```", text, re.DOTALL)):
                body = match.group(1)
                if "…" in body or "..." in body:
                    continue
                try:
                    json.loads(body)
                except Exception as exc:  # noqa: BLE001 - test tolerance
                    self.fail(f"invalid json block in {path.name} block {i}: {exc}")


if __name__ == "__main__":
    unittest.main()
