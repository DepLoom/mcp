"""Whole-bundle integrity regressions, using temporary source copies only."""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "skills" / "deploom-upgrade"
HELPER = runpy.run_path(str(REPO / "scripts" / "skill_integrity.py"))
verify = HELPER["verify_contract"]
write = HELPER["write_contract"]


class SkillIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skill-integrity-")
        self.root = Path(self.tmp.name) / "skill"
        shutil.copytree(SOURCE, self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_checked_in_manifest_covers_actual_content(self):
        self.assertEqual(verify(SOURCE), [])
        contract = json.loads((SOURCE / "contract.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["contractVersion"], "2.0.0")
        self.assertIn("scripts/validate_evidence.py", contract["files"])
        self.assertNotIn("contract.json", contract["files"])

    def test_reference_only_change_changes_bundle_identity(self):
        old = json.loads((self.root / "contract.json").read_text(encoding="utf-8"))
        path = self.root / "references" / "preflight.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nSynthetic changed rule.\n", encoding="utf-8")
        self.assertTrue(any("preflight.md" in p for p in verify(self.root)))
        new = write(self.root)
        self.assertNotEqual(old["bundleSha256"], new["bundleSha256"])
        self.assertEqual(old["files"]["SKILL.md"], new["files"]["SKILL.md"])
        self.assertEqual(verify(self.root), [])

    def test_missing_or_unlisted_reference_is_detected(self):
        (self.root / "references" / "preflight.md").unlink()
        (self.root / "references" / "unlisted.md").write_text("synthetic", encoding="utf-8")
        errors = verify(self.root)
        self.assertTrue(any("missing file: references/preflight.md" in e for e in errors))
        self.assertTrue(any("unlisted canonical file: references/unlisted.md" in e for e in errors))

    def test_generation_is_path_independent_and_repeatable(self):
        expected = (self.root / "contract.json").read_bytes()
        write(self.root)
        self.assertEqual((self.root / "contract.json").read_bytes(), expected)
        write(self.root)
        self.assertEqual((self.root / "contract.json").read_bytes(), expected)

    def test_cli_detects_drift_and_deliberate_write_repairs_manifest(self):
        (self.root / "examples" / "plan-only.md").write_text("synthetic change\n", encoding="utf-8")
        command = [sys.executable, str(REPO / "scripts" / "check_skill.py"), "--skill-dir", str(self.root)]
        failed = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertEqual(failed.returncode, 1)
        fixed = subprocess.run([*command, "--write"], capture_output=True, text=True, timeout=20)
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        self.assertEqual(verify(self.root), [])


if __name__ == "__main__":
    unittest.main()
