"""Real generated-tree preservation and process-crash recovery, no PM/model."""
import json,os,sys,subprocess,tempfile,time,unittest
from pathlib import Path
from deploom_mcp.goal_windows_tree_swap import prepare_tree_swap,apply_tree_swap,WindowsExactSwapError

@unittest.skipUnless(os.name=='nt','Windows NTFS final materialization')
class WindowsTreeSwapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.target=self.root/'node_modules';self.staged=self.root/'staged';self.backup=self.root/'previous'
        self.staged.mkdir();(self.staged/'payload.js').write_bytes(b'new');self.deadline=time.time()+30
    def plan(self):return prepare_tree_swap(self.target,self.staged,self.backup,deadline=self.deadline)
    def test_missing_tree_is_materialized_without_backup_or_delete(self):
        plan=self.plan();self.assertIsNone(plan['before']);apply_tree_swap(plan,deadline=self.deadline)
        self.assertEqual((self.target/'payload.js').read_bytes(),b'new');self.assertFalse(self.backup.exists())
        self.assertFalse(apply_tree_swap(plan,deadline=self.deadline)['mutated'])
    def test_existing_tree_is_preserved_and_actual_process_crash_recovers(self):
        self.target.mkdir();(self.target/'unrelated-local-state').write_bytes(b'preserve')
        plan=self.plan();journal=self.root/'plan.json'
        with journal.open('x') as f:json.dump(plan,f);f.flush();os.fsync(f.fileno())
        code="import json,os,sys;from deploom_mcp.goal_windows_tree_swap import apply_tree_swap;p=json.load(open(sys.argv[1]));apply_tree_swap(p,deadline=p['deadline'],cutpoint=lambda s:os._exit(74) if s=='OLD_TREE_PRESERVED' else None)"
        env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
        x=subprocess.run([sys.executable,'-B','-c',code,str(journal)],env=env,capture_output=True,timeout=10)
        self.assertEqual(x.returncode,74,x.stderr);self.assertFalse(self.target.exists())
        self.assertEqual((self.backup/'unrelated-local-state').read_bytes(),b'preserve')
        apply_tree_swap(json.loads(journal.read_text()),deadline=self.deadline)
        self.assertEqual((self.target/'payload.js').read_bytes(),b'new')
        self.assertEqual((self.backup/'unrelated-local-state').read_bytes(),b'preserve')
    def test_same_payload_foreign_tree_does_not_claim_owned_delivery(self):
        plan=self.plan();self.target.mkdir();(self.target/'payload.js').write_bytes(b'new')
        with self.assertRaisesRegex(WindowsExactSwapError,'FOREIGN_OBJECT_OR_BYTES'):apply_tree_swap(plan,deadline=self.deadline)
        self.assertEqual((self.target/'payload.js').read_bytes(),b'new');self.assertTrue(self.staged.exists())
    def test_modified_payload_after_plan_stops_before_current_tree_move(self):
        self.target.mkdir();(self.target/'old').write_bytes(b'keep');plan=self.plan()
        (self.staged/'payload.js').write_bytes(b'bad')
        with self.assertRaisesRegex(WindowsExactSwapError,'FOREIGN_OBJECT_OR_BYTES'):apply_tree_swap(plan,deadline=self.deadline)
        self.assertEqual((self.target/'old').read_bytes(),b'keep');self.assertFalse(self.backup.exists())
