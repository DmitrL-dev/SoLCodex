"""Guard the preflight reducer against mismatched tests and missing diagnostics."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.reduce_quiet_diagnostic_preflight import CASES, reduce


class ReducerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for label, (prefix, witness) in CASES.items():
            failure = label.endswith("parent")
            xml = (f'<testsuite tests="1" failures="{int(failure)}" '
                   'errors="0" skipped="0"><testcase classname="tests.case" '
                   'name="test_one">' + ('<failure/>' if failure else '') +
                   '</testcase></testsuite>')
            for mode in ("verbose", "quiet"):
                (self.root / f"{prefix}-{mode}.xml").write_text(xml)
                content = (witness or b"passed") + (b" verbose detail" if mode == "verbose" else b"")
                (self.root / f"{prefix}-{mode}.out").write_bytes(content)

    def test_complete_matching_inventory_and_witnesses(self) -> None:
        report = reduce(self.root)
        self.assertEqual(report["pair_count"], 4)
        self.assertFalse(report["task_level_savings_established"])
        self.assertEqual(report["rows"]["click_parent"]["failures"], 1)

    def test_quiet_result_dropping_failure_is_rejected(self) -> None:
        path = self.root / "click-parent-quiet.xml"
        path.write_text(path.read_text().replace('failures="1"', 'failures="0"').replace('<failure/>', ''))
        with self.assertRaisesRegex(ValueError, "changed outcomes"):
            reduce(self.root)

    def test_quiet_result_dropping_diagnostic_is_rejected(self) -> None:
        (self.root / "parent-quiet.out").write_bytes(b"F")
        with self.assertRaisesRegex(ValueError, "omits diagnostic witness"):
            reduce(self.root)


if __name__ == "__main__":
    unittest.main()
