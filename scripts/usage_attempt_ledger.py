"""Development ledger for observed model-request attempts.

This stores no prompts, response text, authorization headers, or raw SSE frames.
It is not a provider billing ledger. A pending or failed attempt has unknown usage.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class AttemptLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS attempts ("
            "attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, arm TEXT NOT NULL, "
            "state TEXT NOT NULL CHECK(state IN ('pending','completed','unknown')), "
            "response_id TEXT UNIQUE, upstream_status INTEGER, "
            "input_tokens INTEGER, output_tokens INTEGER, cached_input_tokens INTEGER, "
            "reason TEXT)"
        )

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def start(self, run_id, arm, attempt_id=None):
        if not run_id or not arm:
            raise ValueError("run_id and arm are required")
        attempt_id = attempt_id or str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO attempts(attempt_id,run_id,arm,state) VALUES(?,?,?,'pending')",
            (attempt_id, run_id, arm),
        )
        return attempt_id

    def complete(self, attempt_id, response_id, input_tokens, output_tokens,
                 cached_input_tokens, upstream_status=200):
        if not response_id:
            raise ValueError("response_id is required for deduplication")
        values = (input_tokens, output_tokens, cached_input_tokens)
        if any(type(n) is not int or n < 0 for n in values):
            raise ValueError("usage must contain nonnegative integer token counts")
        if cached_input_tokens > input_tokens:
            raise ValueError("cached input exceeds total input")
        with self.transaction():
            row = self.db.execute(
                "SELECT state,response_id,input_tokens,output_tokens,cached_input_tokens "
                "FROM attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            if row[0] == "completed":
                if row[1:] == (response_id,) + values:
                    return
                raise ValueError("conflicting completion for attempt")
            if row[0] != "pending":
                raise ValueError("attempt has a terminal unknown state")
            self.db.execute(
                "UPDATE attempts SET state='completed',response_id=?,upstream_status=?,"
                "input_tokens=?,output_tokens=?,cached_input_tokens=? WHERE attempt_id=?",
                (response_id, upstream_status) + values + (attempt_id,),
            )

    def unknown(self, attempt_id, reason, upstream_status=None):
        if not reason:
            raise ValueError("reason is required")
        with self.transaction():
            row = self.db.execute("SELECT state FROM attempts WHERE attempt_id=?",
                                  (attempt_id,)).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            if row[0] == "completed":
                raise ValueError("completed usage cannot be replaced with unknown")
            self.db.execute(
                "UPDATE attempts SET state='unknown',reason=?,upstream_status=? "
                "WHERE attempt_id=?", (reason, upstream_status, attempt_id)
            )

    def summary(self, run_id=None):
        where = "WHERE run_id=?" if run_id is not None else ""
        args = (run_id,) if run_id is not None else ()
        rows = self.db.execute(
            "SELECT state,input_tokens,output_tokens,cached_input_tokens "
            "FROM attempts " + where, args
        ).fetchall()
        counts = {"pending": 0, "completed": 0, "unknown": 0}
        totals = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
        for state, input_tokens, output_tokens, cached_input_tokens in rows:
            counts[state] += 1
            if state == "completed":
                totals["input_tokens"] += input_tokens
                totals["output_tokens"] += output_tokens
                totals["cached_input_tokens"] += cached_input_tokens
        return {"attempts": len(rows), "states": counts,
                "observed_completed_usage": totals,
                "all_attempts_have_observed_usage": bool(rows) and counts["pending"] == 0
                and counts["unknown"] == 0,
                "provider_billing_complete": False}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    ledger = AttemptLedger(args.database)
    try:
        print(json.dumps(ledger.summary(args.run_id), sort_keys=True))
    finally:
        ledger.close()
