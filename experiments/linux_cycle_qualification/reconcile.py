"""Append-only mock-provider reconciliation for the Linux lifecycle qualification.

This does not alter the original attempt state or establish real provider billing.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3

from scripts.usage_attempt_ledger import AttemptLedger


FIELDS = frozenset({"attempt_id", "state", "response_id", "input_tokens",
                    "output_tokens", "cached_input_tokens"})


def canonical(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validate(record: dict) -> None:
    if not isinstance(record, dict) or set(record) != FIELDS:
        raise ValueError("provider record fields invalid")
    if not isinstance(record["attempt_id"], str) or not record["attempt_id"]:
        raise ValueError("provider attempt ID missing")
    if record["state"] not in {"accepted", "completed"}:
        raise ValueError("provider state invalid")
    if record["state"] == "accepted":
        if (record["response_id"] is not None and
                (not isinstance(record["response_id"], str) or not record["response_id"])):
            raise ValueError("accepted response ID invalid")
        if any(record[key] is not None for key in
               ("input_tokens", "output_tokens", "cached_input_tokens")):
            raise ValueError("accepted record contains final usage")
    else:
        if not isinstance(record["response_id"], str) or not record["response_id"]:
            raise ValueError("completed record has no response ID")
        values = [record[key] for key in
                  ("input_tokens", "output_tokens", "cached_input_tokens")]
        if any(type(value) is not int or value < 0 for value in values) or values[2] > values[0]:
            raise ValueError("completed record usage invalid")


class ReconciliationLedger:
    def __init__(self, path: Path):
        self.attempts = AttemptLedger(path)
        self.db = self.attempts.db
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS provider_reconciliations ("
            "attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id), "
            "state TEXT NOT NULL CHECK(state IN ('accepted','completed')), "
            "response_id TEXT, input_tokens INTEGER, output_tokens INTEGER, "
            "cached_input_tokens INTEGER, evidence_sha256 TEXT NOT NULL, "
            "PRIMARY KEY(attempt_id,state))"
        )

    def close(self) -> None:
        self.attempts.close()

    def reconcile(self, record: dict) -> None:
        validate(record)
        evidence = hashlib.sha256(canonical(record)).hexdigest()
        values = (record["attempt_id"], record["state"], record["response_id"],
                  record["input_tokens"], record["output_tokens"],
                  record["cached_input_tokens"], evidence)
        with self.attempts.transaction():
            row = self.db.execute(
                "SELECT state,response_id,input_tokens,output_tokens,cached_input_tokens "
                "FROM attempts WHERE attempt_id=?", (record["attempt_id"],)
            ).fetchone()
            if row is None:
                raise ValueError("provider record has no local attempt")
            if row[0] == "completed":
                if ((record["state"] == "completed" and row[1:] != values[2:6]) or
                        (record["state"] == "accepted" and
                         record["response_id"] is not None and
                         row[1] != record["response_id"])):
                    raise ValueError("provider and local completion conflict")
            other = self.db.execute(
                "SELECT attempt_id FROM provider_reconciliations "
                "WHERE response_id=? AND attempt_id<>? LIMIT 1",
                (record["response_id"], record["attempt_id"])
            ).fetchone() if record["response_id"] is not None else None
            if other is not None:
                raise ValueError("provider response ID belongs to another attempt")
            existing = self.db.execute(
                "SELECT state,response_id,input_tokens,output_tokens,cached_input_tokens,"
                "evidence_sha256 FROM provider_reconciliations WHERE attempt_id=?",
                (record["attempt_id"],)
            ).fetchall()
            for prior in existing:
                if (prior[1] is not None and record["response_id"] is not None and
                        prior[1] != record["response_id"]):
                    raise ValueError("provider response ID changed after acceptance")
                if prior[0] == record["state"]:
                    if prior == values[1:]:
                        return
                    raise ValueError("conflicting provider evidence")
            self.db.execute(
                "INSERT INTO provider_reconciliations VALUES(?,?,?,?,?,?,?)", values)

    def summary(self) -> dict:
        rows = self.db.execute(
            "SELECT a.state,a.response_id,a.input_tokens,a.output_tokens,"
            "a.cached_input_tokens,p.state,p.response_id,c.response_id,c.input_tokens,"
            "c.output_tokens,c.cached_input_tokens FROM attempts a "
            "LEFT JOIN provider_reconciliations p ON a.attempt_id=p.attempt_id "
            "AND p.state='accepted' "
            "LEFT JOIN provider_reconciliations c ON a.attempt_id=c.attempt_id "
            "AND c.state='completed'"
        ).fetchall()
        counts = {"locally_completed": 0, "reconciled_after_local_unknown": 0,
                  "accepted_without_usage": 0, "no_provider_record": 0}
        totals = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
        for local_state, local_id, local_input, local_output, local_cached, accepted_state, accepted_id, provider_id, provider_input, provider_output, provider_cached in rows:
            if provider_id is None and accepted_state is None:
                counts["no_provider_record"] += 1
            elif provider_id is None:
                counts["accepted_without_usage"] += 1
            else:
                if local_state == "completed":
                    if (local_id, local_input, local_output, local_cached) != (
                            provider_id, provider_input, provider_output, provider_cached):
                        raise ValueError("reconciliation drift")
                    counts["locally_completed"] += 1
                else:
                    counts["reconciled_after_local_unknown"] += 1
                totals["input_tokens"] += provider_input
                totals["output_tokens"] += provider_output
                totals["cached_input_tokens"] += provider_cached
        return {"attempts": len(rows), "states": counts, "mock_observed_usage": totals,
                "mock_accounting_complete": bool(rows) and
                counts["accepted_without_usage"] == counts["no_provider_record"] == 0,
                "provider_billing_complete": False}


def read_provider_journal(path: Path) -> list[dict]:
    """Read only the controller-owned mock journal, including accepted-only rows."""
    uri = "file:" + str(path.resolve()) + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        rows = db.execute("SELECT attempt_id,state,response_id,input_tokens,"
                          "output_tokens,cached_input_tokens FROM requests ORDER BY rowid").fetchall()
    records = [dict(zip(("attempt_id", "state", "response_id", "input_tokens",
                         "output_tokens", "cached_input_tokens"), row)) for row in rows]
    for record in records:
        validate(record)
    return records
