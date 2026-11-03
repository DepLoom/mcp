"""Fresh byte/path denial and bounded worker lifetime at the real filesystem boundary."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_core_integrity import verify_core_files


class CoreIntegrityTests(unittest.TestCase):
    def setUp(self):
        workspace = Path(__file__).resolve().parents[3]
        temp_root = workspace / '.tmp-tests' / 'core-integrity'
        temp_root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='guard-', dir=temp_root)
        self.base = Path(self.tmp.name).resolve()
        self.assertTrue(self.base.is_relative_to(workspace.resolve()))
        self.addCleanup(self.tmp.cleanup)
        self.core = self.base / 'core'
        self.core.mkdir()
        self.expected = {}
        for i in range(12):
            body = ('module ' + str(i)).encode()
            name = str(i) + '.py'
            (self.core / name).write_bytes(body)
            self.expected[name] = hashlib.sha256(body).hexdigest()

    def test_every_call_reads_all_bytes_without_cached_verdict(self):
        actual = Path.read_bytes
        reads = []
        lock = threading.Lock()
        def read(path):
            body = actual(path)
            with lock:
                reads.append(path.name)
            return body
        with patch.object(Path, 'read_bytes', read):
            verify_core_files(self.core, self.expected)
            verify_core_files(self.core, self.expected)
        self.assertEqual(len(reads), 24)
        self.assertTrue(all(reads.count(name) == 2 for name in self.expected))

    def test_same_size_mutation_with_restored_mtime_is_rejected(self):
        verify_core_files(self.core, self.expected)
        path = self.core / '5.py'
        before = path.stat()
        path.write_bytes(b'changed!')
        self.assertEqual(path.stat().st_size, before.st_size)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(GoalContractError, 'CORE_INTEGRITY_MISMATCH'):
            verify_core_files(self.core, self.expected)

    def test_missing_file_is_not_success_and_workers_are_joined(self):
        (self.core / '0.py').unlink()
        with self.assertRaises(FileNotFoundError):
            verify_core_files(self.core, self.expected)
        self.assertFalse(any(t.name.startswith('deploom-core-integrity') for t in threading.enumerate()))

    def test_multiple_failures_keep_original_mapping_error_order(self):
        (self.core / '0.py').unlink()
        (self.core / '1.py').write_bytes(b'bad')
        with self.assertRaises(FileNotFoundError):
            verify_core_files(self.core, self.expected)
        self.assertFalse(any(t.name.startswith('deploom-core-integrity') for t in threading.enumerate()))

    def test_escape_is_rejected_even_with_matching_external_hash(self):
        outside = self.base / 'outside.py'
        outside.write_bytes(b'outside')
        expected = dict(self.expected)
        expected['../outside.py'] = hashlib.sha256(b'outside').hexdigest()
        with self.assertRaisesRegex(GoalContractError, 'CORE_INTEGRITY_MISMATCH'):
            verify_core_files(self.core, expected)

    def test_leaf_symlink_is_rejected_even_when_target_bytes_match(self):
        link = self.core / 'linked.py'
        try:
            link.symlink_to(self.core / '0.py')
        except OSError as exc:
            self.skipTest('native symlink creation unavailable: ' + str(exc.winerror if os.name == 'nt' else exc.errno))
        expected = {**self.expected, 'linked.py': self.expected['0.py']}
        with self.assertRaisesRegex(GoalContractError, 'CORE_INTEGRITY_MISMATCH'):
            verify_core_files(self.core, expected)

    def test_reported_leaf_symlink_is_denied_before_reading_its_body(self):
        target = self.core / '0.py'
        actual_link = Path.is_symlink
        actual_read = Path.read_bytes
        def symlink(path):
            return True if path == target else actual_link(path)
        def read(path):
            if path == target:
                self.fail('reported symlink body was consumed')
            return actual_read(path)
        with patch.object(Path, 'is_symlink', symlink), patch.object(Path, 'read_bytes', read):
            with self.assertRaisesRegex(GoalContractError, 'CORE_INTEGRITY_MISMATCH'):
                verify_core_files(self.core, self.expected)

    @unittest.skipUnless(os.name == 'nt', 'native NTFS junction')
    def test_real_parent_junction_escape_is_rejected(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'match.py').write_bytes(b'matching')
        link = self.core / 'redirect'
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        cmd = 'New-Item -ItemType Junction -Path ' + quote(link) + ' -Target ' + quote(outside) + ' | Out-Null'
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', cmd], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(lambda: link.rmdir() if link.is_junction() else None)
        expected = {**self.expected, 'redirect/match.py': hashlib.sha256(b'matching').hexdigest()}
        with self.assertRaisesRegex(GoalContractError, 'CORE_INTEGRITY_MISMATCH'):
            verify_core_files(self.core, expected)

    def test_empty_single_and_large_mappings_keep_fresh_serial_fallback(self):
        verify_core_files(self.core, {})
        verify_core_files(self.core, {'0.py': self.expected['0.py']})
        for i in range(12, 257):
            name = str(i) + '.py'
            (self.core / name).write_bytes(b'x')
            self.expected[name] = hashlib.sha256(b'x').hexdigest()
        verify_core_files(self.core, self.expected)
        (self.core / '256.py').write_bytes(b'y')
        with self.assertRaisesRegex(GoalContractError, 'CORE_INTEGRITY_MISMATCH'):
            verify_core_files(self.core, self.expected)


if __name__ == '__main__':
    unittest.main()