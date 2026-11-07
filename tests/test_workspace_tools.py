import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from deploom_mcp.process_runner import ProcessRun
from deploom_mcp.workspace_tools import observe_workspace_inventory_v1, plan_workspace_snapshot_v1, register_workspace_tools


class WorkspaceToolBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_only_refuses_preflight_and_unbound_metadata_before_worker(self):
        registry = "https://fixture.example/npm"
        with patch("deploom_mcp.workspace_tools.run_process_async", new_callable=AsyncMock) as child:
            for request in ({}, {"registry": registry, "preflight": {}},
                            {"registry": registry, "packuments": {}}):
                with self.subTest(request=request):
                    result = await plan_workspace_snapshot_v1(
                        str(Path("public-fixture").absolute()), request, model_only=True,
                    )
                    self.assertEqual(result["error"]["code"], "WORKSPACE_MODEL_ONLY_REQUIRES_APPROVED_REGISTRY")
            child.assert_not_awaited()

    async def test_model_only_enforces_restricted_worker_egress(self):
        with patch("deploom_mcp.workspace_tools.run_process_async", new_callable=AsyncMock,
                   return_value=ProcessRun(1, '{"ok":false}', "")) as child:
            await plan_workspace_snapshot_v1(
                str(Path("public-fixture").absolute()), {"registry": "https://fixture.example/npm"}, model_only=True,
            )
        self.assertEqual(child.call_args.kwargs["env"]["DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE"], "1")

    async def test_invalid_budget_has_no_child(self):
        for deadline in (True, float('inf'), 0, 31):
            with patch('deploom_mcp.workspace_tools.run_process_async', new_callable=AsyncMock) as child:
                result=await observe_workspace_inventory_v1(str(Path("public-fixture").absolute()), deadline_seconds=deadline)
                self.assertEqual(result['error']['code'], 'INVALID_DEADLINE')
                child.assert_not_awaited()

    async def test_timeout_cannot_be_claimed_complete(self):
        with patch('deploom_mcp.workspace_tools.run_process_async', new_callable=AsyncMock,
                   return_value=ProcessRun(-1, '{"ok":true}', '', True)):
            result=await observe_workspace_inventory_v1(str(Path("public-fixture").absolute()))
            self.assertFalse(result['ok'])
            self.assertEqual(result['discovery']['status'], 'PARTIAL')
            self.assertFalse(result['executionReady'])

    async def test_worker_failure_never_promoted_or_private_stderr_exposed(self):
        with patch('deploom_mcp.workspace_tools.run_process_async', new_callable=AsyncMock,
                   return_value=ProcessRun(1, '{"ok":true}', 'private diagnostics')):
            result=await observe_workspace_inventory_v1(str(Path("public-fixture").absolute()))
            self.assertFalse(result['ok'])
            self.assertNotIn('private', str(result))

    async def test_inventory_cap_remains_partial(self):
        with patch('deploom_mcp.workspace_tools.run_process_async', new_callable=AsyncMock,
                   return_value=ProcessRun(1, '{"ok":false,"error":{"code":"WORKSPACE_MANIFEST_LIMIT"}}', '')):
            result=await observe_workspace_inventory_v1(str(Path("public-fixture").absolute()))
            self.assertEqual(result['discovery']['status'], 'PARTIAL')

    async def test_worker_cannot_grant_execution_authority(self):
        for payload in ({'ok':True}, {'ok':True,'inventory':{'executionReady':True, 'verification':'VERIFIED'}}):
            with patch('deploom_mcp.workspace_tools.run_process_async', new_callable=AsyncMock,
                       return_value=ProcessRun(0, json.dumps(payload), '')):
                self.assertFalse((await observe_workspace_inventory_v1(str(Path("public-fixture").absolute())))['ok'])

    async def test_proposal_requires_explicit_current_inventory_digest(self):
        with patch('deploom_mcp.workspace_tools.run_process_async', new_callable=AsyncMock) as child:
            result=await observe_workspace_inventory_v1(str(Path("public-fixture").absolute()), targets=[{'name':'kleur'}])
            self.assertFalse(result['ok'])
            child.assert_not_awaited()

    async def test_cancellation_reaches_runner(self):
        with patch('deploom_mcp.workspace_tools.run_process_async', new_callable=AsyncMock, side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await observe_workspace_inventory_v1(str(Path("public-fixture").absolute()))

    async def test_mcp_registration_read_only_and_closed_world(self):
        recorded=[]
        class Server:
            def tool(self, **options):
                recorded.append(options)
                return lambda function: function
        register_workspace_tools(Server(), lambda result: result)
        by_name = {tool['name']: tool['annotations'] for tool in recorded}
        for name in ('observe_workspace_inventory_v1', 'review_workspace_transitive_overrides_v1'):
            with self.subTest(tool=name):
                self.assertTrue(by_name[name].read_only_hint)
                self.assertFalse(by_name[name].destructive_hint)
                self.assertFalse(by_name[name].open_world_hint)


    async def test_malformed_success_with_four_status_fields_rejected(self):
        payload={'ok':True,'inventory':{'executionReady':False,'verification':'NOT_VERIFIED',
            'jointAssignment':'NOT_COMPUTED','authority':'READ_ONLY_INVENTORY'}}
        with patch('deploom_mcp.workspace_tools.run_process_async',new_callable=AsyncMock,
                   return_value=ProcessRun(0,json.dumps(payload),'')):
            result=await observe_workspace_inventory_v1(str(Path("public-fixture").absolute()))
            self.assertEqual(result['error']['code'],'WORKSPACE_WORKER_INVALID_RESULT')

    async def test_sensitive_target_rejected_before_process_arguments(self):
        target={'owner':'apps/b','section':'dependencies','name':'fake-package',
                'fromSpec':'git+https://' + 'user:token@example.invalid/pkg.git','toVersion':'1.0.0'}
        with patch('deploom_mcp.workspace_tools.run_process_async',new_callable=AsyncMock) as child:
            result=await observe_workspace_inventory_v1(str(Path("public-fixture").absolute()),'a'*64,[target])
            self.assertFalse(result['ok']);child.assert_not_awaited()
            self.assertNotIn('token',json.dumps(result))


if __name__=='__main__':unittest.main()
