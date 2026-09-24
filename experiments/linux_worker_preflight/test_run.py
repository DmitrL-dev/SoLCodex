"""False-pass controls for the Linux preflight report boundary."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from experiments.linux_worker_preflight.run import (
    EXPECTED_AGENT_CHECKS, probe_output, write_report,
)


def result(checks: dict[str, bool], passed: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        ["synthetic"], 0,
        json.dumps({"schema": "solcodex.linux-agent-probe.v1",
                    "checks": checks, "passed": passed}) + "\n", "")


class ReportBoundaryTests(unittest.TestCase):
    def test_full_report(self):
        checks = {key: True for key in EXPECTED_AGENT_CHECKS}
        self.assertTrue(probe_output(result(checks, True))["passed"])

    def test_missing_denial_cannot_pass(self):
        checks = {key: True for key in EXPECTED_AGENT_CHECKS if key != "gold_denied"}
        with self.assertRaises(ValueError):
            probe_output(result(checks, True))

    def test_false_check_with_claimed_success_is_rejected(self):
        checks = {key: True for key in EXPECTED_AGENT_CHECKS}
        checks["gold_denied"] = False
        with self.assertRaises(ValueError):
            probe_output(result(checks, True))

    def test_report_symlink_cannot_overwrite_host_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "host-canary"
            target.write_text("unchanged")
            link = Path(directory) / "report.json"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                write_report(link, {"passed": True})
            self.assertEqual(target.read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
