"""Failure-state and idempotence checks for independent mock reconciliation."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from experiments.linux_cycle_qualification.reconcile import ReconciliationLedger


def evidence(attempt: str, *, completed: bool = True, output: int = 3) -> dict:
    return {"attempt_id": attempt, "state": "completed" if completed else "accepted",
            "response_id": "response-" + attempt,
            "input_tokens": 11 if completed else None,
            "output_tokens": output if completed else None,
            "cached_input_tokens": 4 if completed else None}


class ReconciliationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = ReconciliationLedger(Path(self.tmp.name) / "attempts.sqlite3")
        self.addCleanup(self.ledger.close)

    def test_completed_and_recovered_usage_count_once(self) -> None:
        local = self.ledger.attempts
        a = local.start("run", "arm", "a")
        b = local.start("run", "arm", "b")
        local.complete(a, "response-a", 11, 3, 4)
        local.unknown(b, "broker_crashed")
        self.ledger.reconcile(evidence(a))
        self.ledger.reconcile(evidence(b, completed=False))
        accepted_hash = self.ledger.db.execute(
            "SELECT evidence_sha256 FROM provider_reconciliations "
            "WHERE attempt_id=? AND state='accepted'", (b,)).fetchone()[0]
        self.assertFalse(self.ledger.summary()["mock_accounting_complete"])
        self.ledger.reconcile(evidence(b))
        self.ledger.reconcile(evidence(b))
        summary = self.ledger.summary()
        self.assertEqual(summary["states"], {
            "locally_completed": 1, "reconciled_after_local_unknown": 1,
            "accepted_without_usage": 0, "no_provider_record": 0})
        self.assertEqual(summary["mock_observed_usage"], {
            "input_tokens": 22, "output_tokens": 6, "cached_input_tokens": 8})
        self.assertTrue(summary["mock_accounting_complete"])
        self.assertEqual(local.summary()["states"]["unknown"], 1)
        observations = self.ledger.db.execute(
            "SELECT state,evidence_sha256 FROM provider_reconciliations "
            "WHERE attempt_id=? ORDER BY state", (b,)).fetchall()
        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0], ("accepted", accepted_hash))
        self.assertNotEqual(observations[0][1], observations[1][1])

    def test_conflict_and_orphan_do_not_modify_accounting(self) -> None:
        local = self.ledger.attempts
        local.start("run", "arm", "a")
        self.ledger.reconcile(evidence("a"))
        before = self.ledger.summary()
        with self.assertRaisesRegex(ValueError, "conflicting provider evidence"):
            self.ledger.reconcile(evidence("a", output=99))
        with self.assertRaisesRegex(ValueError, "no local attempt"):
            self.ledger.reconcile(evidence("orphan"))
        self.assertEqual(self.ledger.summary(), before)

    def test_local_provider_mismatch_rejected(self) -> None:
        local = self.ledger.attempts
        local.start("run", "arm", "a")
        local.complete("a", "response-a", 11, 3, 4)
        with self.assertRaisesRegex(ValueError, "completion conflict"):
            self.ledger.reconcile(evidence("a", output=4))
        self.assertFalse(self.ledger.summary()["mock_accounting_complete"])

    def test_missing_provider_evidence_stays_unknown(self) -> None:
        self.ledger.attempts.start("run", "arm", "a")
        self.assertEqual(self.ledger.summary()["states"]["no_provider_record"], 1)
        self.assertFalse(self.ledger.summary()["mock_accounting_complete"])

    def test_response_id_cannot_change_after_acceptance(self) -> None:
        self.ledger.attempts.start("run", "arm", "a")
        self.ledger.reconcile(evidence("a", completed=False))
        changed = evidence("a")
        changed["response_id"] = "response-other"
        with self.assertRaisesRegex(ValueError, "changed after acceptance"):
            self.ledger.reconcile(changed)
        self.assertEqual(self.ledger.summary()["states"]["accepted_without_usage"], 1)


if __name__ == "__main__":
    unittest.main()
