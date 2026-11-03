"""Actual Windows NTFS file identity, concurrency and process-crash probes."""
import hashlib,json,os
from pathlib import Path
import shutil,subprocess,sys,tempfile,time,unittest
from deploom_mcp.goal_windows_file_swap import (_API,protected_parents,
    prepare_swap,apply_swap,observe_swap,WindowsExactSwapError)


@unittest.skipUnless(os.name=='nt','Windows NTFS delivery primitive')
class WindowsExactSwapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.directory=self.root/'project';self.directory.mkdir()
        self.target=self.directory/'source.txt';self.staged=self.root/'stage.txt';self.backup=self.root/'backup.txt'
        for path,data in [(self.target,b'old'),(self.staged,b'new')]:
            with path.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        self.deadline=time.time()+30
    def plan(self):
        return prepare_swap(self.target,self.staged,self.backup,
            before_sha256=hashlib.sha256(b'old').hexdigest(),after_sha256=hashlib.sha256(b'new').hexdigest(),deadline=self.deadline)
    def test_held_identity_delivery_preserves_old_and_node_can_read_after_release(self):
        plan=self.plan();self.assertNotEqual(plan['before']['fileId'],plan['after']['fileId'])
        out=apply_swap(plan,deadline=self.deadline);self.assertTrue(out['mutated'])
        self.assertEqual(self.target.read_bytes(),b'new');self.assertEqual(self.backup.read_bytes(),b'old')
        self.assertFalse(apply_swap(plan,deadline=self.deadline)['mutated'])
        node=shutil.which('node');self.assertIsNotNone(node)
        x=subprocess.run([node,'-e',"require('node:assert/strict').equal(require('node:fs').readFileSync('source.txt','utf8'),'new')"],cwd=self.directory,capture_output=True,timeout=5)
        self.assertEqual(x.returncode,0,x.stderr)
    def test_active_writer_prevents_lease_before_any_mutation(self):
        api=_API(self.deadline);h=api.k.CreateFileW(str(self.target),0x40000000,7,None,3,0,None)
        try:
            with self.assertRaisesRegex(WindowsExactSwapError,'WIN32_32'):self.plan()
        finally:api.close(h)
        self.assertEqual(self.target.read_bytes(),b'old');self.assertFalse(self.backup.exists())
    def test_foreign_destination_during_vacancy_is_never_overwritten(self):
        plan=self.plan()
        def foreign(stage):
            if stage=='OLD_MOVED_TO_OWNED_BACKUP':self.target.write_bytes(b'foreign')
        with self.assertRaises(WindowsExactSwapError):apply_swap(plan,deadline=self.deadline,cutpoint=foreign)
        self.assertEqual(self.target.read_bytes(),b'foreign');self.assertEqual(self.backup.read_bytes(),b'old');self.assertEqual(self.staged.read_bytes(),b'new')
    def test_same_hash_foreign_object_is_not_accepted_as_delivered(self):
        plan=self.plan()
        def crash(stage):
            if stage=='OLD_MOVED_TO_OWNED_BACKUP':raise RuntimeError('simulated interruption')
        with self.assertRaises(RuntimeError):apply_swap(plan,deadline=self.deadline,cutpoint=crash)
        self.target.write_bytes(b'new')
        with self.assertRaisesRegex(WindowsExactSwapError,'FOREIGN_OBJECT_CONFLICT'):apply_swap(plan,deadline=self.deadline)
        self.assertEqual(self.target.read_bytes(),b'new');self.assertEqual(self.staged.read_bytes(),b'new')
    def test_actual_process_exit_recovers_pinned_objects_and_original_deadline(self):
        plan=self.plan();path=self.root/'journal.json'
        with path.open('x',encoding='utf-8') as stream:json.dump(plan,stream);stream.flush();os.fsync(stream.fileno())
        code="import os,json,sys;from deploom_mcp.goal_windows_file_swap import apply_swap;p=json.load(open(sys.argv[1]));apply_swap(p,deadline=p['deadline'],cutpoint=lambda s:os._exit(73) if s=='OLD_MOVED_TO_OWNED_BACKUP' else None)"
        env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
        x=subprocess.run([sys.executable,'-B','-c',code,str(path)],env=env,capture_output=True,timeout=10)
        self.assertEqual(x.returncode,73,x.stderr)
        self.assertFalse(self.target.exists());self.assertEqual(self.backup.read_bytes(),b'old')
        saved=json.loads(path.read_text());self.assertEqual(saved['deadline'],self.deadline)
        apply_swap(saved,deadline=self.deadline+1000)
        self.assertEqual(self.target.read_bytes(),b'new');self.assertEqual(self.backup.read_bytes(),b'old')
    def test_parent_directory_cannot_be_renamed_while_protected(self):
        api=_API(self.deadline)
        with protected_parents(api,[self.target,self.staged,self.backup]):
            code="from pathlib import Path;import sys;Path(sys.argv[1]).rename(sys.argv[2])"
            x=subprocess.run([sys.executable,'-B','-c',code,str(self.directory),str(self.root/'foreign-parent')],capture_output=True,timeout=5)
            self.assertNotEqual(x.returncode,0)
        self.assertTrue(self.directory.exists());self.assertEqual(self.target.read_bytes(),b'old')
    def test_preexisting_junction_is_rejected_without_mutation(self):
        alias=self.root/'alias';quote=lambda p:"'"+str(p).replace("'","''")+"'"
        x=subprocess.run(['powershell.exe','-NoProfile','-Command','New-Item -ItemType Junction -Path '+quote(alias)+' -Target '+quote(self.directory)+' | Out-Null'],capture_output=True,timeout=10)
        self.assertEqual(x.returncode,0,x.stderr)
        self.addCleanup(lambda:alias.rmdir() if alias.is_junction() else None)
        with self.assertRaisesRegex(WindowsExactSwapError,'LINK_OR_TYPE_UNSUPPORTED'):
            prepare_swap(alias/'source.txt',self.staged,self.backup,before_sha256=hashlib.sha256(b'old').hexdigest(),after_sha256=hashlib.sha256(b'new').hexdigest(),deadline=self.deadline)
        self.assertEqual(self.target.read_bytes(),b'old')
    def test_backup_collision_and_postimage_substitution_fail_closed(self):
        plan=self.plan();self.backup.write_bytes(b'foreign')
        with self.assertRaises(WindowsExactSwapError):apply_swap(plan,deadline=self.deadline)
        self.assertEqual(self.target.read_bytes(),b'old');self.assertEqual(self.backup.read_bytes(),b'foreign')
    def test_original_deadline_is_not_extended_by_resume_argument(self):
        plan=self.plan();plan['deadline']=time.time()-1
        with self.assertRaisesRegex(WindowsExactSwapError,'DEADLINE_EXCEEDED'):apply_swap(plan,deadline=time.time()+1000)
        self.assertEqual(self.target.read_bytes(),b'old')

    def test_read_only_recovery_observation_distinguishes_owned_missing_and_delivered(self):
        plan=self.plan();self.assertEqual(observe_swap(plan,deadline=self.deadline),'PREIMAGE')
        def cut(stage):
            if stage=='OLD_MOVED_TO_OWNED_BACKUP':raise RuntimeError('cut')
        with self.assertRaises(RuntimeError):apply_swap(plan,deadline=self.deadline,cutpoint=cut)
        self.assertEqual(observe_swap(plan,deadline=self.deadline),'OLD_MOVED_TO_OWNED_BACKUP')
        self.assertFalse(self.target.exists())
        apply_swap(plan,deadline=self.deadline)
        self.assertEqual(observe_swap(plan,deadline=self.deadline),'DELIVERED')
    def test_parent_substitution_after_plan_is_not_hidden_by_equal_bytes(self):
        plan=self.plan();moved=self.root/'old-project';self.directory.rename(moved)
        self.directory.mkdir();self.target.write_bytes(b'old')
        with self.assertRaisesRegex(WindowsExactSwapError,'PARENT_IDENTITY_CHANGED'):
            apply_swap(plan,deadline=self.deadline)
        self.assertEqual(self.target.read_bytes(),b'old')
        self.assertEqual((moved/'source.txt').read_bytes(),b'old')

    def test_staged_same_hash_foreign_object_stops_before_source_move(self):
        plan=self.plan();preserved=self.root/'original-stage.txt';self.staged.rename(preserved)
        self.staged.write_bytes(b'new')
        with self.assertRaisesRegex(WindowsExactSwapError,'FOREIGN_OBJECT_CONFLICT'):
            apply_swap(plan,deadline=self.deadline)
        self.assertEqual(self.target.read_bytes(),b'old');self.assertFalse(self.backup.exists())
        self.assertEqual(preserved.read_bytes(),b'new');self.assertEqual(self.staged.read_bytes(),b'new')
