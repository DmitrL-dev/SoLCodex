#!/usr/bin/env python3
"""Focused checks for bounded artifact retrieval; no model or network calls."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import receipt_search


SCRIPT = Path(receipt_search.__file__)


@unittest.skipUnless(os.name == "posix", "prototype requires POSIX")
class ReceiptSearchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "output.bin"
        lines = []
        for index in range(128):
            observed = "False"
            expected = "False"
            if index == 64:
                expected = "True"
            lines.append("CASE %03d expected=%s observed=%s\n" % (index, expected, observed))
        self.path.write_bytes("".join(lines).encode())
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def run_search(self, literal, digest=None, path=None):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--artifact", str(path or self.path),
             "--sha256", digest or self.sha256, "--literal", literal],
            capture_output=True, timeout=10,
        )
        self.assertEqual(result.stderr, b"")
        self.assertLessEqual(len(result.stdout), receipt_search.MAX_RESULT_BYTES)
        return result.returncode, json.loads(result.stdout)

    def test_broad_query_is_bounded_and_reports_truncation(self):
        code, result = self.run_search("expected=")
        self.assertEqual(code, 0)
        self.assertEqual(result["match_count"], 128)
        self.assertEqual(len(result["matches"]), 4)
        self.assertTrue(result["truncated"])
        self.assertNotIn("CASE 064", json.dumps(result))

    def test_selective_query_recovers_middle_case(self):
        code, result = self.run_search("expected=True")
        self.assertEqual(code, 0)
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["matches"][0]["line"], 65)
        self.assertIn("CASE 064", result["matches"][0]["text"])
        self.assertFalse(result["truncated"])

    def test_line_range_recovers_middle_context(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--artifact", str(self.path),
             "--sha256", self.sha256, "--line", "63"],
            capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertLessEqual(len(result.stdout), receipt_search.MAX_RESULT_BYTES)
        self.assertEqual([item["line"] for item in payload["matches"]], [63, 64, 65, 66])
        self.assertIn("CASE 064", payload["matches"][2]["text"])

    def test_hash_mismatch_does_not_disclose_content(self):
        code, result = self.run_search("expected=True", digest="0" * 64)
        self.assertEqual(code, 2)
        self.assertEqual(result["error"], "hash_mismatch")
        self.assertNotIn("CASE", json.dumps(result))

    def test_long_line_is_omitted_and_known_secret_is_redacted(self):
        self.path.write_bytes(b"key=" + b"x" * 300 + b"\nauthorization: Bearer private-token\n")
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        _, long_line = self.run_search("key=")
        self.assertEqual(long_line["matches"][0]["text"], "[line omitted: over 240 bytes]")
        self.assertTrue(long_line["truncated"])
        self.assertEqual(long_line["omitted_long_matches"], 1)
        _, secret = self.run_search("authorization:")
        self.assertNotIn("private-token", json.dumps(secret))

    def test_symlink_is_rejected(self):
        link = self.path.parent / "link.bin"
        link.symlink_to(self.path)
        code, result = self.run_search("expected=True", path=link)
        self.assertEqual(code, 2)
        self.assertEqual(result["error"], "artifact_unavailable")


if __name__ == "__main__":
    unittest.main()
