"""Ensure verifier hook boundary evidence fails closed without leaking marker text."""
import contextlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    from scripts.audit_verifier_prehook_probe import inspect, main
except ModuleNotFoundError:
    from audit_verifier_prehook_probe import inspect, main


class VerifierPrehookAuditTests(unittest.TestCase):
    def fixture(self, root, prehook, expected_exit=0):
        marker = b"HIDDEN_" + b"a" * 24
        work, artifacts = root / "work", root / "artifacts"
        work.mkdir()
        artifacts.mkdir()
        if expected_exit:
            fixture = (
                "import pathlib,sys,unittest\n"
                "class Probe(unittest.TestCase):\n"
                " def test_output(self):\n"
                "  p=pathlib.Path('run-count.log')\n"
                "  with p.open('a') as out:out.write('run\\n')\n"
                "  sys.stdout.write('A'*8000+'\\n'+" + repr(marker.decode()) + "+'\\n')\n"
                "  sys.stdout.flush()\n"
                "  self.fail('expected diagnostic failure')\n"
            ).encode()
        else:
            fixture = b"value = " + repr(marker.decode()).encode() + b"\n"
        (work / "test_probe.py").write_bytes(fixture)
        (work / "run-count.log").write_text("run\n")
        executable = "/tmp/fixed-python3"
        command = executable + " -m unittest -v test_probe"
        stdout = b"A" * 8000 + b"\n" + marker + b"\n"
        if expected_exit:
            stderr = (b"test_output (test_probe.Probe.test_output) ... FAIL\n\n" +
                      b"======================================================================\n" +
                      b"FAIL: test_output (test_probe.Probe.test_output)\n" +
                      b"----------------------------------------------------------------------\n" +
                      b"Traceback (most recent call last):\n" +
                      ("  File \"" + str(work / "test_probe.py") + "\", line 8, in test_output\n").encode() +
                      b"    self.fail('expected diagnostic failure')\n" +
                      b"AssertionError: expected diagnostic failure\n\n" +
                      b"Ran 1 test in 0.000s\n\nFAILED (failures=1)\n")
        else:
            stderr = (b"test_output (test_probe.Probe.test_output) ... ok\n\n" +
                      b"----------------------------------------------------------------------\n" +
                      b"Ran 1 test in 0.000s\n\nOK\n")
        if prehook:
            (artifacts / "stdout.bin").write_bytes(stdout)
            (artifacts / "stderr.bin").write_bytes(stderr)
            (artifacts / "hook-events.jsonl").write_text(json.dumps({
                "hook_event_name": "PreToolUse", "tool_name": "Bash",
                "tool_input_keys": ["command"], "command_matches_exact": True}) + "\n")
            (work / "capture.py").write_text(
                "import pathlib,subprocess,sys\n"
                "root=pathlib.Path(" + repr(str(artifacts)) + ")\n"
                "with (root/'stdout.bin').open('wb') as out, (root/'stderr.bin').open('wb') as err:\n"
                " code=subprocess.run([" + repr(executable) + ",'-m','unittest','-v','test_probe']" +
                ",stdout=out,stderr=err).returncode\n"
                "size=(root/'stdout.bin').stat().st_size+(root/'stderr.bin').stat().st_size\n"
                "print(f'CAPTURED_BYTES={size} STATUS={code}')\n"
                "raise SystemExit(code)\n")
            hook = root / "market/plugins/verifier-event-probe/scripts/hook.py"
            hook.parent.mkdir(parents=True)
            hook.write_text("if cmd==" + repr(command) + ":\n output=" +
                            repr(executable + " capture.py") + "\n")
        requests = [{"upstream_status": 200, "done": True,
                     "client_disconnected": False, "upstream_error": None,
                     "body_bytes": 100 + i,
                     "request_has_probe_marker": i == 1 and not prehook,
                     "completions": [{"input_tokens": 10 + i,
                                      "output_tokens": 1, "cached_tokens": 2}]}
                    for i in range(2)]
        summary = {"exit": 0, "proxy_blocked": 4 if prehook else 0,
                   "sink_requests": requests,
                   "journal": {"attempts": 2, "states": {"completed": 2},
                               "all_attempts_have_observed_usage": True,
                               "provider_billing_complete": False,
                               "observed_completed_usage": {
                                   "input_tokens": 21, "output_tokens": 2,
                                   "cached_input_tokens": 4}}}
        (artifacts / "proxy-summary.json").write_text(json.dumps(summary))
        if prehook:
            output = "CAPTURED_BYTES=%d STATUS=%d" % (len(stdout) + len(stderr), expected_exit)
        elif expected_exit:
            prefix = b"test_output (test_probe.Probe.test_output) ... "
            output = (prefix + stdout + stderr[len(prefix):]).decode()
        else:
            output = (stdout + stderr).decode()
        executed = executable + (" capture.py" if prehook else " -m unittest -v test_probe")
        events = [{"type": "item.completed", "item": {"type": "command_execution",
                    "command": "/bin/zsh -lc " + shlex.quote(executed),
                    "exit_code": expected_exit, "aggregated_output": output}},
                  {"type": "item.completed", "item": {"type": "agent_message",
                    "text": "UNKNOWN" if prehook else marker.decode()}},
                  {"type": "turn.completed", "usage": {"input_tokens": 1,
                    "output_tokens": 1}}]
        (artifacts / "trace.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
        return marker, summary

    def test_direct_and_prehook_marker_boundary_without_marker_leak(self):
        for prehook in (False, True):
            with self.subTest(prehook=prehook), tempfile.TemporaryDirectory() as directory:
                marker, _ = self.fixture(Path(directory), prehook)
                report = inspect(Path(directory), prehook)
                self.assertEqual(report["request_marker_flags"],
                                 [False, False] if prehook else [False, True])
                self.assertEqual(report["verifier_child_executions"], 1)
                self.assertNotIn(marker.decode(), json.dumps(report))

    def test_nonzero_verifier_status_survives_capture_and_tampering_fails(self):
        for prehook in (False, True):
            with self.subTest(prehook=prehook), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker, _ = self.fixture(root, prehook, expected_exit=1)
                report = inspect(root, prehook, expected_exit=1)
                self.assertEqual(report["verifier_exit_code"], 1)
                self.assertNotIn(marker.decode(), json.dumps(report))
                trace = root / "artifacts/trace.jsonl"
                events = [json.loads(line) for line in trace.read_text().splitlines()]
                events[0]["item"]["exit_code"] = 0
                trace.write_text("\n".join(json.dumps(event) for event in events) + "\n")
                with self.assertRaises(ValueError):
                    inspect(root, prehook, expected_exit=1)
                if prehook:
                    events[0]["item"]["exit_code"] = 1
                    events[0]["item"]["aggregated_output"] = events[0]["item"]["aggregated_output"].replace(
                        "STATUS=1", "STATUS=0")
                    trace.write_text("\n".join(json.dumps(event) for event in events) + "\n")
                    with self.assertRaises(ValueError):
                        inspect(root, True, expected_exit=1)

    def test_nonzero_helper_fixture_traceback_and_private_error_fail_closed(self):
        with tempfile.TemporaryDirectory() as direct_dir, tempfile.TemporaryDirectory() as hook_dir:
            direct, hook = Path(direct_dir), Path(hook_dir)
            self.fixture(direct, False, expected_exit=1)
            self.fixture(hook, True, expected_exit=1)
            helper = hook / "work/capture.py"
            original_helper = helper.read_text()
            helper.write_text(original_helper.replace("raise SystemExit(code)", "raise SystemExit(0)"))
            with self.assertRaises(ValueError):
                inspect(hook, True, expected_exit=1)
            helper.write_text(original_helper)
            fixture = hook / "work/test_probe.py"
            original_fixture = fixture.read_text()
            fixture.write_text(original_fixture.replace("  self.fail('expected diagnostic failure')",
                                                        "  # self.fail('expected diagnostic failure')"))
            with self.assertRaises(ValueError):
                inspect(hook, True, expected_exit=1)
            fixture.write_text(original_fixture)
            stderr = hook / "artifacts/stderr.bin"
            original_stderr = stderr.read_bytes()
            stderr.write_bytes(original_stderr.replace(b"Traceback (most recent call last):\n", b""))
            with self.assertRaises(ValueError):
                inspect(hook, True, expected_exit=1)
            stderr.write_bytes(original_stderr)
            helper.write_text("(\n# PRIVATE_SENTINEL_123\n")
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("audit_verifier_prehook_probe.py")),
                 "--direct", str(direct), "--prehook", str(hook), "--expected-exit", "1"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "Audit failed: private input rejected\n")
            self.assertNotIn(str(hook), result.stderr)
            self.assertNotIn("PRIVATE_SENTINEL_123", result.stderr)

    def test_missing_provider_completion_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, summary = self.fixture(root, True)
            summary["sink_requests"][1]["completions"] = []
            (root / "artifacts/proxy-summary.json").write_text(json.dumps(summary))
            with self.assertRaises(ValueError):
                inspect(root, True)

    def test_wrong_event_and_duplicate_child_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, True)
            hook_path = root / "artifacts/hook-events.jsonl"
            valid_hook = hook_path.read_text()
            hook_path.write_text(json.dumps({
                "hook_event_name": "PreToolUse", "tool_name": "Other",
                "tool_input_keys": ["command"], "command_matches_exact": True}) + "\n")
            with self.assertRaises(ValueError):
                inspect(root, True)
            hook_path.write_text(valid_hook)
            (root / "work/run-count.log").write_text("run\nrun\n")
            with self.assertRaises(ValueError):
                inspect(root, True)

    def test_compact_status_and_artifact_integrity_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, True)
            trace = root / "artifacts/trace.jsonl"
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            events[0]["item"]["aggregated_output"] = "CAPTURE_FAILED STATUS=1"
            trace.write_text("\n".join(json.dumps(event) for event in events) + "\n")
            with self.assertRaises(ValueError):
                inspect(root, True)
            self.fixture_status_restore(root)
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            del events[0]["item"]["aggregated_output"]
            trace.write_text("\n".join(json.dumps(event) for event in events) + "\n")
            with self.assertRaises(ValueError):
                inspect(root, True)
            self.fixture_status_restore(root)
            (root / "artifacts/stdout.bin").write_bytes(b"A" * 8000 + b"HIDDEN_" + b"a" * 24)
            with self.assertRaises(ValueError):
                inspect(root, True)
            self.fixture_stdout_restore(root)
            stderr = root / "artifacts/stderr.bin"
            stderr.unlink()
            stderr.symlink_to(root / "artifacts/stdout.bin")
            with self.assertRaises(ValueError):
                inspect(root, True)

    @staticmethod
    def fixture_status_restore(root):
        trace = root / "artifacts/trace.jsonl"
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        events[0]["item"]["aggregated_output"] = "CAPTURED_BYTES=8180 STATUS=0"
        trace.write_text("\n".join(json.dumps(event) for event in events) + "\n")

    @staticmethod
    def fixture_stdout_restore(root):
        marker = b"HIDDEN_" + b"a" * 24
        (root / "artifacts/stdout.bin").write_bytes(b"A" * 8000 + b"\n" + marker + b"\n")

    def test_wrong_direct_command_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, False)
            trace = root / "artifacts/trace.jsonl"
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            events[0]["item"]["command"] = "/bin/zsh -lc 'printf unrelated'"
            trace.write_text("\n".join(json.dumps(event) for event in events) + "\n")
            with self.assertRaises(ValueError):
                inspect(root, False)

    def test_earlier_model_message_marker_and_mixed_fixture_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker, _ = self.fixture(root, True)
            trace = root / "artifacts/trace.jsonl"
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            events.insert(1, {"type": "item.completed", "item": {
                "type": "agent_message", "text": marker.decode()}})
            trace.write_text("\n".join(json.dumps(event) for event in events) + "\n")
            with self.assertRaises(ValueError):
                inspect(root, True)
        with tempfile.TemporaryDirectory() as direct_dir, tempfile.TemporaryDirectory() as hook_dir:
            direct, hook = Path(direct_dir), Path(hook_dir)
            self.fixture(direct, False)
            self.fixture(hook, True)
            with (hook / "work/test_probe.py").open("ab") as stream:
                stream.write(b"# changed fixture\n")
            with mock.patch.object(sys, "argv", ["audit", "--direct", str(direct),
                                                "--prehook", str(hook)]), \
                    contextlib.redirect_stderr(io.StringIO()) as errors, \
                    self.assertRaises(SystemExit):
                main()
            self.assertEqual(errors.getvalue(), "Audit failed: private input rejected\n")


if __name__ == "__main__":
    unittest.main()
