"""Development snapshot fidelity and rejection checks (no model calls)."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from experiments.packaging_checkpoint.snapshot import capture, manifest


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # macOS /var can be a symlink; the API deliberately rejects ancestor links.
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.destination = self.root / 'destination'

    def reject(self, **limits):
        with self.assertRaises((ValueError, OSError)):
            manifest(self.source, **limits)
        with self.assertRaises((ValueError, OSError)):
            capture(self.source, self.destination, **limits)
        self.assertFalse(self.destination.exists())

    def test_roundtrip_add_delete_executable_and_internal_links(self):
        (self.source / 'deleted').write_bytes(b'old')
        before = manifest(self.source)
        (self.source / 'deleted').unlink()
        (self.source / 'empty').mkdir()
        (self.source / 'pkg').mkdir()
        added = self.source / 'pkg' / 'added'
        added.write_bytes(b'\x00hello\xff')
        added.chmod(0o751)
        (self.source / 'link').symlink_to('pkg/added')
        (self.source / 'chain').symlink_to('link')
        (self.source / 'directory-link').symlink_to('pkg')
        captured = capture(self.source, self.destination)
        self.assertNotEqual(before['tree_sha256'], captured['tree_sha256'])
        self.assertEqual(captured, manifest(self.source))
        self.assertEqual(captured, manifest(self.destination))
        self.assertFalse((self.destination / 'deleted').exists())
        self.assertEqual((self.destination / 'pkg/added').read_bytes(), b'\x00hello\xff')
        self.assertEqual((self.destination / 'pkg/added').stat().st_mode & 0o111, 0o111)
        self.assertEqual(os.readlink(self.destination / 'link'), 'pkg/added')
        self.assertTrue((self.destination / 'empty').is_dir())
        added.write_bytes(b'changed')
        self.assertEqual(captured, manifest(self.destination))

    def test_link_rejections(self):
        for target in ('/etc/passwd', '../outside', 'missing', 'link', '.'):
            with self.subTest(target=target):
                (self.source / 'link').symlink_to(target)
                self.reject()
                (self.source / 'link').unlink()

    def test_two_link_cycle(self):
        (self.source / 'a').symlink_to('b')
        (self.source / 'b').symlink_to('a')
        self.reject()

    def test_relative_parent_link_inside_tree(self):
        (self.source / 'pkg').mkdir()
        (self.source / 'data').write_bytes(b'data')
        (self.source / 'pkg/link').symlink_to('../data')
        self.assertEqual(capture(self.source, self.destination), manifest(self.destination))

    def test_file_swapped_to_fifo_before_open_rejected(self):
        target = self.source / 'file'
        target.write_bytes(b'content')
        original_open = os.open

        def swap(name, flags, *args, **kwargs):
            if name == 'file':
                target.unlink()
                os.mkfifo(target)
                self.assertTrue(flags & os.O_NONBLOCK)
                self.assertTrue(flags & os.O_NOFOLLOW)
            return original_open(name, flags, *args, **kwargs)

        with patch('experiments.packaging_checkpoint.snapshot.os.open', side_effect=swap):
            with self.assertRaises(ValueError):
                capture(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_directory_link_cycle(self):
        (self.source / 'a').mkdir()
        (self.source / 'b').mkdir()
        (self.source / 'a/to-b').symlink_to('../b')
        (self.source / 'b/to-a').symlink_to('../a')
        self.reject()

    def test_link_cannot_traverse_file(self):
        (self.source / 'file').write_bytes(b'x')
        (self.source / 'link').symlink_to('file/../file')
        self.reject()

    def test_fifo_does_not_hang(self):
        os.mkfifo(self.source / 'pipe')
        code = ('from pathlib import Path; '
                'from experiments.packaging_checkpoint.snapshot import manifest; '
                'manifest(Path(' + repr(str(self.source)) + '))')
        result = subprocess.run([sys.executable, '-B', '-c', code],
                                capture_output=True, timeout=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'special files forbidden', result.stderr)
        self.reject()

    def test_socket_rejected(self):
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(self.source / 'socket'))
            self.reject()

    def test_file_and_tree_limits(self):
        (self.source / 'a').write_bytes(b'abc')
        self.reject(max_file_bytes=2)
        (self.source / 'b').write_bytes(b'def')
        self.reject(max_tree_bytes=5)
        capture(self.source, self.destination, max_file_bytes=3, max_tree_bytes=6)

    def test_git_metadata_rejected(self):
        (self.source / '.git').write_text('gitdir: elsewhere')
        self.reject()

    def test_existing_destination_untouched(self):
        self.destination.mkdir()
        (self.destination / 'keep').write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            capture(self.source, self.destination)
        self.assertEqual((self.destination / 'keep').read_bytes(), b'keep')

    def test_partial_destination_cleaned(self):
        (self.source / 'a').write_bytes(b'abc')
        with patch('experiments.packaging_checkpoint.snapshot.os.fsync',
                   side_effect=OSError('injected write failure')):
            with self.assertRaises(OSError):
                capture(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_source_root_symlink_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to('source')
        with self.assertRaises(OSError):
            manifest(alias)

    def test_destination_inside_source_rejected(self):
        with self.assertRaises(ValueError):
            capture(self.source, self.source / 'copy')

    def test_deterministic_creation_order(self):
        (self.source / 'z').write_bytes(b'z')
        (self.source / 'a').write_bytes(b'a')
        first = manifest(self.source)
        (self.source / 'z').unlink()
        (self.source / 'z').write_bytes(b'z')
        self.assertEqual(first, manifest(self.source))


if __name__ == '__main__':
    unittest.main()
