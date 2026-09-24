"""Meaningful storage, restart, and accounting boundaries for the prototype ledger."""
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

try:
    from scripts.usage_attempt_ledger import AttemptLedger
except ModuleNotFoundError:
    from usage_attempt_ledger import AttemptLedger


class AttemptLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "attempts.sqlite3"

    def test_reopen_keeps_unknown_pending_and_committed_completion(self):
        ledger = AttemptLedger(self.path)
        first = ledger.start("run-1", "control", "attempt-1")
        second = ledger.start("run-1", "variant", "attempt-2")
        ledger.close()
        ledger = AttemptLedger(self.path)
        self.assertEqual(ledger.summary("run-1")["states"]["pending"], 2)
        ledger.complete(first, "response-1", 120, 14, 80)
        ledger.unknown(second, "upstream_eof", 200)
        ledger.close()
        ledger = AttemptLedger(self.path)
        summary = ledger.summary("run-1")
        self.assertEqual(summary["states"], {"pending": 0, "completed": 1, "unknown": 1})
        self.assertEqual(summary["observed_completed_usage"],
                         {"input_tokens": 120, "output_tokens": 14, "cached_input_tokens": 80})
        self.assertFalse(summary["all_attempts_have_observed_usage"])
        self.assertFalse(summary["provider_billing_complete"])
        ledger.close()

    def test_rejects_duplicate_response_and_conflicting_usage(self):
        ledger = AttemptLedger(self.path)
        a = ledger.start("run", "control")
        b = ledger.start("run", "control")
        ledger.complete(a, "response", 10, 3, 0)
        ledger.complete(a, "response", 10, 3, 0)
        with self.assertRaises(ValueError):
            ledger.complete(a, "response", 11, 3, 0)
        with self.assertRaises(sqlite3.IntegrityError):
            ledger.complete(b, "response", 10, 3, 0)
        self.assertEqual(ledger.summary()["states"]["pending"], 1)
        ledger.close()

    def test_process_kill_leaves_committed_intent_without_inventing_zero(self):
        code = (
            "import sys,time; from scripts.usage_attempt_ledger import AttemptLedger; "
            "j=AttemptLedger(sys.argv[1]); j.start('run','control','killed'); "
            "print('READY',flush=True); time.sleep(30)"
        )
        proc = subprocess.Popen([sys.executable, "-c", code, str(self.path)],
                                cwd=Path(__file__).resolve().parent.parent,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "READY")
            proc.kill()
            proc.communicate(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)
        ledger = AttemptLedger(self.path)
        summary = ledger.summary("run")
        self.assertEqual(summary["states"], {"pending": 1, "completed": 0, "unknown": 0})
        self.assertFalse(summary["all_attempts_have_observed_usage"])
        ledger.close()


if __name__ == "__main__":
    unittest.main()
