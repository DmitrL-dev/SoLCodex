"""Controls for false-pass and cleanup risks in the public replay."""
from __future__ import annotations

from pathlib import Path
import json
import subprocess
import unittest
from unittest.mock import patch

from experiments.action_fusion.external_verify import CASES
from experiments.action_fusion.replay_behavior import (
    invoke, parent_qualified, PARENT_PASSED, ROOT, witness_qualified,
)


class ReplayTests(unittest.TestCase):
    def test_parent_import_failure_does_not_qualify(self):
        case_results = {name: False for name, _, _ in CASES}
        self.assertFalse(parent_qualified({"accepted": False, "invalid_wire_cases": [],
                                           "case_results": case_results}))
        self.assertTrue(parent_qualified({
            "accepted": False, "invalid_wire_cases": [],
            "case_results": {name: name in PARENT_PASSED for name, _, _ in CASES}}))

    def test_all_x_control_errors_do_not_qualify(self):
        manifest = json.loads((ROOT / "controls_manifest.json").read_text())
        expected = {name: wire for name, _, wire in CASES}
        for name, entry in manifest["controls"].items():
            if name == "reference":
                continue
            with self.subTest(control=name):
                self.assertFalse(witness_qualified(
                    "X", True, expected[entry["witness_case"]],
                    entry["witness_observed_wire"]))

    @patch("experiments.action_fusion.replay_behavior.subprocess.run")
    def test_timeout_forces_container_removal(self, run):
        run.side_effect = [subprocess.TimeoutExpired("docker", 15),
                           subprocess.CompletedProcess([], 0),
                           subprocess.CompletedProcess([], 1, b"", b"No such object")]
        with self.assertRaises(subprocess.TimeoutExpired):
            invoke(Path(__file__), {"kind": "literal", "value": 1})
        self.assertEqual(run.call_args_list[1].args[0][1:3], ["rm", "-f"])

    @patch("experiments.action_fusion.replay_behavior.subprocess.run")
    def test_cleanup_failure_cannot_pass(self, run):
        run.side_effect = [subprocess.CompletedProcess([], 0, b"I:1\n", b""),
                           subprocess.CompletedProcess([], 1),
                           subprocess.CompletedProcess([], 0, b"container still exists", b"")]
        with self.assertRaisesRegex(RuntimeError, "cleanup"):
            invoke(Path(__file__), {"kind": "literal", "value": 1})


if __name__ == "__main__":
    unittest.main()
