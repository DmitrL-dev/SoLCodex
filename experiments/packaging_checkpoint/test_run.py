"""Exercise the stopped worker's source shape and fixed evaluator wire parser."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from experiments.packaging_checkpoint import run as runner, scripted_worker
from experiments.packaging_checkpoint.run import (
    EXPECTED, accepted, check_controls, judge_observations, protocol, read_regular,
)
from experiments.packaging_checkpoint.snapshot import capture


class Stopped(Exception):
    pass


class WorkerShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / "work"
        self.work.mkdir()
        licenses = self.work / "src/packaging/licenses"
        licenses.mkdir(parents=True)
        (licenses / "__init__.py").write_text(
            scripted_worker.ANCHOR + scripted_worker.OLD)
        tests = self.work / "tests"
        tests.mkdir()
        (tests / "test_metadata.py").write_text("def test_original(): pass\n")
        (self.work / "README.rst").write_text("readme\n")
        control = self.work / "checkpoint-control"
        control.mkdir()
        (control / "delete-me.txt").write_text("deleted\n")

    def exercise(self, scenario: str) -> None:
        with (patch.dict(os.environ, {"SCENARIO": scenario}),
              patch.object(scripted_worker, "WORK", self.work),
              patch.object(scripted_worker, "SOURCE",
                           self.work / "src/packaging/licenses/__init__.py"),
              patch.object(scripted_worker, "HELPER",
                           self.work / "src/packaging/licenses/_parenthesis.py"),
              patch.object(scripted_worker, "isolated", return_value=True),
              patch.object(scripted_worker.time, "sleep", side_effect=Stopped),
              redirect_stdout(io.StringIO())):
            with self.assertRaises(Stopped):
                scripted_worker.main()
        self.assertEqual((self.work / ".checkpoint-ready").read_text(), scenario + "\n")
        self.assertFalse((self.work / ".checkpoint-ready.tmp").exists())

    def test_alternative_preserves_material_addition_and_tree_controls(self) -> None:
        self.exercise("alternate")
        source = (self.work / "src/packaging/licenses/__init__.py").read_text()
        self.assertIn(scripted_worker.IMPORT, source)
        self.assertIn(scripted_worker.ALT, source)
        snapshot = self.root / "snapshot"
        record = capture(self.work, snapshot)
        check_controls(snapshot, record)
        self.assertFalse((snapshot / "checkpoint-control/delete-me.txt").exists())

    def test_wrong_fix_forgeries_are_visible_to_controller(self) -> None:
        self.exercise("wrong_symptom_only")
        source = (self.work / "src/packaging/licenses/__init__.py").read_text()
        self.assertIn(scripted_worker.WRONG, source)
        self.assertIn("test_forged_success", (self.work / "tests/test_metadata.py").read_text())
        self.assertEqual((self.work / "checkpoint-control/forged-evaluation.json").read_text(),
                         '{"accepted":true}\n')


class ReportParserTest(unittest.TestCase):
    def test_behavior_container_has_no_trusted_assertions_mount(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in ("candidate", "deps", "trusted-tests"):
                (root / directory).mkdir()
            wheel_name = "packaging-1-py3-none-any.whl"
            raw = b"fixed wheel bytes"
            observations = {
                case: ({"kind": "exception", "type": "InvalidLicenseExpression"}
                       if expected is None else
                       {"kind": "value", "type": "str", "value": expected})
                for case, expected in EXPECTED.items()
            }

            def fake_docker(*args, **kwargs):
                if "--source-date-epoch" in args:
                    (root / "case-wheel" / wheel_name).write_bytes(raw)
                    return SimpleNamespace(stdout=json.dumps({"build_ok": True, "wheel": {
                        "name": wheel_name, "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest()}}))
                if "observations" in args:
                    self.assertFalse(any("/trusted" in arg for arg in args))
                    return SimpleNamespace(stdout=json.dumps({
                        "schema": "solcodex.packaging-installed-observations.v2",
                        "phase": "observations",
                        "install_ok": True, "import_ok": True, "failure_stage": None,
                        "observations_complete": True, "observations": observations}))
                self.assertIn("/trusted/tests", " ".join(args))
                return SimpleNamespace(stdout=json.dumps({
                    "schema": "solcodex.packaging-installed-observations.v2",
                    "phase": "upstream", "install_ok": True,
                    "import_ok": True, "failure_stage": None,
                    "upstream_exit": 0, "upstream_summary": {"tests": 1, "failures": 0,
                                                              "errors": 0, "skipped": 0}}))

            containers = []
            with patch.object(runner, "docker", side_effect=fake_docker):
                result = runner.build_and_evaluate(
                    root / "candidate", "case", root / "deps", root / "trusted-tests",
                    root, 1761699550, "suffix", containers)
            self.assertEqual(result["evaluation"]["behavior"]["passed"], 14)
            self.assertEqual(len(containers), 3)

    def test_worker_waits_for_atomic_marker_before_kill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            pending = work / ".checkpoint-ready.tmp"
            pending.write_text("alternate\n")
            marker = work / ".checkpoint-ready"
            running = True
            calls = []

            def fake_docker(*args, **kwargs):
                nonlocal running
                calls.append(args[:1])
                if args[0] == "inspect":
                    data = {"Mounts": [{"Type": "bind", "Destination": path}
                                       for path in ("/work", "/opt/worker.py")],
                            "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True,
                                           "Privileged": False, "CapDrop": ["ALL"],
                                           "SecurityOpt": ["no-new-privileges:true"],
                                           "Memory": 2 * 1024**3, "NanoCpus": 2_000_000_000},
                            "Config": {"User": "65534:65534"},
                            "State": {"Running": running}}
                    return SimpleNamespace(stdout=json.dumps([data]))
                if args[0] == "kill":
                    self.assertEqual(marker.read_text(), "alternate\n")
                    running = False
                if args[0] == "wait":
                    return SimpleNamespace(stdout="137\n")
                if args[0] == "logs":
                    return SimpleNamespace(stdout=json.dumps(
                        {"phase": "checkpoint_ready", "scenario": "alternate"}) + "\n")
                return SimpleNamespace(stdout="")

            def fake_wait(predicate, label):
                self.assertFalse(predicate())
                self.assertTrue(pending.exists())
                pending.rename(marker)
                self.assertTrue(predicate())

            with patch.object(runner, "docker", side_effect=fake_docker), \
                    patch.object(runner, "wait_for", side_effect=fake_wait):
                observed = runner.worker_checkpoint(work, "alternate", "worker", [])
            self.assertTrue(observed["worker_stopped_before_capture"])
            self.assertIn(("kill",), calls)

    def test_protocol_is_pinned_and_current(self) -> None:
        data, digest = protocol()
        self.assertEqual(len(data["scenarios_in_order"]), 6)
        self.assertEqual(len(digest), 64)

    def test_controller_judges_exact_inventory_and_values(self) -> None:
        observations = {
            case: ({"kind": "exception", "type": "InvalidLicenseExpression"}
                   if expected is None else
                   {"kind": "value", "type": "str", "value": expected})
            for case, expected in EXPECTED.items()
        }
        self.assertEqual(judge_observations(observations)["passed"], 14)
        observations["nested-and-or"]["value"] = "wrong"
        self.assertEqual(judge_observations(observations)["passed"], 13)
        del observations["invalid-6"]
        with self.assertRaises(ValueError):
            judge_observations(observations)

    def test_fail_open_upstream_result_rejected(self) -> None:
        report = {"build": {"build_ok": True}, "evaluation": {
            "install_ok": True, "upstream_install_ok": True,
            "upstream_import_ok": True,
            "import_ok": True, "observations_complete": True,
            "behavior": {"passed": 14, "total": 14}, "failure_stage": None,
            "upstream_exit": None, "upstream_summary": None}}
        self.assertFalse(accepted(report, 14))
        report["evaluation"]["upstream_exit"] = 0
        report["evaluation"]["upstream_summary"] = {
            "tests": 1, "errors": 0, "skipped": 0, "failures": 0}
        self.assertTrue(accepted(report, 14))
        report["evaluation"]["failure_stage"] = "upstream_wire"
        self.assertFalse(accepted(report, 14))

    def test_bounded_nofollow_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            regular = root / "regular"
            regular.write_bytes(b"ready\n")
            self.assertEqual(read_regular(regular, 8), b"ready\n")
            with self.assertRaises(ValueError):
                read_regular(regular, 5)
            link = root / "link"
            link.symlink_to(regular)
            with self.assertRaises(OSError):
                read_regular(link, 8)


if __name__ == "__main__":
    unittest.main()
