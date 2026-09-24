"""Reject incomplete or internally inconsistent external quality reports."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from experiments.click_2447.reduce_selector_dev import (
    CASES, complete_upstream_result, read_case_results,
)


class ExternalReportTests(unittest.TestCase):
    def report(self, outcomes: list[bool], total: int | None = None) -> tuple[dict, str]:
        lines = [f"{name}: {'PASS' if passed else 'FAIL (Boom)'}"
                 for name, passed in zip(CASES, outcomes)]
        lines.append(f"TOTAL {sum(outcomes) if total is None else total}/{len(CASES)}")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "external.stdout"
            path.write_text("\n".join(lines) + "\n")
            return read_case_results(path)

    def test_accepts_recorded_seven_of_nine_regression(self):
        values = [name not in {"nested_inner_suppresses", "nested_outer_suppresses"}
                  for name in CASES]
        outcomes, checksum = self.report(values)
        self.assertEqual(sum(outcomes.values()), 7)
        self.assertFalse(outcomes["nested_inner_suppresses"])
        self.assertEqual(len(checksum), 64)

    def test_rejects_score_disagreeing_with_cases(self):
        with self.assertRaises(ValueError):
            self.report([True] * len(CASES), total=7)

    def test_rejects_missing_case(self):
        with self.assertRaises(ValueError):
            self.report([True] * (len(CASES) - 1))

    def test_incomplete_upstream_run_cannot_qualify(self):
        self.assertTrue(complete_upstream_result("1283 passed, 22 skipped, 1 xfailed in 1.88s"))
        self.assertFalse(complete_upstream_result("1 passed, 22 skipped, 1 xfailed in 0.01s"))


if __name__ == "__main__":
    unittest.main()
