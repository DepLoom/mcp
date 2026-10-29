"""Integration contract for the repinned registry diagnostic Core.

Uses the real source Core, not the fake Core used by adapter unit tests. It
avoids network and never changes package manifests or the solver result.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "upstream" / "deploom"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from build_support import validate_release, inventory_digest


class RepinnedCoreRegistryContractTests(unittest.TestCase):
    def test_clean_submodule_revision_is_exactly_the_release_pin(self) -> None:
        release = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8"))
        expected = release["core"]["revision"]
        gitlink = subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-files", "--stage", "upstream/deploom"],
            text=True,
        ).strip().split()
        self.assertEqual(gitlink[:2], ["160000", expected])
        self.assertEqual(
            subprocess.check_output(["git", "-C", str(CORE_DIR), "rev-parse", "HEAD"], text=True).strip(),
            expected,
        )

    def test_full_release_inventory_includes_new_core_module(self) -> None:
        release = validate_release(ROOT)
        files = release["core"]["files"]
        self.assertIn("registry_candidate_diagnostics.py", files)
        self.assertIn("dependency_live_roadmap_generator.py", files)
        self.assertGreaterEqual(len(files), 134)
        self.assertEqual(inventory_digest(files), release["core"]["contentSha256"])
        for name in ("registry_candidate_diagnostics.py", "dependency_live_roadmap_generator.py"):
            self.assertEqual(hashlib.sha256((CORE_DIR / name).read_bytes()).hexdigest(), files[name])

    def test_pinned_core_guard_denies_encoded_traversal_and_userinfo(self) -> None:
        from registry_candidate_diagnostics import safe_registry_artifact_url
        registry = "https://nexus.invalid/repository/npm"
        self.assertTrue(safe_registry_artifact_url(registry,registry+"/a.tgz"))
        for url in (registry+"/../private/a.tgz", registry+"/%2e%2e/a.tgz",
                    registry+"/%252e%252e/a.tgz", registry+"/a.tgz?token=secret",
                    "https://user:password@nexus.invalid/repository/npm/a.tgz",
                    "https://nexus.invalid:invalid/repository/npm/a.tgz"):
            with self.subTest(url=url):
                self.assertFalse(safe_registry_artifact_url(registry,url))

    def test_valid_vcs_lockfile_resolution_is_separate_from_registry_admission(self) -> None:
        from lockfile_consistency import registry_artifact_url_allowed
        from registry_candidate_diagnostics import safe_registry_artifact_url
        registry = "https://nexus.invalid/repository/npm"
        uri = "git+ssh://git@github.com/example/project.git#v1"
        self.assertTrue(registry_artifact_url_allowed(registry, uri))
        self.assertFalse(safe_registry_artifact_url(registry, uri))
        self.assertFalse(registry_artifact_url_allowed(registry, "unknown://outside.invalid/pkg"))

    def test_authoritative_registry_guard_remains_in_charge_of_versions(self) -> None:
        import dependency_live_roadmap_generator as core
        from registry_candidate_diagnostics import no_target_diagnostic_code, summarize_structural_candidates

        registry = "http://127.0.0.1:48997/frozen"
        client = core.LiveDataClient(registry, timeout=2, batch_size=2, sleep_sec=0,
                                     use_system_proxy=False)
        version_urls = {
            "1.0.0": "https://registry.npmjs.org/demo/-/demo-1.0.0.tgz",
            "1.2.0": "https://registry.npmjs.org/demo/-/demo-1.2.0.tgz",
        }
        metadata = {"versions": {v: {"dist": {"tarball": url}} for v, url in version_urls.items()}}
        structurally_eligible, _ = client.registry_structural_candidates(metadata, version_urls.keys())
        diagnostic = summarize_structural_candidates(
            registry, metadata, version_urls.keys(), client.registry_artifact_url_allowed,
            current="1.0.0",
        )
        self.assertEqual(structurally_eligible, [])
        self.assertEqual(diagnostic["rejectionCounts"], {"FOREIGN_TARBALL_ORIGIN": 2})
        self.assertEqual(
            no_target_diagnostic_code("no-target", diagnostic), "NO_INSTALLABLE_ARTIFACT"
        )
        self.assertEqual(diagnostic["physicalProbe"]["unprobed"], True)
        # Compatible, matching tarballs are still accepted by the same guard.
        matching = {"versions": {"1.2.0": {"dist": {
            "tarball": "http://127.0.0.1:48997/frozen/demo/-/demo-1.2.0.tgz"
        }}}}
        allowed, _ = client.registry_structural_candidates(matching, ["1.2.0"])
        self.assertEqual(allowed, ["1.2.0"])
        accepted = summarize_structural_candidates(
            registry, matching, ["1.2.0"], client.registry_artifact_url_allowed,
            current="1.0.0",
        )
        self.assertEqual(accepted["acceptedUpgradeCandidates"], 1)
        self.assertIsNone(no_target_diagnostic_code("proposed", accepted))


if __name__ == "__main__":
    unittest.main()
