"""Test-readiness rejection controls; no mock claims a project or dead process."""
import asyncio,json,tempfile,time,unittest
from types import SimpleNamespace
from unittest.mock import patch
import process_witness as witness_module
from pathlib import Path
from test_process_runner import _await_tree_ready
from process_witness import TreeWitness,WitnessUnknown

class ReadinessRejectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.pidfile=Path(self.temp.name)/'child.pid'

    async def check_metadata_access(self,release):
        original=Path.read_text;state={'allow':False,'deniedObserved':False}
        def read(path,*args,**kwargs):
            if path.name.endswith('.tree.json'):
                if not state['allow']:
                    state['deniedObserved']=True
                    raise PermissionError('synthetic fixture sharing denial')
                nonce=original(self.pidfile.with_suffix('.nonce'),encoding='ascii')
                return json.dumps({'parent':1,'child':2,'nonce':nonce})
            return original(path,*args,**kwargs)
        # Mock handles test metadata readiness only; no process-death claim.
        def handle(pid):return SimpleNamespace(pid=pid,alive=lambda:True,close=lambda:None)
        with patch.object(Path,'read_text',read),patch.object(witness_module,'ProcessWitness',side_effect=handle):
            tree=TreeWitness(self.pidfile)
            try:
                until=time.monotonic()+1
                while not state['deniedObserved'] and time.monotonic()<until:await asyncio.sleep(.005)
                self.assertTrue(state['deniedObserved']);self.assertIsNone(tree.bound_at)
                with self.assertRaises(WitnessUnknown):tree.require_bound()
                if release:
                    state['allow']=True;until=time.monotonic()+1
                    while tree.bound_at is None and time.monotonic()<until:await asyncio.sleep(.005)
                    tree.require_bound();self.assertEqual(set(tree.handles),{'parent','child'})
                else:
                    await asyncio.sleep(.025)
                    self.assertIsNone(tree.bound_at)
                    with self.assertRaises(WitnessUnknown):tree.require_bound()
            finally:tree.close()

    async def test_transient_metadata_denial_waits_for_actual_readable_nonce(self):
        await self.check_metadata_access(True)

    async def test_persistent_metadata_denial_never_claims_ready(self):
        await self.check_metadata_access(False)

    async def test_missing_fixture_is_unknown_not_ready(self):
        with self.assertRaisesRegex(WitnessUnknown,'not ready'):
            await _await_tree_ready(self.pidfile,None,timeout=.02)

    async def test_wrong_nonce_rejected_before_any_pid_liveness_claim(self):
        self.pidfile.write_text('123',encoding='ascii')
        self.pidfile.with_suffix('.nonce').write_text('expected',encoding='ascii')
        self.pidfile.with_suffix('.tree.json').write_text(json.dumps({'parent':456,'child':123,'nonce':'wrong'}),encoding='ascii')
        with self.assertRaisesRegex(WitnessUnknown,'nonce readiness mismatch'):
            await _await_tree_ready(self.pidfile,None,timeout=.02)

    async def test_missing_native_child_is_not_ready(self):
        tree=object.__new__(TreeWitness);tree.error=None;tree.bound_at=time.monotonic()-1;tree.handles={}
        with self.assertRaisesRegex(WitnessUnknown,'not prebound'):
            await _await_tree_ready(self.pidfile,tree,timeout=.02)

    async def test_late_native_binding_keeps_original_rejection(self):
        tree=object.__new__(TreeWitness);tree.error=None;tree.bound_at=5.;tree.handles={'parent':object(),'child':object()}
        with self.assertRaisesRegex(WitnessUnknown,'bound after'):
            tree.require_bound(before=4.)