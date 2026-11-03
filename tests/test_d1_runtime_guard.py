import json,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from runtime_guard import verify_runtime
class RuntimeGuardTests(unittest.TestCase):
    def fixture(self,root):
        python=root/'venv/Scripts/python.exe';python.parent.mkdir(parents=True);python.touch()
        wheel=root/'test.whl';site=root/'venv/Lib/site-packages'
        with zipfile.ZipFile(wheel,'w') as z:
            for i in range(6):
                name=f'deploom_mcp/file{i}.py';data=f'# file {i}\n';z.writestr(name,data)
                p=site/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data.encode())
        return python,wheel,site
    def test_changed_runtime_fails_before_any_subprocess(self):
        with tempfile.TemporaryDirectory() as d:
            python,wheel,site=self.fixture(Path(d));(site/'deploom_mcp/file0.py').write_text('changed')
            with patch('runtime_guard.subprocess.run') as run:
                with self.assertRaisesRegex(RuntimeError,'MISMATCH'):verify_runtime(python,wheel,site)
            run.assert_not_called()
    def test_valid_runtime_checks_dependencies_without_installing(self):
        with tempfile.TemporaryDirectory() as d:
            python,wheel,site=self.fixture(Path(d))
            with patch('runtime_guard.subprocess.run',return_value=SimpleNamespace(returncode=0)) as run, patch('runtime_guard.subprocess.check_output',return_value='[]') as inventory:
                r=verify_runtime(python,wheel,site)
            self.assertEqual(r['verifiedPackageFiles'],6);self.assertFalse(r['installationPerformed'])
            self.assertEqual(run.call_args.args[0][-2:],['pip','check'])
    def test_invalid_archive_and_missing_payload_never_run_interpreter(self):
        import stat,warnings
        for mode in ['missing','traversal','absolute','backslash','duplicate','archive-link']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                python,wheel,site=self.fixture(Path(d))
                if mode=='missing':(site/'deploom_mcp/file0.py').unlink()
                else:
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore',UserWarning)
                        with zipfile.ZipFile(wheel,'a') as z:
                            names={'traversal':'../escape.py','absolute':'/escape.py',
                                'backslash':'deploom_mcp\\escape.py','duplicate':'deploom_mcp/file0.py'}
                            if mode=='archive-link':
                                row=zipfile.ZipInfo('deploom_mcp/link.py');row.create_system=3
                                row.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(row,'target')
                            else:z.writestr(names[mode],'unsafe')
                with patch('runtime_guard.subprocess.run') as run,patch('runtime_guard.subprocess.check_output') as inventory:
                    with self.assertRaises(RuntimeError):verify_runtime(python,wheel,site)
                    run.assert_not_called();inventory.assert_not_called()
    def test_installed_symlink_escape_is_not_read_or_executed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);python,wheel,site=self.fixture(root);outside=root/'outside.py';outside.write_text('foreign')
            path=site/'deploom_mcp/file0.py';path.unlink()
            try:path.symlink_to(outside)
            except OSError as exc:
                if getattr(exc,'winerror',None)!=1314:raise
                # Ordinary Windows junction setup needs no symlink privilege.
                # Both moved package and target stay in this owned temp root.
                import subprocess
                package=site/'deploom_mcp';target=root/'outside-package'
                assert package.resolve().is_relative_to(root.resolve()) and target.parent==root
                package.rename(target)
                subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(package),str(target)],
                    check=True,capture_output=True)
            with patch('runtime_guard.subprocess.run') as run:
                with self.assertRaisesRegex(RuntimeError,'LINK_OR_TYPE'):verify_runtime(python,wheel,site)
                run.assert_not_called()
    def test_failed_dependency_check_is_not_valid_runtime(self):
        with tempfile.TemporaryDirectory() as d:
            python,wheel,site=self.fixture(Path(d))
            with patch('runtime_guard.subprocess.run',return_value=SimpleNamespace(returncode=1)) as run,patch('runtime_guard.subprocess.check_output') as inventory:
                with self.assertRaisesRegex(RuntimeError,'DEPENDENCIES_INVALID'):verify_runtime(python,wheel,site)
                self.assertEqual(run.call_count,1);inventory.assert_not_called()
    def test_dependency_inventory_must_be_observed_list(self):
        with tempfile.TemporaryDirectory() as d:
            python,wheel,site=self.fixture(Path(d))
            with patch('runtime_guard.subprocess.run',return_value=SimpleNamespace(returncode=0)),patch('runtime_guard.subprocess.check_output',return_value='{}'):
                with self.assertRaisesRegex(RuntimeError,'INVENTORY_INVALID'):verify_runtime(python,wheel,site)

if __name__=='__main__':unittest.main()
