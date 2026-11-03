"""Actual NTFS aliases are names, not new filesystem authority."""
import ctypes
from ctypes import wintypes as w
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_report import _plain_under
from deploom_mcp.goal_windows_file_swap import _API, WindowsExactSwapError, prepare_swap, apply_swap

@unittest.skipUnless(os.name == "nt", "real NTFS alias boundary")
class NativeAliasBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="deploom-alias-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.state = self.root / "authorized long directory"
        self.state.mkdir()
        self.file = self.state / "source file.txt"
        self.file.write_bytes(b"old")
        self.short_root = self.short(self.state)
        self.short_file = self.short(self.file)
        self.assertNotEqual(self.short_root, self.state, "real differing 8.3 alias required")
        self.assertTrue(self.file.samefile(self.short_file))
        self.deadline = time.time() + 30

    @staticmethod
    def short(path):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = kernel.GetShortPathNameW
        fn.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
        fn.restype = w.DWORD
        buf = ctypes.create_unicode_buffer(32768)
        n = fn(str(path), buf, len(buf))
        if not 0 < n < len(buf):
            raise RuntimeError("native short alias unavailable: " + str(ctypes.get_last_error()))
        return Path(buf.value)

    def test_containment_mixed_spellings_and_missing_tail(self):
        for path in (self.file, self.short_file):
            for root in (self.state, self.short_root):
                with self.subTest(path=path == self.file, root=root == self.state):
                    self.assertEqual(_plain_under(path, root), path)
        missing = self.state / "not-created" / "artifact.json"
        self.assertEqual(_plain_under(missing, self.short_root), missing)
        outside = self.root / "outside.json"
        outside.write_bytes(b"not authorized")
        with self.assertRaises(GoalContractError):
            _plain_under(outside, self.short_root)

    def test_raw_junction_root_and_path_cannot_be_hidden_by_resolution(self):
        alias = self.root / "redirect"
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        command = "New-Item -ItemType Junction -Path " + quote(alias) + " -Target " + quote(self.state) + " | Out-Null"
        created = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                 capture_output=True, timeout=10)
        self.assertEqual(created.returncode, 0, created.stderr)
        self.addCleanup(lambda: alias.rmdir() if alias.is_junction() else None)
        with self.assertRaises(GoalContractError):
            _plain_under(self.file, alias)
        with self.assertRaises(GoalContractError):
            _plain_under(alias / self.file.name, self.state)
        api = _API(self.deadline)
        with self.assertRaises(WindowsExactSwapError):
            api.open(alias / self.file.name, readonly_file=True)

    def test_parent_components_do_not_erase_raw_junctions(self):
        outside = self.root / "outside" / "sub"
        outside.mkdir(parents=True)
        alias = self.state / "raw redirect"
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        command = "New-Item -ItemType Junction -Path " + quote(alias) + " -Target " + quote(outside) + " | Out-Null"
        created = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                 capture_output=True, timeout=10)
        self.assertEqual(created.returncode, 0, created.stderr)
        self.addCleanup(lambda: alias.rmdir() if alias.is_junction() else None)
        self.assertTrue(alias.is_junction())
        with self.assertRaises(GoalContractError):
            _plain_under(alias / ".." / self.file.name, self.state)
        with self.assertRaises(GoalContractError):
            _plain_under(self.file, alias / "..")
        ordinary = self.state / "ordinary"
        ordinary.mkdir()
        harmless = ordinary / ".." / self.file.name
        self.assertEqual(_plain_under(harmless, self.state), harmless)
        self.assertEqual(self.file.read_bytes(), b"old")

    def test_native_alias_keeps_same_identity_and_read_bytes(self):
        api = _API(self.deadline)
        first = api.open(self.file, readonly_file=True)
        try:
            second = api.open(self.short_file, readonly_file=True)
            try:
                self.assertEqual(api.identity(first), api.identity(second))
                self.assertEqual(api.file(first), api.file(second))
            finally:
                api.close(second)
        finally:
            api.close(first)

    def test_alias_does_not_bypass_hardlink_rejection(self):
        os.link(self.file, self.root / "another-link.txt")
        api = _API(self.deadline)
        with self.assertRaisesRegex(WindowsExactSwapError, "LINK_OR_TYPE_UNSUPPORTED"):
            api.open(self.short_file, readonly_file=True)

    def test_alias_does_not_bypass_conflicting_writer(self):
        api = _API(self.deadline)
        writer = api.k.CreateFileW(str(self.file), 0x40000000, 7, None, 3, 0, None)
        self.assertNotEqual(writer, ctypes.c_void_p(-1).value)
        try:
            with self.assertRaisesRegex(WindowsExactSwapError, "WIN32_32"):
                api.open(self.short_file, readonly_file=True)
        finally:
            api.close(writer)
        self.assertEqual(self.file.read_bytes(), b"old")

    def test_foreign_alias_observation_identity_fails_closed(self):
        api = _API(self.deadline)
        with patch.object(api, "identity", side_effect=[{"volumeSerial":"1","fileId":"a"},
                                                       {"volumeSerial":"1","fileId":"b"}]):
            with self.assertRaisesRegex(WindowsExactSwapError, "PATH_SUBSTITUTION"):
                api.open(self.short_file, readonly_file=True)
        self.assertEqual(self.file.read_bytes(), b"old")

    def test_alias_cannot_make_same_object_two_swap_roles(self):
        from deploom_mcp.goal_windows_tree_swap import prepare_tree_swap
        # Different strings must not let one object occupy old and staged
        # roles. Original deny-delete leases reject the second writable open.
        backup = self.state / "never-created-backup.txt"
        with self.assertRaises(WindowsExactSwapError):
            prepare_swap(self.file, self.short_file, backup,
                         before_sha256=hashlib.sha256(b"old").hexdigest(),
                         after_sha256=hashlib.sha256(b"old").hexdigest(), deadline=self.deadline)
        self.assertEqual(self.file.read_bytes(), b"old")
        self.assertFalse(backup.exists())
        tree_backup = self.root / "never-created-tree-backup"
        with self.assertRaises(WindowsExactSwapError):
            prepare_tree_swap(self.state, self.short_root, tree_backup, deadline=self.deadline)
        self.assertEqual(self.file.read_bytes(), b"old")
        self.assertFalse(tree_backup.exists())

    def test_alias_swap_and_repeat_use_original_pinned_objects(self):
        staged = self.state / "new source.txt"
        staged.write_bytes(b"new")
        backup = self.short_root / "backup.txt"
        plan = prepare_swap(self.short_file, self.short(staged), backup,
                            before_sha256=hashlib.sha256(b"old").hexdigest(),
                            after_sha256=hashlib.sha256(b"new").hexdigest(), deadline=self.deadline)
        self.assertTrue(apply_swap(plan, deadline=self.deadline)["mutated"])
        self.assertEqual(self.file.read_bytes(), b"new")
        self.assertEqual(backup.read_bytes(), b"old")
        self.assertFalse(apply_swap(plan, deadline=self.deadline)["mutated"])
