"""Positive and negative controls for the test-only Windows object witness."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch
import process_witness as witness

@unittest.skipUnless(os.name == "nt", "Windows native process-object witness")
class ProcessWitnessControls(unittest.TestCase):
    def spawn(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        self.addCleanup(self.cleanup_process, process)
        handle = witness.ProcessWitness(process.pid)
        self.addCleanup(handle.close)
        return process, handle

    @staticmethod
    def cleanup_process(process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)

    def test_live_object_times_out_and_terminated_object_signals(self):
        process, handle = self.spawn()
        self.assertTrue(handle.alive())
        process.kill()
        process.wait(timeout=5)
        self.assertFalse(handle.alive())

    def test_surviving_child_is_not_mistaken_for_dead_parent(self):
        scripts = str(Path(__file__).resolve().parents[1] / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from d1_owned_process import OwnedProcess
        from test_process_runner import GRANDCHILD_CODE
        with tempfile.TemporaryDirectory(prefix="witness-negative-") as tmp:
            pidfile = Path(tmp) / "child.pid"
            tree = witness.TreeWitness(pidfile)
            try:
                release = Path(tmp) / "release-parent"
                body, separator, tail = GRANDCHILD_CODE.rpartition("\ntime.sleep(120)\n")
                self.assertTrue(separator)
                self.assertEqual(tail, "")
                code = body + "\nwhile not Path(sys.argv[2]).exists(): time.sleep(.01)\n"
                with OwnedProcess([sys.executable, "-c", code, str(pidfile), str(release)],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL) as owned:
                    deadline = time.monotonic() + 5
                    while tree.bound_at is None and tree.error is None and time.monotonic() < deadline:
                        time.sleep(.005)
                    tree.require_bound()
                    self.assertTrue(tree.handles["parent"].alive())
                    self.assertTrue(tree.handles["child"].alive())
                    # A venv launcher may be a different process from fixture
                    # os.getpid(). Release the fixture itself, not its wrapper.
                    release.write_text("exit", encoding="ascii")
                    owned.proc.wait(timeout=5)
                    self.assertFalse(tree.handles["parent"].alive())
                    self.assertTrue(tree.handles["child"].alive())
                    self.assertTrue(tree.any_alive(), "survivor rejected before job cleanup")
                # TerminateJobObject requests termination asynchronously; wait
                # on the pinned objects, without ever interpreting failure as dead.
                self.assertTrue(tree.handles["parent"].wait_dead(5000))
                self.assertTrue(tree.handles["child"].wait_dead(5000))
                self.assertFalse(tree.any_alive())
            finally:
                tree.close()

    def test_failed_open_is_unknown(self):
        with patch.object(witness._kernel, "OpenProcess", return_value=0):
            with self.assertRaises(witness.WitnessUnknown):
                witness.ProcessWitness(os.getpid())

    def test_failed_wait_is_unknown(self):
        _, handle = self.spawn()
        with patch.object(witness._kernel, "WaitForSingleObject", return_value=0xFFFFFFFF):
            with self.assertRaises(witness.WitnessUnknown):
                handle.alive()

    def test_closed_handle_is_unknown(self):
        _, handle = self.spawn()
        handle.close()
        with self.assertRaises(witness.WitnessUnknown):
            handle.alive()

    def test_unbound_tree_is_unknown(self):
        tree = object.__new__(witness.TreeWitness)
        tree.error = None
        tree.bound_at = None
        tree.handles = {}
        with self.assertRaises(witness.WitnessUnknown):
            tree.require_bound()
