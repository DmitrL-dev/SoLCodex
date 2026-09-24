#!/usr/bin/env python3
"""Focused subprocess checks for the opt-in research adapter (stdlib only)."""

import ast
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import receipt_command as adapter


SCRIPT = Path(__file__).with_name("receipt_command.py")


@unittest.skipUnless(os.name == "posix", "prototype requires POSIX")
class ReceiptCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.artifacts = self.root / "artifacts"

    def command(self, code, *args):
        return [sys.executable, str(SCRIPT), "--artifact-dir", str(self.artifacts),
                "--", sys.executable, "-c", code, *args]

    def run_code(self, code, *args):
        result = subprocess.run(self.command(code, *args), capture_output=True, timeout=15)
        return result, self.receipt(result)

    def receipt(self, result):
        self.assertEqual(result.stderr, b"")
        self.assertLessEqual(len(result.stdout), adapter.MAX_RECEIPT_BYTES)
        receipt = json.loads(result.stdout)
        self.assertEqual(result.returncode, receipt["wrapper_exit_code"])
        if receipt["path"]:
            path = Path(receipt["path"])
            data = path.read_bytes()
            self.assertEqual(len(data), receipt["bytes"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), receipt["sha256"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(path.parent.parent, self.artifacts.resolve())
        return receipt

    def test_success_combines_streams(self):
        result, receipt = self.run_code("import os; os.write(1,b'hello\\n'); os.write(2,b'world\\n')")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(receipt["exit_code"], 0)
        self.assertTrue(receipt["capture_complete"])
        self.assertEqual(Path(receipt["path"]).read_bytes(), b"hello\nworld\n")
        self.assertEqual(receipt["head"][1]["line"], 2)

    def test_failure_exact_code_and_diagnostic(self):
        result, receipt = self.run_code("import sys; print('ERROR broken',file=sys.stderr); sys.exit(37)")
        self.assertEqual(result.returncode, 37)
        self.assertEqual(receipt["exit_code"], 37)
        self.assertEqual(receipt["diagnostics"][0]["text"], "ERROR broken")

    def test_preserves_high_exit_codes(self):
        for code in (125, 126, 127, 255):
            with self.subTest(code=code):
                result, receipt = self.run_code("import sys; sys.exit(" + str(code) + ")")
                self.assertEqual(result.returncode, code)
                self.assertEqual(receipt["exit_code"], code)
                self.assertEqual(receipt["status"], "completed")

    def test_noisy_middle_diagnostic_and_no_raw_leak(self):
        code = "import os; os.write(1,b'noise\\n'*10000+b'ERROR middle\\n'+b'noise\\n'*10000)"
        result, receipt = self.run_code(code)
        self.assertEqual(receipt["diagnostics"][0]["line"], 10001)
        self.assertEqual(receipt["diagnostics"][0]["text"], "ERROR middle")
        self.assertEqual(receipt["lines"], 20001)
        self.assertLess(len(result.stdout), 3000)
        self.assertLess(result.stdout.count(b"noise"), 10)

    def test_error_survives_warning_flood_and_long_line(self):
        code = ("import os; os.write(1,b'warning noise\\n'*100+"
                "b'x'*1000+b' ERROR middle-line root cause\\n')")
        _, receipt = self.run_code(code)
        self.assertEqual(receipt["diagnostics"][0]["line"], 101)
        self.assertEqual(receipt["diagnostics"][0]["text"],
                         "Diagnostic on overlong line; inspect private artifact")

    def test_explicit_diagnostics_displace_early_weak_matches(self):
        decoys = [b"test_case[exception-%d] PASSED [ 70%%]\n" % index for index in range(6)]
        self.assertTrue(all(adapter.ERROR_SIGNAL.search(line) for line in decoys))
        explicit = [b"ERROR build failed", b"E    assert actual == expected",
                    b"FATAL broken state", b"> assert value is not None",
                    b"Traceback (most recent call last):", b"PANIC unrecoverable"]
        payload = (b"".join(decoys) + b"noise\n" * 5300 +
                   b"warning: many\n" * 20 +
                   b"\n".join(explicit) + b"\n" +
                   b"later weak exception\n" * 20)
        expected_lines = [item.decode() for item in explicit]
        for chunk_size in (1, 37, 65536):
            with self.subTest(chunk_size=chunk_size):
                summary = adapter.Summary()
                for start in range(0, len(payload), chunk_size):
                    summary.feed(payload[start:start + chunk_size])
                summary.finish()
                self.assertEqual([item["text"] for item in summary.diagnostics],
                                 expected_lines)
                self.assertEqual(summary.size, len(payload))
                self.assertEqual(summary.digest.hexdigest(), hashlib.sha256(payload).hexdigest())
        payload_file = self.root / "verbose-output.bin"
        payload_file.write_bytes(payload)
        result, receipt = self.run_code(
            "import os,pathlib,sys; os.write(1,pathlib.Path(sys.argv[1]).read_bytes())",
            str(payload_file))
        self.assertEqual(result.returncode, 0)
        self.assertTrue(receipt["capture_complete"])
        self.assertEqual([item["text"] for item in receipt["diagnostics"]], expected_lines)

    def test_compiler_error_remains_above_warning_with_error_word(self):
        summary = adapter.Summary()
        summary.feed(b"warning: Exception may be thrown\n"
                     b"src/main.c:22: error: unknown token\n"
                     b"ERROR service refused request\n")
        summary.finish()
        self.assertEqual([item["text"] for item in summary.diagnostics], [
            "src/main.c:22: error: unknown token", "ERROR service refused request",
            "warning: Exception may be thrown"])

    def test_early_compiler_error_survives_later_explicit_noise(self):
        summary = adapter.Summary()
        summary.feed(b"src/main.c:22: error: undefined symbol\n" +
                     b"ERROR unrelated follow-up\n" * 6)
        summary.finish()
        self.assertEqual(summary.diagnostics[0]["text"],
                         "src/main.c:22: error: undefined symbol")

    def test_long_explicit_placeholders_do_not_displace_short_error(self):
        payload = (b"> assert " + b"x" * 300 + b"\n") * 6 + b"ERROR decisive root cause\n"
        payload_file = self.root / "overlong-output.bin"
        payload_file.write_bytes(payload)
        _, receipt = self.run_code(
            "import os,pathlib,sys; os.write(1,pathlib.Path(sys.argv[1]).read_bytes())",
            str(payload_file))
        self.assertEqual(receipt["diagnostics"][0]["text"], "ERROR decisive root cause")
        self.assertTrue(all("xxxx" not in item["text"] for item in receipt["diagnostics"]))

    def test_overlong_warning_placeholder_is_neutral(self):
        summary = adapter.Summary()
        summary.feed(b"warning: " + b"x" * 300 + b"\n")
        summary.finish()
        self.assertEqual(summary.diagnostics[0]["text"],
                         "Diagnostic on overlong line; inspect private artifact")

    def test_long_line_diagnostic_does_not_leak_secret_after_clipping(self):
        token = "DUMMY_PRIVATE_VALUE"
        code = ("print('x'*300+' Authorization: Bearer " + token + "' +"
                "' '*50+'ERROR failed')")
        _, receipt = self.run_code(code)
        self.assertIn(token, Path(receipt["path"]).read_text())
        self.assertNotIn(token, json.dumps(receipt))

    def test_known_secret_shapes_are_redacted_only_in_receipt(self):
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        _, receipt = self.run_code("print('ERROR " + secret + "'); "
                                   "print('Authorization: Bearer topsecretvalue')")
        self.assertIn(secret, Path(receipt["path"]).read_text())
        self.assertNotIn(secret, json.dumps(receipt))
        self.assertNotIn("topsecretvalue", json.dumps(receipt))
        self.assertIn("[REDACTED]", receipt["diagnostics"][0]["text"])

    def test_timeout_preserves_partial_output_and_status(self):
        command = [sys.executable, str(SCRIPT), "--artifact-dir", str(self.artifacts),
                   "--timeout-seconds", "1.0", "--", sys.executable, "-c",
                   "import os,time; os.write(1,b'partial\\n'); time.sleep(30)"]
        result = subprocess.run(command, capture_output=True, timeout=5)
        receipt = self.receipt(result)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(receipt["status"], "timeout")
        self.assertFalse(receipt["capture_complete"])
        self.assertEqual(Path(receipt["path"]).read_bytes(), b"partial\n")

    def test_timeout_kills_descendant_that_closed_output_pipe(self):
        marker = self.root / "orphan-wrote"
        descendant = ("import os,signal,time,pathlib; "
                      "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                      "os.close(1); os.close(2); time.sleep(0.8); "
                      "pathlib.Path(" + repr(str(marker)) + ").write_text('alive')")
        parent = ("import subprocess,sys,time; "
                  "subprocess.Popen([sys.executable,'-c'," + repr(descendant) + "]); "
                  "print('started',flush=True); time.sleep(30)")
        command = [sys.executable, str(SCRIPT), "--artifact-dir", str(self.artifacts),
                   "--timeout-seconds", "0.2", "--", sys.executable, "-c", parent]
        result = subprocess.run(command, capture_output=True, timeout=5)
        receipt = self.receipt(result)
        self.assertEqual(receipt["status"], "timeout")
        time.sleep(0.9)
        self.assertFalse(marker.exists(), "timed-out descendant survived adapter")

    def test_newline_free_output_is_bounded(self):
        result, receipt = self.run_code("import os; os.write(1,b'x'*5000000+b'RAW_END_MARKER')")
        self.assertEqual(receipt["bytes"], 5000014)
        self.assertEqual(receipt["lines"], 1)
        self.assertTrue(receipt["head"][0]["truncated"])
        self.assertNotIn(b"RAW_END_MARKER", result.stdout)

    def test_binary_utf8_and_control_characters(self):
        data = "Привет ☃\n".encode() + b"\xff\x00\x1b[31m\rfinal"
        result, receipt = self.run_code("import os; os.write(1," + repr(data) + ")")
        self.assertEqual(Path(receipt["path"]).read_bytes(), data)
        self.assertEqual(receipt["lines"], 2)
        self.assertIn("Привет", receipt["head"][0]["text"])
        self.assertNotIn(b"\x1b", result.stdout)
        self.assertNotIn(b"\x00", result.stdout)

    def test_empty_output(self):
        _, receipt = self.run_code("pass")
        self.assertEqual(receipt["bytes"], 0)
        self.assertEqual(receipt["lines"], 0)
        self.assertEqual(receipt["head"], [])

    def test_argv_is_literal_no_shell(self):
        marker = self.root / "not-created"
        literal = "$(touch " + str(marker) + "); echo injected | cat"
        _, receipt = self.run_code("import sys; print(sys.argv[1])", literal)
        self.assertEqual(Path(receipt["path"]).read_text().strip(), literal)
        self.assertFalse(marker.exists())

    def test_stdin_is_closed(self):
        _, receipt = self.run_code("import sys; print(repr(sys.stdin.read()))")
        self.assertEqual(receipt["head"][0]["text"], "''")

    def test_permissions_with_permissive_umask_and_unique_names(self):
        old = os.umask(0)
        try:
            _, first = self.run_code("pass")
            _, second = self.run_code("pass")
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(self.artifacts.stat().st_mode), 0o700)
        self.assertNotEqual(first["path"], second["path"])

    def test_shared_directory_rejected_before_spawn(self):
        self.artifacts.mkdir(mode=0o755)
        self.artifacts.chmod(0o755)
        result, receipt = self.run_code("raise Exception('must not run')")
        self.assertEqual(result.returncode, 125)
        self.assertIsNone(receipt["exit_code"])
        self.assertIsNone(receipt["path"])
        self.assertEqual(list(self.artifacts.iterdir()), [])

    def test_symlink_directory_rejected(self):
        self.artifacts.symlink_to(self.root, target_is_directory=True)
        result, receipt = self.run_code("print('must not run')")
        self.assertEqual(result.returncode, 125)
        self.assertIsNone(receipt["path"])

    def test_failed_spawn_has_empty_verified_artifact(self):
        command = self.command("pass")[:5] + [str(self.root / "missing-secret-program")]
        result = subprocess.run(command, capture_output=True, timeout=5)
        receipt = self.receipt(result)
        self.assertEqual(result.returncode, 125)
        self.assertIsNone(receipt["exit_code"])
        self.assertEqual(receipt["bytes"], 0)
        self.assertFalse(receipt["capture_complete"])
        self.assertNotIn(b"missing-secret-program", result.stdout)

    def test_explicit_separator_and_directory_required(self):
        for args in (["--", sys.executable], ["--artifact-dir", str(self.artifacts)],
                     ["--artifact-dir", str(self.artifacts), sys.executable]):
            result = subprocess.run([sys.executable, str(SCRIPT)] + args,
                                    capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 2)
        self.assertFalse(self.artifacts.exists())

    def test_child_signal_has_exact_negative_code(self):
        result, receipt = self.run_code("import os,signal; os.kill(os.getpid(),signal.SIGTERM)")
        self.assertEqual(receipt["exit_code"], -signal.SIGTERM)
        self.assertEqual(receipt["signal"], signal.SIGTERM)
        self.assertEqual(result.returncode, 128 + signal.SIGTERM)

    def test_interrupt_escalates_and_retains_partial_output(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signum=signum):
                ready = self.root / ("ready-" + str(signum))
                code = ("import os,signal,time,pathlib; "
                        "signal.signal(signal.SIGINT,signal.SIG_IGN); "
                        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                        "os.write(1,b'partial\\n'); "
                        "pathlib.Path(" + repr(str(ready)) + ").write_text(str(os.getpid())); "
                        "time.sleep(30)")
                process = subprocess.Popen(self.command(code), stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 5
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(ready.exists(), "child never became ready")
                    process.send_signal(signum)
                    stdout, stderr = process.communicate(timeout=5)
                    receipt = self.receipt(subprocess.CompletedProcess(
                        process.args, process.returncode, stdout, stderr))
                    self.assertEqual(receipt["status"], "interrupted")
                    self.assertEqual(receipt["interrupted_by"], signum)
                    self.assertEqual(receipt["exit_code"], -signal.SIGKILL)
                    self.assertFalse(receipt["capture_complete"])
                    self.assertEqual(Path(receipt["path"]).read_bytes(), b"partial\n")
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(ready.read_text()), 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)

    def test_summary_handles_split_diagnostic_and_bounded_storage(self):
        summary = adapter.Summary()
        summary.feed(b"x" * 100000 + b"\nER")
        summary.feed(b"ROR boundary\n" + b"warning: many\n" * 100)
        summary.finish()
        self.assertEqual(summary.diagnostics[0]["line"], 2)
        self.assertEqual(len(summary.diagnostics), adapter.DIAGNOSTICS)
        self.assertEqual(len(summary.tail), 2)
        self.assertTrue(summary.head[0]["truncated"])
        self.assertNotIn("xxxxx", summary.head[0]["text"])

    def test_write_failure_stops_child_and_emits_safe_receipt(self):
        artifact, path = adapter.create_artifact(self.artifacts)
        faulty = mock.Mock(wraps=artifact)
        faulty.write.side_effect = OSError(28, "sensitive exception text")
        output = io.BytesIO()
        child_pid = self.root / "write-failure-pid"
        code = ("import os,pathlib,time; pathlib.Path(" + repr(str(child_pid))
                + ").write_text(str(os.getpid())); os.write(1,b'RAW_SECRET'); time.sleep(30)")
        with mock.patch.object(adapter, "create_artifact", return_value=(faulty, path)), \
                mock.patch.object(sys, "stdout", mock.Mock(buffer=output)):
            status = adapter.main(["--artifact-dir", str(self.artifacts), "--",
                                   sys.executable, "-c", code])
        receipt = self.receipt(subprocess.CompletedProcess([], status, output.getvalue(), b""))
        self.assertEqual(status, 125)
        self.assertEqual(receipt["status"], "capture_error")
        self.assertFalse(receipt["capture_complete"])
        self.assertEqual(receipt["error"]["errno"], 28)
        self.assertNotIn(b"sensitive exception", output.getvalue())
        self.assertNotIn(b"RAW_SECRET", output.getvalue())
        with self.assertRaises(ProcessLookupError):
            os.kill(int(child_pid.read_text()), 0)

    def test_receipt_limit_with_escaped_binary_previews(self):
        result, receipt = self.run_code("import os; os.write(1,(b'ERROR '+b'\\xff'*240+b'\\n')*50)")
        self.assertLessEqual(len(result.stdout), adapter.MAX_RECEIPT_BYTES)
        self.assertNotIn("\\ufffd", json.dumps(receipt))

    def test_python39_syntax(self):
        for path in (SCRIPT, Path(__file__)):
            ast.parse(path.read_text(), filename=str(path), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
