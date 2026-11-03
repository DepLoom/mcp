"""Pure owner wiring control, not a real project acceptance."""
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from deploom_mcp import goal_final_checkpoint as module


class PublicationMaintenanceScopeTests(unittest.TestCase):
    def test_capture_only_defers_orphans_and_keeps_both_tree_and_source_guards(self):
        class StopAtBoundary(Exception):pass
        active=[False];events=[];snapshot=SimpleNamespace(key='a'*32)
        @contextmanager
        def scope():
            active[0]=True
            try:yield
            finally:active[0]=False
        def capture(*a,**k):
            self.assertTrue(active[0]);events.append('capture');return snapshot
        def opened(*a,**k):
            self.assertFalse(active[0]);events.append('open');return snapshot
        def guard():
            self.assertFalse(active[0]);events.append('source')
        def observe(*a,**k):
            self.assertFalse(active[0]);events.append('tree');return True
        def save(phase):
            self.assertEqual(phase,'SNAPSHOT_DURABLE');raise StopAtBoundary()
        core=SimpleNamespace(defer_orphan_snapshot_maintenance=scope,capture_durable_source_snapshot=capture,open_source_snapshot=opened)
        with tempfile.TemporaryDirectory() as td:
            tx=SimpleNamespace(core=core,directory=Path(td),project=Path(td)/'project',context=object(),record={'liveObservation':{'sourceSnapshotKey':snapshot.key},'deadline':123,'dependencyTreeMove':{}},remaining=lambda:5,guard_source=guard,save=save)
            with patch.object(module,'live_complete',return_value=True),patch.object(module,'current_state_matches',return_value=True),patch.object(module,'observe_delivered_tree',side_effect=observe):
                with self.assertRaises(StopAtBoundary):module.publish_checkpoint(tx,{})
        self.assertEqual(events,['source','tree','capture','open','source','tree']);self.assertFalse(active[0])


if __name__=='__main__':unittest.main()
