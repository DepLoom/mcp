"""System proxy precedence, Core CLI flag, audit process env and real stdio.

Fake Core is used *only* for the stdio transport receipt. The live-OSV
test is a separate opt-in check and must never be claimed to pass here.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters, stdio_client

from deploom_mcp.blueprint import BlueprintOptions, BlueprintPolicy, _prepare_argv_full
from deploom_mcp.dependency_audit import validate_request
from deploom_mcp.proxy_settings import (
    ProxySettingError, native_proxy_environment, resolve_system_proxy,
)
from deploom_mcp.server import _build_request, get_upgrade_plan
from deploom_mcp.target_assessment import target_assessment, target_decision_evidence

ROOT = Path(__file__).resolve().parents[1]
FAKE_CORE = ROOT / "tests" / "fixtures" / "fake_core" / "dependency_live_roadmap_generator.py"


class SystemProxyPolicyTests(unittest.TestCase):
    def test_absent_setting_keeps_false(self):
        self.assertEqual((False, "default"), resolve_system_proxy(None, environ={}))

    def test_global_true_false_and_accepted_bool_spellings(self):
        for text in ("true", "TRUE", "1", "yes", "ON"):
            with self.subTest(text=text):
                self.assertEqual((True, "environment"), resolve_system_proxy(
                    None, environ={"DEPLOOM_MCP_USE_SYSTEM_PROXY": text}))
        for text in ("false", "FALSE", "0", "no", "OFF"):
            with self.subTest(text=text):
                self.assertEqual((False, "environment"), resolve_system_proxy(
                    None, environ={"DEPLOOM_MCP_USE_SYSTEM_PROXY": text}))

    def test_explicit_tool_bool_overrides_both_env_directions(self):
        self.assertEqual((False, "tool"), resolve_system_proxy(
            False, environ={"DEPLOOM_MCP_USE_SYSTEM_PROXY": "true"}))
        self.assertEqual((True, "tool"), resolve_system_proxy(
            True, environ={"DEPLOOM_MCP_USE_SYSTEM_PROXY": "false"}))
        self.assertEqual((True, "tool"), resolve_system_proxy(
            True, environ={"DEPLOOM_MCP_USE_SYSTEM_PROXY": "not-a-valid-value"}))

    def test_invalid_input_fails_without_silent_proxy(self):
        for value in ("", "maybe", "system"):
            with self.subTest(value=value), self.assertRaises(ProxySettingError):
                resolve_system_proxy(None, environ={"DEPLOOM_MCP_USE_SYSTEM_PROXY": value})
        for value in (1, 0, "true", [], {}):
            with self.subTest(value=value), self.assertRaises(ProxySettingError):
                resolve_system_proxy(value, environ={})

    def test_core_argv_is_opt_in_and_explicit_false_omits_flag(self):
        def argv(use):
            return _prepare_argv_full(
                Path("/dummy-core/dependency_live_roadmap_generator.py"),
                Path("/tmp/projects.txt"), Path("/tmp/artifacts"),
                Path("/tmp/state"), Path("/tmp/history"),
                "run-proxy", "workspace", "project",
                BlueprintPolicy(), BlueprintOptions(use_system_proxy=use),
            )
        self.assertNotIn("--use-system-proxy", argv(False))
        selected = argv(True)
        self.assertEqual(1, selected.count("--use-system-proxy"))
        self.assertNotIn("--no-use-system-proxy", selected)
        self.assertNotIn("--use-system-proxy=true", selected)

    def test_mcp_request_builds_core_options_with_env_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            (project / "package.json").write_text('{"name":"proxy-test"}', encoding="utf-8")
            values = {
                "project_path": str(project), "target_level": "yellow",
                "min_lag_ok_pct": 80, "deadline_seconds": 20,
                "include_prerelease": False, "registry": None,
                "registry_metadata_ttl": 0, "output_dir": str(root / "out"),
                "exclude": None,
            }
            with patch.dict(os.environ, {"DEPLOOM_MCP_STATE_DIR": str(root / "state"),
                                         "DEPLOOM_MCP_USE_SYSTEM_PROXY": "true"}):
                err, req = _build_request(**values)
                self.assertIsNone(err)
                self.assertTrue(req["options"].use_system_proxy)
                self.assertEqual("environment", req["systemProxySource"])
                err, req = _build_request(**values, use_system_proxy=False)
                self.assertIsNone(err)
                self.assertFalse(req["options"].use_system_proxy)
                self.assertEqual("tool", req["systemProxySource"])
            with patch.dict(os.environ, {"DEPLOOM_MCP_STATE_DIR": str(root / "state"),
                                         "DEPLOOM_MCP_USE_SYSTEM_PROXY": "false"}):
                err, req = _build_request(**values, use_system_proxy=True)
                self.assertIsNone(err)
                self.assertTrue(req["options"].use_system_proxy)
            with patch.dict(os.environ, {"DEPLOOM_MCP_STATE_DIR": str(root / "state"),
                                         "DEPLOOM_MCP_USE_SYSTEM_PROXY": "not-a-bool"}):
                err, req = _build_request(**values)
                self.assertIsNone(req)
                self.assertEqual("invalid-system-proxy", err["code"])

    def test_audit_request_resolves_same_env_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text('{"name":"audit-proxy"}', encoding="utf-8")
            (root / "package-lock.json").write_text('{"lockfileVersion":3}', encoding="utf-8")
            with patch.dict(os.environ, {"DEPLOOM_MCP_USE_SYSTEM_PROXY": "true"}):
                self.assertTrue(validate_request(str(root))["useSystemProxy"])
                request = validate_request(str(root), use_system_proxy=False)
                self.assertFalse(request["useSystemProxy"])
                self.assertEqual("tool", request["systemProxySource"])
            with patch.dict(os.environ, {"DEPLOOM_MCP_USE_SYSTEM_PROXY": "false"}):
                self.assertTrue(validate_request(str(root), use_system_proxy=True)["useSystemProxy"])

    def test_native_env_false_removes_implicit_proxy_but_preserves_registry(self):
        original = {
            "HTTP_PROXY": "http://proxy.test:123", "https_proxy": "http://proxy.test:123",
            "ALL_PROXY": "socks5://proxy.test", "NO_PROXY": "127.0.0.1",
            "npm_config_registry": "https://internal-registry.example.test",
            "PATH": "some-path",
        }
        no = native_proxy_environment(original, use_system_proxy=False)
        self.assertNotIn("HTTP_PROXY", no)
        self.assertNotIn("https_proxy", no)
        self.assertNotIn("ALL_PROXY", no)
        self.assertEqual(original["npm_config_registry"], no["npm_config_registry"])
        self.assertEqual(original["NO_PROXY"], no["NO_PROXY"])
        yes = native_proxy_environment(original, use_system_proxy=True)
        self.assertEqual(original, yes)
        self.assertEqual(original["HTTP_PROXY"], original["HTTP_PROXY"])

    def test_failed_osv_evidence_cannot_become_clean(self):
        blueprint = {
            "projections": {
                "postPlanSecurityKnown": 0, "postPlanSecurityUnknown": 1,
                "postPlanSecurityTotal": 1, "postPlanCritical": 0,
                "postPlanHigh": 0, "postPlanModerate": 0,
                "postPlanLow": 0, "postPlanGoal": "unknown",
            },
            "perProject": {},
        }
        evidence = target_assessment(blueprint)
        self.assertFalse(evidence["evidenceComplete"])
        codes = {reason["code"] for reason in target_decision_evidence(evidence)}
        self.assertIn("TARGET_SECURITY_UNKNOWN", codes)
        self.assertNotIn("TARGET_POLICY_BLOCKED", codes)


class SystemProxyStdioTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, env_value: str | None, explicit: bool | None) -> bool:
        with tempfile.TemporaryDirectory(prefix="deploom-proxy-stdio-") as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "package.json").write_text('{"name":"stdio-proxy"}', encoding="utf-8")
            env = dict(os.environ)
            env.pop("DEPLOOM_MCP_USE_SYSTEM_PROXY", None)
            env.update({
                "DEPLOOM_MCP_STATE_DIR": str(root / "state"),
                "DEPLOOM_MCP_GENERATOR_PATH": str(FAKE_CORE),
                "FAKE_GENERATOR_PROXY_RECEIPT": "1",
                "FAKE_GENERATOR_MODE": "ok",
            })
            if env_value is not None:
                env["DEPLOOM_MCP_USE_SYSTEM_PROXY"] = env_value
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "deploom_mcp"],
                cwd=str(ROOT), env=env,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    request = {"project_path": str(project),
                               "output_dir": str(root / "outside"),
                               "deadline_seconds": 30}
                    if explicit is not None:
                        request["use_system_proxy"] = explicit
                    result = await session.call_tool("get_upgrade_plan", request)
                    self.assertFalse(result.is_error, result.content[0].text)
                    payload = result.structured_content
                    self.assertTrue(payload["ok"], payload.get("error"))
                    record = Path(payload["artifacts"]["manifest"]).parent / "proxy-argv.json"
                    self.assertTrue(record.is_file(), "actual child process must write its argv receipt")
                    self.assertFalse((project / "proxy-argv.json").exists())
                    self.assertFalse((project / "node_modules").exists())
                    return json.loads(record.read_text(encoding="utf-8"))["useSystemProxy"]

    async def test_stdio_global_default_and_explicit_overrides(self):
        for env, explicit, expected in (
            (None, None, False),
            ("true", None, True),
            ("true", False, False),
            ("false", True, True),
            ("false", None, False),
        ):
            with self.subTest(env=env, explicit=explicit):
                self.assertIs(await self._run(env, explicit), expected)


    async def _audit_stdio(self, env_value: str | None, explicit: bool | None) -> tuple[bool, str]:
        """Observe effective proxy policy after actual stdio -> audit worker.

        Use conflicting canonical locks to stop before any npm/Yarn network
        request; worker still records the effective option. This verifies the
        cross-process contract without depending on network or installed CLIs.
        """
        with tempfile.TemporaryDirectory(prefix="deploom-audit-proxy-stdio-") as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (project / "package.json").write_text(
                '{"name":"audit-proxy-stdio","private":true}', encoding="utf-8"
            )
            (project / "package-lock.json").write_text(
                '{"lockfileVersion":3,"packages":{}}', encoding="utf-8"
            )
            (project / "yarn.lock").write_text(
                '# yarn lockfile v1\n', encoding="utf-8"
            )
            before = {p.name: p.read_bytes() for p in project.iterdir()}
            env = dict(os.environ)
            env.pop("DEPLOOM_MCP_USE_SYSTEM_PROXY", None)
            env["DEPLOOM_MCP_STATE_DIR"] = str(root / "state")
            if env_value is not None:
                env["DEPLOOM_MCP_USE_SYSTEM_PROXY"] = env_value
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "deploom_mcp.audit_server"],
                cwd=str(ROOT), env=env,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    self.assertIn("audit_dependencies", names)
                    request = {
                        "project_path": str(project),
                        "output_dir": str(root / "outside"),
                        "deadline_seconds": 30,
                    }
                    if explicit is not None:
                        request["use_system_proxy"] = explicit
                    result = await session.call_tool("audit_dependencies", request)
                    self.assertTrue(result.is_error)
                    payload = result.structured_content
                    self.assertEqual("ambiguous-lockfiles", payload["error"]["code"], payload)
                    self.assertFalse(payload["security"]["complete"])
                    self.assertEqual("UNKNOWN", payload["security"]["verdict"])
                    self.assertEqual(before, {p.name: p.read_bytes() for p in project.iterdir()})
                    self.assertFalse((project / "node_modules").exists())
                    return (payload["network"]["useSystemProxy"],
                            payload["network"]["source"])

    async def test_audit_stdio_proxy_env_and_both_override_directions(self):
        for env_value, explicit, expected, source in (
            (None, None, False, "default"),
            ("true", None, True, "environment"),
            ("true", False, False, "tool"),
            ("false", True, True, "tool"),
        ):
            with self.subTest(env=env_value, explicit=explicit):
                self.assertEqual(
                    (expected, source),
                    await self._audit_stdio(env_value, explicit),
                )


if __name__ == "__main__":
    unittest.main()
