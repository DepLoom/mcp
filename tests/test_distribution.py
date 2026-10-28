"""Resource boundary regressions: independent of Git and a live registry."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_support import copy_resources, digest, inventory_digest, source_inventory, validate_release
from deploom_mcp import distribution


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def fixture(self):
        core = self.write("upstream/deploom/dependency_live_roadmap_generator.py", "print('synthetic core')\n")
        self.write("upstream/deploom/private-local.json", '{"neverPackage":true}')
        schema = self.write("contracts/upgrade-blueprint.schema.json", '{"type":"object"}')
        skill = self.write("skills/deploom-upgrade/SKILL.md", "---\nname: synthetic\n---\n")
        reference = self.write("skills/deploom-upgrade/references/contract.md", "synthetic execution contract\n")
        files = {"SKILL.md": digest(skill.read_bytes()), "references/contract.md": digest(reference.read_bytes())}
        contract = self.write("skills/deploom-upgrade/contract.json", json.dumps({
            "contractVersion": "2.0.0", "files": files, "bundleSha256": inventory_digest(files),
        }))
        self.write("skills/deploom-upgrade/local-note.md", "never package unlisted files")
        self.write("pyproject.toml", '[project]\nversion="0.2.0"\n')
        for name in ("install_skill.py", "skill_integrity.py", "check_skill.py"):
            self.write(f"scripts/{name}", "# synthetic\n")
        core_files = {"dependency_live_roadmap_generator.py": digest(core.read_bytes())}
        manifest = {"mcpVersion": "0.2.0", "core": {"files": core_files, "contentSha256": inventory_digest(core_files)},
                    "schemaSha256": digest(schema.read_bytes()), "skillManifestSha256": digest(contract.read_bytes())}
        self.write("release-manifest.json", json.dumps(manifest))
        return manifest

    def test_bundle_uses_explicit_inventory_without_git_or_unlisted_files(self):
        self.fixture()
        dest = self.root / "wheel" / "deploom_mcp"
        copy_resources(self.root, dest)
        self.assertTrue((dest / "_core/dependency_live_roadmap_generator.py").is_file())
        self.assertFalse((dest / "_core/private-local.json").exists())
        self.assertFalse((dest / "resources/skills/deploom-upgrade/local-note.md").exists())
        with patch.object(distribution, "_package_root", return_value=dest):
            self.assertEqual(distribution.resolve_core_dir(), dest / "_core")
            self.assertTrue(distribution.blueprint_schema_path().is_file())
            self.assertTrue(distribution.release_manifest_path().is_file())
            self.assertTrue((distribution.skill_root() / "contract.json").is_file())

    def test_core_bytes_must_match_release_before_bundling(self):
        self.fixture()
        self.write("upstream/deploom/dependency_live_roadmap_generator.py", "changed\n")
        with self.assertRaisesRegex(ValueError, "Core release resource changed"):
            validate_release(self.root)

    def test_rebuild_removes_stale_generated_resources(self):
        self.fixture()
        self.write("wheel/deploom_mcp/_core/stale-local.json", "must not survive a rebuild")
        self.write("wheel/deploom_mcp/resources/skills/deploom-upgrade/stale.md", "must not survive a rebuild")
        dest = self.root / "wheel/deploom_mcp"
        copy_resources(self.root, dest)
        self.assertFalse((dest / "_core/stale-local.json").exists())
        self.assertFalse((dest / "resources/skills/deploom-upgrade/stale.md").exists())

    def test_source_distribution_also_excludes_unlisted_resources(self):
        self.fixture()
        files = source_inventory(self.root, ["README.md", "upstream/deploom/private-local.json",
                                            "skills/deploom-upgrade/local-note.md"])
        self.assertIn("upstream/deploom/dependency_live_roadmap_generator.py", files)
        self.assertIn("skills/deploom-upgrade/references/contract.md", files)
        self.assertIn("skills/deploom-upgrade/contract.json", files)
        self.assertNotIn("upstream/deploom/private-local.json", files)
        self.assertNotIn("skills/deploom-upgrade/local-note.md", files)

    def test_build_hook_refuses_to_clean_source_package(self):
        self.fixture()
        with self.assertRaisesRegex(ValueError, "canonical source"):
            copy_resources(self.root, self.root / "src/deploom_mcp")

    def test_reference_drift_fails_even_when_top_level_skill_unchanged(self):
        self.fixture()
        self.write("skills/deploom-upgrade/references/contract.md", "changed reference\n")
        with self.assertRaisesRegex(ValueError, "Skill release resource changed"):
            validate_release(self.root)

    def test_schema_drift_fails_closed(self):
        self.fixture()
        self.write("contracts/upgrade-blueprint.schema.json", "{}")
        with self.assertRaisesRegex(ValueError, "release resource changed"):
            validate_release(self.root)

    def test_inventory_cannot_escape_core_directory(self):
        manifest = self.fixture()
        self.write("upstream/outside.py", "do not package")
        files = manifest["core"]["files"]
        files["../outside.py"] = digest(b"do not package")
        manifest["core"]["contentSha256"] = inventory_digest(files)
        self.write("release-manifest.json", json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "unsafe release resource"):
            validate_release(self.root)

    def test_checkout_locator_keeps_existing_editable_layout(self):
        package = self.root / "src" / "deploom_mcp"
        package.mkdir(parents=True)
        with patch.object(distribution, "_package_root", return_value=package):
            self.assertEqual(distribution.resolve_core_dir(), self.root / "upstream/deploom")
            self.assertEqual(distribution.blueprint_schema_path(), self.root / "contracts/upgrade-blueprint.schema.json")


if __name__ == "__main__":
    unittest.main()
