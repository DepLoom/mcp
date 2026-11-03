"""Adversarial local import and file-handle boundaries, using owned fixtures only."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.workspace_inventory import _read
from deploom_mcp.workspace_tools import observe_workspace_inventory_v1


class FileHandleIdentityTests(unittest.TestCase):
    def test_opened_handle_must_match_checked_file_before_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();requested=root/'requested.json';foreign=root/'foreign.json'
            requested.write_text('{"name":"expected"}');foreign.write_text('{"name":"foreign"}')
            original=Path.open
            with patch('deploom_mcp.workspace_inventory.open_bound_file', lambda path,parent: original(foreign,'rb')):
                with self.assertRaisesRegex(GoalContractError,'WORKSPACE_INPUT_CHANGED'):
                    _read(requested,1024)


class WorkerImportIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_and_pythonpath_code_is_never_imported(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp).resolve();root=base/'fixture';evil=base/'untrusted-cwd';root.mkdir();evil.mkdir()
            marker=evil/'import-executed.txt'
            (evil/'sitecustomize.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("startup imported")\n')
            fake=evil/'deploom_mcp';fake.mkdir()
            (fake/'__init__.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("package imported")\nraise RuntimeError("untrusted package must not execute")\n')
            manifests={'package.json':{'name':'fixture','version':'1.0.0','workspaces':['packages/*']},
                'packages/a/package.json':{'name':'@fixture/a','version':'1.0.0'}}
            for name,value in manifests.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
            lock={'lockfileVersion':3,'packages':{'':manifests['package.json'],'packages/a':manifests['packages/a/package.json'],
                'node_modules/@fixture/a':{'link':True,'resolved':'packages/a'}}}
            (root/'package-lock.json').write_text(json.dumps(lock))
            with patch.dict(os.environ,{'PYTHONPATH':str(evil)}),patch('deploom_mcp.workspace_tools.Path.cwd',return_value=evil):
                result=await observe_workspace_inventory_v1(str(root))
            self.assertFalse(marker.exists(),'read-only worker imported code from cwd/PYTHONPATH')
            self.assertTrue(result['ok'],result)


class DirectoryNamespaceTests(unittest.TestCase):
    def test_bound_directory_cannot_be_replaced_with_foreign_namespace(self):
        from deploom_mcp.workspace_input_io import bound_directory,open_bound_file
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();child=root/'member';child.mkdir();(child/'package.json').write_text('{"name":"expected"}')
            moved=root/'old-member'
            with bound_directory(root,child) as parent:
                if os.name=='nt':
                    with self.assertRaises(OSError):child.rename(moved)
                else:
                    child.rename(moved)
                    child.mkdir();(child/'package.json').write_text('{"name":"foreign"}')
                with open_bound_file(child/'package.json',parent) as stream:
                    self.assertEqual(json.load(stream)['name'],'expected')


class SensitiveSpecTests(unittest.TestCase):
    def test_sensitive_declaration_never_enters_inventory_rows(self):
        from deploom_mcp.workspace_inventory import _declarations
        with self.assertRaisesRegex(GoalContractError,'SENSITIVE_OR_UNSUPPORTED_SPEC'):
            _declarations({'dependencies':{'fake-package':'git+https://' + 'user:token@example.invalid/pkg.git'}},'.')


class SelectedRootIdentityTests(unittest.TestCase):
    def test_root_replaced_after_lstat_rejected_before_any_json_read(self):
        from deploom_mcp.workspace_inventory import observe_npm_workspace,_safe
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp).resolve();root=base/'selected';foreign=base/'foreign';root.mkdir();foreign.mkdir()
            (root/'package.json').write_text('{"name":"original"}')
            (foreign/'package.json').write_text('{"name":"foreign"}')
            changed=False
            def swap(path,**kwargs):
                nonlocal changed
                result=_safe(path,**kwargs)
                if path==root and not changed:
                    changed=True;root.rename(base/'original-object');foreign.rename(root)
                return result
            with patch('deploom_mcp.workspace_inventory._safe',side_effect=swap),patch('deploom_mcp.workspace_inventory._read') as reader:
                with self.assertRaisesRegex(GoalContractError,'WORKSPACE_INPUT_CHANGED'):
                    observe_npm_workspace(root)
                reader.assert_not_called()


if __name__=='__main__':unittest.main()