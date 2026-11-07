"""Real filesystem scope and lock membership; no package-manager execution."""
import asyncio
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.workspace_inventory import observe_npm_workspace
from deploom_mcp.workspace_plan_snapshot import _workspace_registry, plan_workspace_snapshot
from deploom_mcp.workspace_tools import plan_workspace_snapshot_v1
from deploom_mcp.workspace_upgrade import _snapshot_request, get_workspace_upgrade_plan


class WorkspaceInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.manifests = {
            'package.json': {'name': 'fixture', 'version': '1.0.0', 'workspaces': ['packages/*'],
                             'devDependencies': {'typescript': '5.0.0'}},
            'packages/a/package.json': {'name': '@fixture/a', 'version': '1.0.0',
                                        'dependencies': {'kleur': '^3.0.0'}},
            'packages/b/package.json': {'name': '@fixture/b', 'version': '1.0.0',
                                        'dependencies': {'kleur': '^4.0.0'},
                                        'peerDependencies': {'kleur': '^4.0.0'}}}
        self.lock = {'lockfileVersion': 3, 'packages': {
            '': copy.deepcopy(self.manifests['package.json']),
            'packages/a': copy.deepcopy(self.manifests['packages/a/package.json']),
            'packages/b': copy.deepcopy(self.manifests['packages/b/package.json']),
            'node_modules/@fixture/a': {'resolved': 'packages/a', 'link': True},
            'node_modules/@fixture/b': {'resolved': 'packages/b', 'link': True},
            'node_modules/kleur': {'version': '3.0.0'},
            'packages/b/node_modules/kleur': {'version': '4.0.0'}}}
        self.save()

    def save(self):
        for name, value in {**self.manifests, 'package-lock.json': self.lock}.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding='utf-8')

    def fails(self, code):
        with self.assertRaisesRegex(GoalContractError, code):
            observe_npm_workspace(self.root)

    def test_project_npmrc_is_bound_without_exposing_its_contents(self):
        config = self.root / ".npmrc"
        before = observe_npm_workspace(self.root)
        raw = b"registry=https://mirror.example.invalid/npm/\r\nsave-exact=true\r\n"
        config.write_bytes(raw)
        after = observe_npm_workspace(self.root)
        self.assertNotEqual(before["inventoryDigest"], after["inventoryDigest"])
        self.assertEqual(after["identity"]["npmConfigInputs"], {".npmrc": hashlib.sha256(raw).hexdigest()})
        self.assertNotIn("mirror.example.invalid", json.dumps(after))
        self.assertEqual(_workspace_registry(None, {".npmrc": raw}), "https://mirror.example.invalid/npm")
        self.assertEqual(_workspace_registry("https://mirror.example.invalid/npm", {".npmrc": raw}),
                         "https://mirror.example.invalid/npm")
        with self.assertRaisesRegex(GoalContractError, "PROJECT_REGISTRY_MISMATCH"):
            _workspace_registry("https://registry.npmjs.org", {".npmrc": raw})
        config.write_bytes(raw.replace(b"true", b"false"))
        self.assertNotEqual(after["inventoryDigest"], observe_npm_workspace(self.root)["inventoryDigest"])

    def test_workspace_request_registry_must_match_existing_authority_before_planning(self):
        registry = "https://mirror.example.invalid/npm"
        (self.root / ".npmrc").write_text(f"registry={registry}\n", encoding="utf-8")
        request = {
            "schemaVersion": 2, "goalKind": "MONOREPO_TO_POLICY",
            "inventoryDigest": observe_npm_workspace(self.root)["inventoryDigest"],
            "targets": [], "environment": {}, "topology": None,
            "policy": {"targetLevel": "yellow"}, "registry": registry + "/",
        }
        env = {"DEPLOOM_WORKSPACE_POLICY_REGISTRY": registry}
        with patch.dict(os.environ, env, clear=True):
            with patch("deploom_mcp.workspace_policy_request.bind_workspace_policy_solver") as bind:
                bind.return_value = lambda *_: {
                    "assignment": None, "status": "UNSAT_IN_MODELED_DOMAIN",
                    "topology": "OBSERVED_LOCK_LAYOUT",
                }
                result = plan_workspace_snapshot(self.root, _snapshot_request(request))
                self.assertEqual(result["status"], "UNSAT_IN_MODELED_DOMAIN")
                bind.assert_called_once()
                bind.reset_mock()
                for other in ("https://other.example.invalid/npm", registry + "/another"):
                    with self.subTest(registry=other), self.assertRaisesRegex(GoalContractError, "REQUEST_REGISTRY_MISMATCH"):
                        plan_workspace_snapshot(self.root, _snapshot_request({**request, "registry": other}))
                bind.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(GoalContractError, "REGISTRY_NOT_AUTHORIZED"):
            plan_workspace_snapshot(self.root, _snapshot_request(request))

    def test_targeted_shape_error_identifies_missing_fields_without_echoing_user_keys(self):
        result = asyncio.run(get_workspace_upgrade_plan(str(self.root), {
            "schemaVersion": 2, "goalKind": "TARGETED_UPGRADE", "untrusted-example": "private-value",
        }))
        error = result["error"]
        self.assertEqual(error["code"], "INVALID_WORKSPACE_UPGRADE_REQUEST")
        self.assertIn("environment", error["message"])
        self.assertIn("registry or packuments", error["message"])
        self.assertNotIn("untrusted-example", error["message"])
        self.assertNotIn("private-value", error["message"])

    def test_workspace_registry_advisories_require_host_match_and_publish_planning_only_evidence(self):
        import sys
        from deploom_mcp.distribution import resolve_core_dir

        registry = "https://mirror.example.invalid/npm"
        npmrc = self.root / ".npmrc"
        npmrc.write_text(f"registry={registry}\n", encoding="utf-8")
        self.lock["packages"]["node_modules/typescript"] = {"version": "5.0.0"}
        self.save()

        def request():
            return {
                "schemaVersion": 2, "goalKind": "MONOREPO_TO_POLICY",
                "inventoryDigest": observe_npm_workspace(self.root)["inventoryDigest"],
                "targets": [], "environment": {}, "topology": None,
                "policy": {"targetLevel": "yellow", "securityEvidenceMode": "REGISTRY_ONLY"},
            }

        def packument(name):
            versions = ("5.0.0",) if name == "typescript" else ("3.0.0", "4.0.0")
            return {"name": name, "dist-tags": {"latest": versions[-1]},
                    "time": {v: "2026-05-01T00:00:00Z" for v in versions},
                    "versions": {v: {"name": name, "version": v,
                                     "dist": {"tarball": f"{registry}/{name}/-/{name}-{v}.tgz"}}
                                 for v in versions}}

        sys.path.insert(0, str(resolve_core_dir()))
        try:
            from dependency_live_roadmap_generator import LiveDataClient
            from registry_advisory_evidence import RegistryAdvisoryEvidence

            def advisory(pairs, *_args, **_kwargs):
                return {pair: RegistryAdvisoryEvidence("NO_REPORTED_ADVISORIES", "EXACT",
                                                        response_sha256="c" * 64) for pair in pairs}

            host = {"DEPLOOM_WORKSPACE_POLICY_REGISTRY": registry,
                    "DEPLOOM_WORKSPACE_POLICY_OSV_PACKAGES": "kleur,typescript"}
            with patch.dict(os.environ, host, clear=True), patch.object(
                LiveDataClient, "fetch_npm_metadata", side_effect=packument,
            ) as metadata, patch.object(
                LiveDataClient, "query_registry_advisory_evidence", side_effect=advisory,
            ) as bulk, patch.object(LiveDataClient, "query_osv_versions") as osv, patch(
                "workspace_npm_preflight.observe_workspace_peer_overrides", return_value={},
            ):
                for source in ("", registry + "/wrong-repository"):
                    with self.subTest(source=source), patch.dict(os.environ, {"DEPLOOM_MCP_REGISTRY_ADVISORY_SOURCE": source}):
                        with self.assertRaisesRegex(GoalContractError, "WORKSPACE_REGISTRY_ADVISORY_NOT_AUTHORIZED"):
                            plan_workspace_snapshot(self.root, _snapshot_request(request()))
                metadata.assert_not_called()
                bulk.assert_not_called()
                with patch.dict(os.environ, {"DEPLOOM_MCP_REGISTRY_ADVISORY_SOURCE": registry + "/",
                                             "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "1"}):
                    result = plan_workspace_snapshot(self.root, _snapshot_request(request()))
                self.assertEqual(bulk.call_count, 1)
                osv.assert_not_called()

                bulk.reset_mock()
                legacy = {**request(), "policy": {"targetLevel": "yellow"}}
                with patch.dict(os.environ, {"DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "1"}):
                    denied = plan_workspace_snapshot(self.root, _snapshot_request(legacy))
                bulk.assert_not_called()
                osv.assert_not_called()
                self.assertEqual(denied["policy"]["evidence"]["unknownSecurityVersionCount"], 3)

                npmrc.write_text(f"registry={registry}\n@fixture:registry=https://other.example.invalid/npm\n",
                                 encoding="utf-8")
                metadata.reset_mock()
                with patch.dict(os.environ, {"DEPLOOM_MCP_REGISTRY_ADVISORY_SOURCE": registry}):
                    with self.assertRaisesRegex(GoalContractError, "WORKSPACE_PREFLIGHT_PROJECT_NPMRC_UNSUPPORTED"):
                        plan_workspace_snapshot(self.root, _snapshot_request(request()))
                metadata.assert_not_called()
                bulk.assert_not_called()
                npmrc.write_text(f"registry={registry}\n", encoding="utf-8")

            worker_payload = json.loads(json.dumps({"ok": True, "plan": result}))
            with patch("deploom_mcp.workspace_upgrade.plan_workspace_snapshot_v1",
                       new_callable=AsyncMock, return_value=worker_payload):
                pending = get_workspace_upgrade_plan(str(self.root), request())
                with self.assertRaises(StopIteration) as response:
                    pending.send(None)
                wire = response.exception.value
            self.assertTrue(wire["ok"], wire)
            self.assertEqual(wire["policy"]["evidence"]["source"], "REGISTRY_BULK_ADVISORY")
            self.assertEqual(wire["policy"]["status"], "FEASIBLE_IN_MODELED_DOMAIN")
            self.assertEqual(wire["action"], {"kind": "REVIEW_HELD_BASELINE", "code": "WORKSPACE_HELD_BASELINE"})
            self.assertEqual(wire["coverage"]["selectedDeclarationCount"], 0)
            self.assertEqual(wire["packageManagerValidation"]["status"], "NOT_RUN")
            self.assertEqual(wire["policy"]["finalAudit"], "NOT_RUN")
            self.assertEqual(wire["acceptance"]["status"], "NOT_RUN")
            self.assertFalse(wire["executionReady"])
        finally:
            sys.path.pop(0)

    def test_peer_observer_failure_stays_unknown_and_does_not_run_resolver(self):
        registry = "https://mirror.example.invalid/npm"
        (self.root / ".npmrc").write_text(f"registry={registry}\n", encoding="utf-8")
        request = {
            "schemaVersion": 2, "goalKind": "TARGETED_UPGRADE", "registry": registry,
            "inventoryDigest": observe_npm_workspace(self.root)["inventoryDigest"],
            "targets": [{"selector": {"owner": "packages/a", "section": "dependencies", "name": "kleur"},
                         "approvedLines": [3], "versions": ["3.0.1"], "exactTarget": "3.0.1"}],
            "environment": {}, "topology": None, "preflight": {"reauthor": None, "candidateLimit": 1},
        }
        import sys
        from deploom_mcp.distribution import resolve_core_dir
        sys.path.insert(0, str(resolve_core_dir()))
        try:
            from workspace_npm_preflight import WorkspacePeerEvidenceError
            with patch.dict(os.environ, {"DEPLOOM_WORKSPACE_POLICY_REGISTRY": registry}), patch(
                "workspace_npm_preflight.observe_workspace_peer_overrides",
                side_effect=WorkspacePeerEvidenceError("NPM_OBSERVER_TIMEOUT"),
            ), patch("workspace_npm_preflight.run_workspace_npm_preflight") as resolver:
                result = plan_workspace_snapshot(self.root, _snapshot_request(request))
                resolver.assert_not_called()
            self.assertIsNone(result["assignment"])
            worker_payload = json.loads(json.dumps({"ok": True, "plan": result}))
            with patch("deploom_mcp.workspace_upgrade.plan_workspace_snapshot_v1",
                       new_callable=AsyncMock, return_value=worker_payload):
                wire = asyncio.run(get_workspace_upgrade_plan(str(self.root), request))
            self.assertTrue(wire["ok"], wire)
            self.assertEqual(wire["constraintModel"]["status"], "UNKNOWN")
            self.assertEqual(wire["constraintModel"]["reason"], "WORKSPACE_EFFECTIVE_PEER_NPM_OBSERVER_TIMEOUT")
            self.assertEqual(wire["packageManagerValidation"]["status"], "NOT_RUN")
            self.assertFalse(wire["executionReady"])
        finally:
            sys.path.pop(0)

    def test_health_plan_preserves_unknown_when_peer_observer_fails(self):
        registry = "https://mirror.example.invalid/npm"
        (self.root / ".npmrc").write_text(f"registry={registry}\n", encoding="utf-8")
        request = {
            "schemaVersion": 2, "goalKind": "MONOREPO_TO_POLICY", "registry": registry,
            "inventoryDigest": observe_npm_workspace(self.root)["inventoryDigest"],
            "targets": [], "environment": {}, "topology": None,
            "policy": {"targetLevel": "yellow", "securityEvidenceMode": "REGISTRY_ONLY"},
        }
        import sys
        from deploom_mcp.distribution import resolve_core_dir
        sys.path.insert(0, str(resolve_core_dir()))
        try:
            from workspace_npm_preflight import WorkspacePeerEvidenceError
            with patch.dict(os.environ, {"DEPLOOM_WORKSPACE_POLICY_REGISTRY": registry}), patch(
                "workspace_npm_preflight.observe_workspace_peer_overrides",
                side_effect=WorkspacePeerEvidenceError("NPM_OBSERVER_TIMEOUT"),
            ), patch("deploom_mcp.workspace_policy_request.bind_workspace_policy_solver") as solver:
                plan = plan_workspace_snapshot(self.root, _snapshot_request(request))
                solver.assert_not_called()
            with patch("deploom_mcp.workspace_upgrade.plan_workspace_snapshot_v1",
                       new_callable=AsyncMock, return_value={"ok": True, "plan": plan}):
                pending = get_workspace_upgrade_plan(str(self.root), request)
                with self.assertRaises(StopIteration) as response:
                    pending.send(None)
                wire = response.exception.value
            self.assertTrue(wire["ok"], wire)
            self.assertEqual(wire["constraintModel"]["reason"], "WORKSPACE_EFFECTIVE_PEER_NPM_OBSERVER_TIMEOUT")
            self.assertEqual(wire["policy"]["status"], "UNKNOWN")
            self.assertEqual(wire["policy"]["finalAudit"], "NOT_RUN")
            self.assertFalse(wire["executionReady"])
        finally:
            sys.path.pop(0)

    def test_model_route_shares_peer_and_registry_deadline(self):
        from deploom_mcp.distribution import resolve_core_dir
        import sys
        registry = "https://mirror.example.invalid/npm"
        (self.root / ".npmrc").write_text(f"registry={registry}\n", encoding="utf-8")
        request = _snapshot_request({
            "schemaVersion": 2, "goalKind": "MONOREPO_TO_POLICY", "registry": registry,
            "inventoryDigest": observe_npm_workspace(self.root)["inventoryDigest"],
            "targets": [], "environment": {}, "topology": None,
            "policy": {"targetLevel": "yellow", "securityEvidenceMode": "REGISTRY_ONLY"},
        })
        sys.path.insert(0, str(resolve_core_dir()))
        try:
            import workspace_npm_preflight
            with patch.dict(os.environ, {"DEPLOOM_WORKSPACE_POLICY_REGISTRY": registry}), patch.object(
                workspace_npm_preflight, "observe_workspace_peer_overrides", return_value={},
            ) as observer, patch("deploom_mcp.workspace_policy_request.bind_workspace_policy_solver") as bind, patch(
                "deploom_mcp.workspace_plan_snapshot.time", SimpleNamespace(monotonic=lambda: 100.0),
            ):
                bind.return_value = lambda *_: {"assignment": None, "status": "UNKNOWN",
                                                 "topology": "OBSERVED_LOCK_LAYOUT"}
                plan_workspace_snapshot(self.root, request, model_only=True)
                self.assertEqual(observer.call_args.kwargs["timeout_seconds"], 20)
                self.assertEqual(bind.call_args.kwargs["metadata_deadline"], 165.0)
                plan_workspace_snapshot(self.root, request)
                self.assertEqual(observer.call_args.kwargs["timeout_seconds"], 10)
                self.assertIsNone(bind.call_args.kwargs["metadata_deadline"])
        finally:
            sys.path.pop(0)

    def test_targeted_registry_supplies_held_metadata_without_granting_policy_authority(self):
        registry = "https://mirror.example.invalid/npm"
        (self.root / ".npmrc").write_text(f"registry={registry}\n", encoding="utf-8")
        request = {
            "schemaVersion": 2, "goalKind": "TARGETED_UPGRADE", "registry": registry,
            "inventoryDigest": observe_npm_workspace(self.root)["inventoryDigest"],
            "targets": [{"selector": {"owner": "packages/a", "section": "dependencies", "name": "kleur"},
                         "approvedLines": [3], "versions": ["3.0.1"], "exactTarget": "3.0.1"}],
            "environment": {}, "topology": None,
        }
        snapshot = _snapshot_request(request)
        with patch("deploom_mcp.workspace_policy_request.bind_workspace_policy_solver") as bind:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(GoalContractError, "REGISTRY_NOT_AUTHORIZED"):
                    plan_workspace_snapshot(self.root, snapshot)
                bind.assert_not_called()
            with patch.dict(os.environ, {"DEPLOOM_WORKSPACE_POLICY_REGISTRY": registry}, clear=True):
                with self.assertRaisesRegex(GoalContractError, "REQUEST_REGISTRY_MISMATCH"):
                    plan_workspace_snapshot(self.root, {**snapshot, "registry": registry + "/other"})
                bind.assert_not_called()
                bind.return_value = lambda *_: {"assignment": None, "status": "UNKNOWN",
                                                  "topology": "OBSERVED_LOCK_LAYOUT"}
                result = plan_workspace_snapshot(self.root, snapshot)
                self.assertIsNone(bind.call_args.args[0])
                self.assertEqual(len(bind.call_args.args[2]), 3)
                self.assertEqual(result["candidateEvidence"], "CORE_REGISTRY")
                self.assertNotIn("policy", result)
                self.assertFalse(result["executionReady"])

        result.update(authority="PLANNING_ONLY", verification="NOT_VERIFIED")
        worker_reply = SimpleNamespace(returncode=0, timed_out=False,
                                       stdout=json.dumps({"ok": True, "plan": result}))
        with patch("deploom_mcp.workspace_tools.run_process_async", new_callable=AsyncMock) as worker:
            worker.return_value = worker_reply
            wire = asyncio.run(plan_workspace_snapshot_v1(str(self.root), snapshot))
            self.assertTrue(wire["ok"])
            self.assertEqual(worker.call_args.kwargs["timeout"], 75)

    def test_workspace_request_registry_rejects_unsafe_destinations(self):
        for registry in (None, "http://mirror.example.invalid/npm", "https://mirror.example.invalid/npm?x=1"):
            with self.subTest(registry=registry), self.assertRaisesRegex(GoalContractError, "REQUEST_REGISTRY_INVALID"):
                plan_workspace_snapshot(self.root, {
                    "inventoryDigest": "a" * 64, "mutable": [], "environment": {},
                    "topology": None, "policy": {"targetLevel": "yellow"}, "registry": registry,
                })

    def test_project_npmrc_accepts_explicit_strict_flags_matching_preflight(self):
        registry = "https://mirror.example.invalid/npm"
        raw = (f"registry={registry}\nsave-exact=true\nengine-strict=true\n"
               "strict-peer-deps=true\nlegacy-peer-deps=false\nforce=false").encode()
        self.assertEqual(_workspace_registry(registry, {".npmrc": raw}), registry)

    def test_project_npmrc_rejects_settings_the_isolated_command_does_not_preserve(self):
        for raw in (b"strict-peer-deps=false", b"engine-strict=false",
                    b"legacy-peer-deps=true", b"force=true", b"//registry.example.invalid/:_authToken=x",
                    b"registry=https://mirror.example.invalid/\x24{REGISTRY}",
                    b"registry=https://one.invalid\nregistry=https://two.invalid",
                    b"save-exact=maybe"):
            with self.subTest(raw=raw), self.assertRaisesRegex(GoalContractError, "PROJECT_NPMRC_UNSUPPORTED"):
                _workspace_registry(None, {".npmrc": raw})
        with self.assertRaisesRegex(GoalContractError, "MEMBER_NPMRC_UNSUPPORTED"):
            _workspace_registry(None, {"packages/a/.npmrc": b"save-exact=true"})

    def test_owner_and_section_preserved_no_flattening_or_execution(self):
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        result = observe_npm_workspace(self.root)
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(len(result['owners']), 3)
        rows = [r for r in result['declarations'] if r['name'] == 'kleur']
        self.assertEqual([(r['owner'], r['section'], r['declaredSpec']) for r in rows], [
            ('packages/a', 'dependencies', '^3.0.0'),
            ('packages/b', 'dependencies', '^4.0.0'),
            ('packages/b', 'peerDependencies', '^4.0.0')])
        self.assertFalse(result['executionReady'])
        self.assertEqual(result['jointAssignment'], 'NOT_COMPUTED')
        self.assertEqual(result['physicalTree'], 'NOT_OBSERVED')

    def test_lock_evidence_cannot_name_a_sibling_owner_provider(self):
        from deploom_mcp.workspace_tools import _valid_success

        def lookup(packages, name, owner):
            return ("node_modules/kleur", "3.0.0") if name == "kleur" else None

        result = observe_npm_workspace(self.root, lock_resolver=lookup)
        self.assertTrue(_valid_success(result, None, ""))
        self.assertEqual(result["physicalTree"], "NOT_OBSERVED")
        row = next(row for row in result["lockResolutionEvidence"]["rows"]
                   if row["owner"] == "packages/a")
        row["lockPath"] = "packages/b/node_modules/kleur"
        self.assertFalse(_valid_success(result, None, ""))

    def test_child_cannot_be_mistaken_for_lock_owner(self):
        with self.assertRaises(GoalContractError):
            observe_npm_workspace(self.root / 'packages/a')

    def test_stale_workspace_manifest_rejected(self):
        self.manifests['packages/a/package.json']['dependencies']['kleur'] = '99.0.0'
        self.save()
        self.fails('MANIFEST_LOCK_MISMATCH')

    def test_duplicate_workspace_names_rejected(self):
        self.manifests['packages/b/package.json']['name'] = '@fixture/a'
        self.lock['packages']['packages/b']['name'] = '@fixture/a'
        self.save()
        self.fails('DUPLICATE_NAME')

    def test_missing_lock_member_rejected(self):
        del self.lock['packages']['packages/a']
        self.save()
        self.fails('LOCK_MEMBER_MISSING')

    def test_nested_workspace_group_is_not_an_owner(self):
        self.manifests['package.json']['workspaces'] = ['packages/*', 'packages/group/*']
        self.lock['packages']['']['workspaces'] = ['packages/*', 'packages/group/*']
        member = 'packages/group/nested'
        value = {'name': '@fixture/nested', 'version': '1.0.0'}
        self.manifests[member + '/package.json'] = value
        self.lock['packages'][member] = value
        self.lock['packages']['node_modules/@fixture/nested'] = {'resolved': member, 'link': True}
        self.save()
        inventory = observe_npm_workspace(self.root)
        self.assertEqual({row['owner'] for row in inventory['owners']},
                         {'.', 'packages/a', 'packages/b', member})
        self.lock['packages']['packages/group'] = {'name': '@fixture/group'}
        self.save()
        self.fails('LOCK_MEMBERSHIP_MISMATCH')

    def test_literal_workspace_without_manifest_is_rejected(self):
        (self.root / 'packages/group').mkdir()
        self.manifests['package.json']['workspaces'].append('packages/group')
        self.save()
        self.fails('WORKSPACE_MANIFEST_MISSING')

    def test_unmatched_workspace_pattern_rejected(self):
        self.manifests['package.json']['workspaces'] = ['missing/*']
        self.save()
        self.fails('PATTERN_UNMATCHED')

    def test_lock_link_escape_rejected(self):
        self.lock['packages']['node_modules/@fixture/a']['resolved'] = '../outside'
        self.save()
        self.fails('LINK_TARGET_UNSUPPORTED')

    def test_link_renaming_or_missing_rejected(self):
        entry = self.lock['packages'].pop('node_modules/@fixture/a')
        self.lock['packages']['node_modules/alias'] = entry
        self.save()
        self.fails('LINK_OWNER_MISMATCH')
        del self.lock['packages']['node_modules/alias']
        self.save()
        self.fails('LINK_MISSING')

    def test_stale_member_record_not_covered_by_current_globs_rejected(self):
        self.lock['packages']['packages/missing'] = {'name': 'ghost'}
        self.save()
        self.fails('LOCK_MEMBERSHIP_MISMATCH')

    def test_discovery_cap_cannot_return_complete(self):
        with self.assertRaisesRegex(GoalContractError, 'DISCOVERY_LIMIT'):
            observe_npm_workspace(self.root, max_directories=1)
        with self.assertRaisesRegex(GoalContractError, 'MANIFEST_LIMIT'):
            observe_npm_workspace(self.root, max_manifests=1)

    def test_reread_detects_stale_lock_even_without_declared_edge_change(self):
        from deploom_mcp.workspace_inventory import _discover
        calls = 0
        def mutate(*args):
            nonlocal calls
            calls += 1
            result = _discover(*args)
            if calls == 2:
                value = copy.deepcopy(self.lock)
                value['packages']['node_modules/kleur']['version'] = '99.0.0'
                (self.root / 'package-lock.json').write_text(json.dumps(value))
            return result
        with patch('deploom_mcp.workspace_inventory._discover', side_effect=mutate):
            self.fails('INPUT_CHANGED')

    def test_membership_change_during_observation_rejected(self):
        from deploom_mcp.workspace_inventory import _discover
        calls = 0
        def mutate(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                added = self.root / 'packages/c'
                added.mkdir()
                (added / 'package.json').write_text('{"name":"c"}')
            return _discover(*args)
        with patch('deploom_mcp.workspace_inventory._discover', side_effect=mutate):
            self.fails('INPUT_CHANGED')

    def test_unsupported_globs_never_silently_omit_members(self):
        for pattern in ['../packages/*', 'packages/**', '!packages/b', 'packages/{a,b}', 'C:/packages/*', 'packages/../*']:
            with self.subTest(pattern=pattern):
                self.manifests['package.json']['workspaces'] = [pattern]
                self.save()
                self.fails('PATTERN_UNSUPPORTED')

    def test_ambiguous_lock_owners_rejected(self):
        (self.root / 'npm-shrinkwrap.json').write_text('{}')
        self.fails('LOCK_OWNER_UNSUPPORTED')

    def test_unsupported_manager_rejected(self):
        self.manifests['package.json']['packageManager'] = 'pnpm@10.0.0'
        self.save()
        self.fails('UNSUPPORTED_WORKSPACE_OR_MANAGER')

    def test_boolean_lock_version_rejected(self):
        self.lock['lockfileVersion'] = True
        self.save()
        self.fails('LOCK_V3_REQUIRED')

    def test_duplicate_json_key_rejected(self):
        (self.root / 'packages/a/package.json').write_text('{"name":"a","name":"b"}')
        self.fails('DUPLICATE_JSON_KEY')

    def test_reparse_directory_rejected_without_following_it(self):
        real_scandir = os.scandir
        class Entry:
            name = 'unsafe'
            def stat(self, **kwargs):
                class Info:
                    st_mode = 0o040755
                    st_file_attributes = 0x400
                return Info()
        class Scan:
            def __enter__(self): return iter([Entry()])
            def __exit__(self, *args): pass
        def guarded(folder):
            selected=(os.fstat(folder).st_ino==self.root.stat().st_ino) if isinstance(folder,int) else Path(folder)==self.root
            return Scan() if selected else real_scandir(folder)
        with patch('deploom_mcp.workspace_inventory.os.scandir', side_effect=guarded) as scan:
            with patch.object(os,'supports_fd',os.supports_fd | {scan}):
                self.fails('UNSAFE_INPUT')


    def test_proposal_holds_other_owners_and_same_name_other_sections(self):
        from deploom_mcp.workspace_inventory import propose_workspace_targets
        inventory = observe_npm_workspace(self.root)
        target = {'owner': 'packages/b', 'section': 'dependencies', 'name': 'kleur',
                  'fromSpec': '^4.0.0', 'toVersion': '4.1.0'}
        proposal = propose_workspace_targets(self.root, inventory['inventoryDigest'], [target])
        self.assertEqual(proposal['heldDeclarationCount'], 3)
        self.assertEqual(proposal['targets'], [target])
        self.assertFalse(proposal['executionReady'])
        self.assertEqual(proposal['globalImmutableInstances'], 'NOT_SPECIFIED_BY_THIS_PROPOSAL')
        self.assertEqual({r['owner']:r['count'] for r in proposal['heldDeclarationsByOwner']},
                         {'.':1, 'packages/a':1, 'packages/b':1})

    def test_flat_name_or_stale_before_spec_cannot_expand_scope(self):
        from deploom_mcp.workspace_inventory import propose_workspace_targets
        inventory = observe_npm_workspace(self.root)
        targets = [{'name':'kleur','toVersion':'4.1.0'},
                   {'owner':'.','section':'dependencies','name':'kleur','fromSpec':'^4.0.0','toVersion':'4.1.0'},
                   {'owner':'packages/b','section':'dependencies','name':'kleur','fromSpec':'3.0.0','toVersion':'4.1.0'}]
        for target in targets:
            with self.subTest(target=target), self.assertRaises(GoalContractError):
                propose_workspace_targets(self.root, inventory['inventoryDigest'], [target])

    def test_proposal_reobserves_identity_and_rejects_old_dependency_inputs(self):
        from deploom_mcp.workspace_inventory import propose_workspace_targets
        inventory = observe_npm_workspace(self.root)
        self.manifests['packages/a/package.json']['description']='changed'
        self.save()
        with self.assertRaisesRegex(GoalContractError, 'STALE_WORKSPACE_INVENTORY'):
            propose_workspace_targets(self.root, inventory['inventoryDigest'], [{'owner':'packages/b','section':'dependencies',
                'name':'kleur','fromSpec':'^4.0.0','toVersion':'4.1.0'}])

    def test_duplicate_owner_target_rejected(self):
        from deploom_mcp.workspace_inventory import propose_workspace_targets
        inventory = observe_npm_workspace(self.root)
        target={'owner':'packages/b','section':'dependencies','name':'kleur','fromSpec':'^4.0.0','toVersion':'4.1.0'}
        with self.assertRaisesRegex(GoalContractError, 'WORKSPACE_TARGET_NOT_CURRENT_DECLARATION'):
            propose_workspace_targets(self.root, inventory['inventoryDigest'], [target,target])

    def test_legacy_goal_worker_keeps_workspace_rejection(self):
        from deploom_mcp.goal_worker import observe_goal_source
        with self.assertRaisesRegex(GoalContractError, 'UNSUPPORTED_WORKSPACE_OR_MANAGER'):
            observe_goal_source(self.root, 'a'*64)


if __name__ == '__main__':
    unittest.main()
