"""Verify worker checkpoint capture before external evaluation."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from experiments.linux_cycle_qualification.run import checkpoint_snapshot, read_worker_file


class CheckpointCaptureTest(unittest.TestCase):
    def test_snapshot_is_private_independent_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = root / "worker.py"
            worker.write_bytes(b"trusted candidate\n")
            snapshot = checkpoint_snapshot(worker, root / "trusted")
            worker.write_bytes(b"changed candidate\n")
            self.assertEqual(snapshot.read_bytes(), b"trusted candidate\n")
            self.assertEqual(os.stat(snapshot).st_mode & 0o777, 0o644)
            self.assertEqual(os.stat(snapshot.parent).st_mode & 0o777, 0o700)

    def test_symlink_outside_worker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.write_bytes(b"private\n")
            link = root / "worker.py"
            link.symlink_to(outside)
            with self.assertRaises(OSError):
                checkpoint_snapshot(link, root / "trusted")
            self.assertFalse((root / "trusted").exists())

    def test_nonregular_and_oversized_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                read_worker_file(root)
            fifo = root / "fifo"
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                read_worker_file(fifo)
            source = root / "worker.py"
            source.write_bytes(b"x" * (128 * 1024 + 1))
            with self.assertRaises(ValueError):
                read_worker_file(source)


if __name__ == "__main__":
    unittest.main()
