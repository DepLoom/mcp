import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from deploom_mcp.dependency_audit import AuditError
from deploom_mcp.planning_registry import planning_registry


class PlanningRegistryTests(TestCase):
    def test_configured_classic_route_requires_host_approval(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            url = "https://mirror.example.invalid/npm"
            (root / ".yarnrc").write_text(f'"registry" "{url}/"\n', encoding="utf-8")
            with patch.dict(os.environ, clear=True):
                with self.assertRaises(AuditError) as ctx:
                    planning_registry(root, None)
                self.assertEqual(ctx.exception.code, "project-registry-not-authorized")
            with patch.dict(os.environ, {"DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY": url}):
                self.assertEqual(planning_registry(root, None), url)
            self.assertEqual(planning_registry(root, "https://operator.example.invalid"), "https://operator.example.invalid")

    def test_yarn_yaml_route_must_match_npmrc_and_all_scopes(self):
        approved = "https://mirror.example.invalid/npm"
        with TemporaryDirectory() as directory, patch.dict(os.environ, {
            "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "1",
            "DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY": approved,
        }, clear=True):
            root = Path(directory).resolve()
            (root / ".npmrc").write_text(f"registry={approved}\n", encoding="utf8")
            yarn = root / ".yarnrc.yml"
            document = f"npmRegistryServer: {approved}/\nnpmScopes:\n  demo:\n    npmRegistryServer: {approved}\n"
            yarn.write_text(document, encoding="utf8")
            self.assertEqual(planning_registry(root, None), approved)
            yarn.write_text(document.replace(f"    npmRegistryServer: {approved}",
                                              "    npmRegistryServer: https://other.example.invalid"), encoding="utf8")
            with self.assertRaises(AuditError) as ctx:
                planning_registry(root, None)
            self.assertEqual(ctx.exception.code, "project-registry-ambiguous")

    def test_yarn_yaml_rejects_duplicates_aliases_and_nonliteral_registry(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            yarn = root / ".yarnrc.yml"
            for data, code in (
                ("npmRegistryServer: https://one.example.invalid\nnpmRegistryServer: https://two.example.invalid",
                 "project-registry-ambiguous"),
                ("source: &r https://mirror.example.invalid\nnpmRegistryServer: *r", "project-registry-unsupported"),
                ("npmRegistryServer: ${UNSET}", "invalid-registry"),
                ("npmRegistryServer: null", "invalid-registry"),
                ("npmScopes:\n  demo:\n    npmRegistryServer: https://mirror.example.invalid", "project-registry-ambiguous"),
            ):
                with self.subTest(code=code):
                    yarn.write_text(data, encoding="utf8")
                    with self.assertRaises(AuditError) as ctx:
                        planning_registry(root, None)
                    self.assertEqual(ctx.exception.code, code)

    def test_split_or_unresolved_routing_cannot_fall_back(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / ".npmrc"
            cases = (
                ("registry=https://registry.npmjs.org\n@x:registry=https://mirror.example.invalid", "project-registry-ambiguous"),
                ("//mirror.example.invalid/:_authToken=not-a-real-token", "project-registry-ambiguous"),
                ("registry=${PRIVATE_REGISTRY}", "invalid-registry"),
            )
            for raw, code in cases:
                with self.subTest(code=code):
                    config.write_text(raw, encoding="utf-8")
                    with self.assertRaises(AuditError) as ctx:
                        planning_registry(root, None)
                    self.assertEqual(ctx.exception.code, code)

    def test_plain_project_retains_default_and_explicit_credentials_are_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.assertIsNone(planning_registry(root, None))
            with self.assertRaises(AuditError) as ctx:
                planning_registry(root, "https://synthetic:placeholder@example.invalid")
            self.assertEqual(ctx.exception.code, "invalid-registry")

    def test_restricted_profile_uses_only_the_approved_registry(self):
        approved = "https://mirror.example.invalid/npm"
        with TemporaryDirectory() as directory, patch.dict(os.environ, {
            "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "1",
            "DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY": approved,
        }, clear=True):
            root = Path(directory).resolve()
            self.assertEqual(planning_registry(root, None), approved)
            self.assertEqual(planning_registry(root, approved + "/"), approved)
            (root / ".npmrc").write_text("registry=https://registry.npmjs.org", encoding="utf-8")
            for explicit in (None, "https://registry.npmjs.org"):
                with self.subTest(explicit=explicit), self.assertRaises(AuditError) as ctx:
                    planning_registry(root, explicit)
                self.assertEqual(ctx.exception.code, "project-registry-not-authorized")

    def test_registry_advisory_route_requires_exact_host_opt_in(self):
        from deploom_mcp.blueprint import BlueprintOptions, BlueprintPolicy, _exec_env

        approved = "https://mirror.example.invalid/npm"
        source_key = "DEPLOOM_DRAFT_REGISTRY_ADVISORY_SOURCE"
        with patch.dict(os.environ, {"DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "1",
                                     "DEPLOOM_MCP_REGISTRY_ADVISORY_SOURCE": approved,
                                     source_key: "https://untrusted.example.invalid"}, clear=True):
            for registry in (None, approved + "-other", "https://other.example.invalid/npm"):
                env = _exec_env(BlueprintPolicy(), "run", "ws", "project", BlueprintOptions(registry=registry))
                self.assertNotIn(source_key, env)
                self.assertEqual(env["DEPLOOM_DRAFT_DISABLE_EXTERNAL_EVIDENCE"], "1")
            env = _exec_env(BlueprintPolicy(), "run", "ws", "project", BlueprintOptions(registry=approved + "/"))
            self.assertEqual(env[source_key], approved)

    def test_sdk_cannot_fall_back_when_project_route_is_unapproved(self):
        from deploom_mcp.blueprint import BlueprintOptions, BlueprintPolicy, _prepare_generation

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / "project"
            project.mkdir()
            (project / "package.json").write_text("{}", encoding="utf-8")
            registry = "https://mirror.example.invalid/npm"
            (project / ".yarnrc").write_text(f'registry "{registry}"', encoding="utf-8")
            generator = root / "generator.py"
            generator.touch()
            options = BlueprintOptions(output_dir=str(root / "out"), generator_path=str(generator))
            with patch.dict(os.environ, {"DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY": "https://other.example.invalid"}):
                error, prepared = _prepare_generation(str(project), BlueprintPolicy(), options, None, None)
            self.assertEqual(error["code"], "project-registry-not-authorized")
            self.assertIsNone(prepared)
            self.assertFalse((root / "out").exists())
            with patch.dict(os.environ, {"DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY": registry,
                                         "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "1"}):
                error, prepared = _prepare_generation(str(project), BlueprintPolicy(), options, None, None)
            self.assertIsNone(error)
            self.assertEqual(prepared.argv[prepared.argv.index("--registry") + 1], registry)
            self.assertIn("--skip-release-intel", prepared.argv)
            self.assertEqual(prepared.env["DEPLOOM_DRAFT_DISABLE_EXTERNAL_EVIDENCE"], "1")
