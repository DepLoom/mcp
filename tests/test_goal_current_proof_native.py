"""Real Core timeout and owned hung-hash process controls; not project acceptance."""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_delta_trial import _core
from deploom_mcp import goal_current_proof_process as process
from deploom_mcp.process_runner import run_process_async
from test_goal_current_proof_process import registration
from test_process_runner import GRANDCHILD_CODE, _watch_tree, _await_tree_ready, _close_fixture_task, _pid_alive


class FractionalCoreBudgetTests(unittest.TestCase):
    def test_fractional_manifest_timeout_is_not_rounded_up(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'input.txt').write_text('bounded input',encoding='utf-8')
            core=_core();original=core._hash_regular_file
            def slow(*args,**kwargs):
                time.sleep(.35)
                return original(*args,**kwargs)
            with patch.object(core,'_hash_regular_file',side_effect=slow):
                with self.assertRaisesRegex(core.SourceCaptureError,'SOURCE_MATERIALIZATION_TIMEOUT'):
                    core.build_source_tree_manifest(root,timeout_seconds=.1,exact_timeout=True)

    def test_exact_io_admission_does_not_round_fractional_remainder_up(self):
        _core()
        import cross_process_io
        @contextmanager
        def delayed(*a,**kw):yield 200
        @cross_process_io.coordinated_io('hash',error_type=ValueError)
        def observed(**kwargs):return kwargs['timeout_seconds']
        with patch.object(cross_process_io,'shared_io_slot',delayed):
            self.assertAlmostEqual(observed(timeout_seconds=.25,exact_timeout=True),.05)
            with self.assertRaisesRegex(ValueError,'SOURCE_MATERIALIZATION_TIMEOUT'):
                observed(timeout_seconds=.1,exact_timeout=True)
            self.assertEqual(observed(timeout_seconds=.25),1)

    def test_real_live_fingerprint_forwards_one_remaining_budget_to_manifest(self):
        from deploom_mcp.goal_current_proof_budget import CurrentProofBudget,CurrentProofSpec
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'package.json').write_text('{"name":"budget-fixture","version":"1.0.0"}',encoding='utf-8')
            core=_core();budget=CurrentProofBudget(CurrentProofSpec(5))
            with patch.object(core,'build_source_tree_manifest',wraps=core.build_source_tree_manifest) as manifest:
                key=core.source_snapshot_fingerprint(root,remaining_budget=budget.remaining)
            self.assertEqual(len(key),32);self.assertEqual(manifest.call_count,1)
            self.assertTrue(manifest.call_args.kwargs['exact_timeout'])
            self.assertGreater(manifest.call_args.kwargs['timeout_seconds'],0)
            self.assertLessEqual(manifest.call_args.kwargs['timeout_seconds'],5)

    def test_fractional_complete_manifest_has_same_full_digest_as_integer(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'input.txt').write_text('bounded input',encoding='utf-8')
            core=_core()
            a=core.build_source_tree_manifest(root,timeout_seconds=5)
            b=core.build_source_tree_manifest(root,timeout_seconds=5.0,exact_timeout=True)
            self.assertEqual(a.entries,b.entries);self.assertEqual(a.key,b.key)


@unittest.skipUnless(os.name=='nt','native pinned Windows process handles required')
class HungHashSupervisorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.pidfile=self.root/'child.pid';self.tree=self.root/'tree';self.tree.mkdir()
        (self.tree/'input.txt').write_text('fixture input',encoding='utf-8')
        self.marker=self.root/'hash.started'
        self.ids=('budget-run','budget-candidate','b'*64)

    def code(self):
        # Existing nonce/handle fixture, followed by real Core's blocked hash
        # future. Deliberate test-only injection never touches installed bytes.
        prefix=GRANDCHILD_CODE.rsplit('time.sleep(120)',1)[0]
        return prefix + (
            "from deploom_mcp.goal_delta_trial import _core\n"
            "core=_core()\n"
            "def blocked(*a,**kw):\n"
            "    Path(sys.argv[3]).write_text('hash started',encoding='ascii')\n"
            "    time.sleep(120)\n"
            "core._hash_regular_file=blocked\n"
            "core.build_source_tree_manifest(Path(sys.argv[2]),timeout_seconds=.05,exact_timeout=True)\n"
            "print('UNEXPECTED_LATE_PASS')\n")

    async def launch_fixture(self,*,cancel):
        witness=_watch_tree(self,self.pidfile)
        async def actual(_command,**kwargs):
            return await run_process_async([sys.executable,'-B','-c',self.code(),
                str(self.pidfile),str(self.tree),str(self.marker)],**kwargs)
        with patch.object(process,'run_process_async',side_effect=actual):
            task=asyncio.create_task(process.supervised_current_read(*self.ids,registration(3 if not cancel else 10)))
            try:
                await _await_tree_ready(self.pidfile,witness)
                deadline=time.monotonic()+2
                while not self.marker.exists() and time.monotonic()<deadline:await asyncio.sleep(.005)
                self.assertTrue(self.marker.exists(),'must reach real blocked hash before cancellation/timeout')
                if cancel:
                    stopped_at=time.monotonic();task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
                else:
                    with self.assertRaises(GoalContractError):await task
                    stopped_at=time.monotonic()
            finally:
                if not task.done():await _close_fixture_task(task)
        witness.require_bound(before=stopped_at)
        self.assertLess(witness.bound_at,stopped_at)
        self.assertTrue(all(handle.wait_dead(5000) for handle in witness.handles.values()))
        self.assertFalse(_pid_alive(int(self.pidfile.read_text()),witness),'known parent and child must be dead')

    async def test_cancel_kills_real_blocked_hash_process_and_known_descendant(self):
        await self.launch_fixture(cancel=True)

    async def test_timeout_kills_real_blocked_hash_process_and_known_descendant(self):
        await self.launch_fixture(cancel=False)


if __name__=='__main__':unittest.main()
