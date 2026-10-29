"""Cheap offline negative tests for the independent DepLoom fixture oracle.

No npm install, LLM calls, network or agent execution in this unit module.
The actual positive npm ci/build/test + packaged stdio is a separate workflow.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile
import shutil
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from correctness_oracle import (
    VERSIONS, OracleFailure, frozen_policy, verify_candidate,
    CHALK_BASELINE, CHALK_TARGET, chalk_static_contract, verify_chalk_candidate, read_json,
)
from e2e_fixture import BUILD_JS, PACKAGE_JSON, SRC_INDEX_JS, VERIFY_JS
from e2e_pilot import canonical_fixture_files_intact
from correctness_preflight import seeded_registry


def write_json(path: pathlib.Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def lockfile(versions: dict[str, str]) -> dict:
    return {
        "name": "fixture-app", "version": "1.0.0",
        "lockfileVersion": 3, "requires": True,
        "packages": {
            "": {"name": "fixture-app", "version": "1.0.0",
                 "dependencies": dict(versions)},
            **{"node_modules/" + name: {
                "version": version,
                "resolved": f"http://127.0.0.1:4873/{name}/-/{name}-{version}.tgz",
                "integrity": "sha512-public-test-fixture-placeholder",
            } for name, version in versions.items()},
        },
    }


class IndependentOraclePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.baseline = self.root / "baseline"
        self.candidate = self.root / "candidate"
        for path, versions in (
            (self.baseline, PACKAGE_JSON["dependencies"]),
            (self.candidate, VERSIONS),
        ):
            path.mkdir()
            (path / "src").mkdir()
            (path / ".npmrc").write_text("registry=http://127.0.0.1:4873\n")
            (path / "build.js").write_text(BUILD_JS)
            (path / "verify.js").write_text(VERIFY_JS)
            (path / "src/index.js").write_text(SRC_INDEX_JS)
            manifest = copy.deepcopy(PACKAGE_JSON)
            manifest["dependencies"] = dict(versions)
            write_json(path / "package.json", manifest)
            write_json(path / "package-lock.json", lockfile(versions))

    def test_positive_policy_does_not_claim_install_success(self):
        frozen = frozen_policy(self.baseline, self.candidate)
        self.assertIn("verify.js", frozen)
        self.assertIn("build.js", frozen)
        self.assertNotIn("package-lock.json", frozen)

    def assert_rejected(self, reason: str):
        result = verify_candidate(self.baseline, self.candidate)
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(reason, result["reason"])
        self.assertNotIn("cleanInstall", result["checks"], "no install on unsafe input")

    def test_excluded_dependency_upgrade_rejected_before_install(self):
        manifest = json.loads((self.candidate / "package.json").read_text())
        manifest["dependencies"]["kontur-fixture-c"] = "4.0.0"
        write_json(self.candidate / "package.json", manifest)
        self.assert_rejected("POLICY_OR_EXCLUSION_VIOLATED")

    def test_changed_manifest_without_lock_is_rejected(self):
        write_json(self.candidate / "package-lock.json",
                   lockfile(PACKAGE_JSON["dependencies"]))
        self.assert_rejected("MANIFEST_LOCK_MISMATCH")

    def test_changed_build_or_test_scripts_rejected(self):
        (self.candidate / "verify.js").write_text("console.log('SMOKE_test_OK')")
        (self.candidate / "build.js").write_text("console.log('BUILD_OK')")
        self.assert_rejected("UNAUTHORIZED_SOURCE_CHANGE")

    def test_changed_source_even_if_agent_claims_correct_version_is_rejected(self):
        (self.candidate / "src/index.js").write_text("module.exports={versions:{a:'1.9.0',b:'2.1.0',c:'3.0.0'}}")
        self.assert_rejected("UNAUTHORIZED_SOURCE_CHANGE")

    def test_extra_file_rejected(self):
        (self.candidate / "fake-evidence.json").write_text('{"outcome":"PASSED"}')
        self.assert_rejected("UNAUTHORIZED_SOURCE_CHANGE")

    def test_foreign_and_secret_artifact_urls_rejected(self):
        for value in ("https://outside.invalid/a.tgz",
                      "http://127.0.0.1:4873/kontur-fixture-a/-/../secret.tgz",
                      "http://127.0.0.1:4873/kontur-fixture-a/-/a.tgz?token=private",
                      "http://user:pw@127.0.0.1:4873/kontur-fixture-a/-/a.tgz"):
            with self.subTest(value=value):
                lock = lockfile(VERSIONS)
                lock["packages"]["node_modules/kontur-fixture-a"]["resolved"] = value
                write_json(self.candidate / "package-lock.json", lock)
                self.assert_rejected("FOREIGN_OR_UNSAFE_LOCK_ARTIFACT")

    def test_windows_newline_normalization_keeps_canonical_source_proof(self):
        for rel in ("build.js", "verify.js", "src/index.js"):
            path = self.baseline / rel
            original = path.read_text(encoding="utf-8")
            path.write_bytes(original.replace("\n", "\r\n").encode("utf-8"))
        self.assertTrue(canonical_fixture_files_intact(self.baseline))

    def test_original_checkout_guard_is_no_longer_self_comparison(self):
        self.assertTrue(canonical_fixture_files_intact(self.baseline))
        altered = json.loads((self.baseline / "package.json").read_text())
        altered["dependencies"]["kontur-fixture-a"] = "1.9.0"
        write_json(self.baseline / "package.json", altered)
        self.assertFalse(canonical_fixture_files_intact(self.baseline))
        # A clean git status would not rescue a rewritten test in the source.
        write_json(self.baseline / "package.json", PACKAGE_JSON)
        (self.baseline / "verify.js").write_text("process.exit(0)")
        self.assertFalse(canonical_fixture_files_intact(self.baseline))


    def test_frozen_registry_never_synthesizes_unpublished_artifacts(self):
        # A wrong-version lockfile must not become magically installable.
        # Only localhost, no external registry or paid agent.
        with seeded_registry() as registry:
            good = (registry + "/kontur-fixture-a/-/kontur-fixture-a-1.9.0.tgz")
            with urlopen(good, timeout=3) as response:
                self.assertEqual(200, response.status)
                self.assertGreater(len(response.read()), 0)
            for uri in (
                registry + "/kontur-fixture-a/-/kontur-fixture-a-99.0.0.tgz",
                registry + "/kontur-fixture-a/-/different-1.9.0.tgz",
                registry + "/not-published/-/not-published-1.0.0.tgz",
            ):
                with self.subTest(uri=uri), self.assertRaises(HTTPError) as failed:
                    urlopen(uri, timeout=3)
                self.assertEqual(404, failed.exception.code)


class PublicChalkBreakingApiPolicyTests(unittest.TestCase):
    """No npm/network: prove the independent oracle rejects bypasses first."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = pathlib.Path(self.td.name)
        self.base = self.root / "baseline"
        self.candidate = self.root / "candidate"
        for folder, versions in ((self.base, CHALK_BASELINE),
                                 (self.candidate, CHALK_TARGET)):
            (folder / "src").mkdir(parents=True)
            for name, content in {
                ".npmrc": "registry=https://registry.npmjs.org/\n",
                "src/index.cjs": "module.exports=require('./format.cjs');\n",
                "src/format.cjs": "module.exports=()=> 'READY Ada';\n",
                "build.cjs": "console.log('build');\n",
                "test.cjs": "console.log('test');\n",
                "README.md": "public fixture\n",
            }.items():
                (folder / name).write_text(content, encoding="utf-8")
            pkg = {"name": "fixture", "version": "1.0.0",
                   "private": True, "scripts": {"test": "node test.cjs",
                                                "build": "node build.cjs"},
                   "dependencies": dict(versions)}
            write_json(folder / "package.json", pkg)
            entries = {"": {"dependencies": dict(versions)}}
            for dep, version in versions.items():
                entries["node_modules/" + dep] = {
                    "version": version,
                    "resolved": f"https://registry.npmjs.org/{dep}/-/{dep}-{version}.tgz",
                    "integrity": "sha512-" + "Q" * 84 + "==",
                }
            write_json(folder / "package-lock.json",
                       {"lockfileVersion": 3, "packages": entries})

    def test_valid_static_contract_and_pinned_exclusion(self):
        self.assertIn("build.cjs", chalk_static_contract(self.base, self.candidate))
        self.assertIn("test.cjs", chalk_static_contract(self.base, self.candidate))
        self.assertIn("src/format.cjs", chalk_static_contract(self.base, self.candidate))
        other = self.root / "untouched"
        shutil.copytree(self.base, other)
        self.assertIn(".npmrc", chalk_static_contract(
            self.base, other, baseline_mode=True))

    def _blocked(self, reason):
        result = verify_chalk_candidate(self.base, self.candidate)
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(reason, result["reason"])
        self.assertNotIn("install", result["checks"],
                         "unsafe source must be rejected before any npm command")

    def test_version_only_can_pass_static_but_not_migration_runtime(self):
        # Static package-lock consistency alone is insufficient. The full
        # replay must subsequently execute build, test and Node runtime.
        self.assertIn("src/format.cjs", chalk_static_contract(self.base, self.candidate))

    def test_script_tampering_and_test_deletion_are_rejected(self):
        (self.candidate / "test.cjs").unlink()
        self._blocked("FROZEN_SOURCE_SET_CHANGED")
        (self.candidate / "test.cjs").write_text("console.log('test');\n")
        p = read_json(self.candidate / "package.json")
        p["scripts"]["build"] = "node -e \"console.log('PASS')\""
        write_json(self.candidate / "package.json", p)
        self._blocked("BASELINE_OR_MANIFEST_SCOPE_CHANGED")

    def test_excluded_package_and_foreign_integrity_cannot_pass(self):
        p = read_json(self.candidate / "package.json")
        p["dependencies"]["is-number"] = "8.0.0"
        write_json(self.candidate / "package.json", p)
        self._blocked("CHALK_TARGET_OR_EXCLUSION_VIOLATION")
        p["dependencies"] = dict(CHALK_TARGET)
        write_json(self.candidate / "package.json", p)
        lock = read_json(self.candidate / "package-lock.json")
        lock["packages"]["node_modules/chalk"]["resolved"] = (
            "https://malicious.invalid/chalk-5.3.0.tgz")
        write_json(self.candidate / "package-lock.json", lock)
        self._blocked("CHALK_FOREIGN_LOCK_ARTIFACT")

    def test_deleted_or_added_source_does_not_fool_oracle(self):
        (self.candidate / "README.md").unlink()
        self._blocked("FROZEN_SOURCE_SET_CHANGED")
        (self.candidate / "README.md").write_text("public fixture\n")
        (self.candidate / "fake-evidence.json").write_text('{"outcome":"PASSED"}')
        self._blocked("FROZEN_SOURCE_SET_CHANGED")


if __name__ == "__main__":
    unittest.main()
