"""Exercise broker, mock upstream, and ledger on real local HTTP sockets."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sqlite3
import struct
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen

from experiments.linux_cycle_qualification.mock_broker import Handler
from experiments.linux_cycle_qualification.mock_upstream import MockUpstream
from experiments.linux_cycle_qualification.reconcile import (
    ReconciliationLedger, read_provider_journal)


class BrokerIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.upstream_db = self.root / "upstream.sqlite3"
        self.local_db = self.root / "attempts.sqlite3"
        self.upstream = MockUpstream(("127.0.0.1", 0), self.upstream_db)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        self.broker = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.broker.upstream_host = "127.0.0.1"
        self.broker.upstream_port = self.upstream.server_port
        self.broker.ledger_path = self.local_db
        self.broker_thread = threading.Thread(target=self.broker.serve_forever, daemon=True)
        self.broker_thread.start()
        self.addCleanup(self.stop_servers)

    def stop_servers(self) -> None:
        self.upstream.request_shutdown()
        self.broker.shutdown()
        self.broker.server_close()
        self.broker_thread.join(timeout=2)
        self.upstream_thread.join(timeout=2)
        self.upstream.server_close()

    def request(self, scenario: str) -> bytes:
        body = json.dumps({"scenario": scenario}).encode()
        request = Request(f"http://127.0.0.1:{self.broker.server_port}/responses",
                          data=body, headers={"Content-Type": "application/json"},
                          method="POST")
        with urlopen(request, timeout=5) as response:
            return response.read()

    def drive(self, scenario: str) -> tuple[bytes, dict]:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.request, scenario)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                rows = self.upstream.state()
                if rows:
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("upstream never durably accepted request")
            self.assertEqual(rows[0]["state"], "accepted")
            self.assertEqual(self.upstream.release(rows[0]["attempt_id"]), 200)
            result = future.result(timeout=5)
        journal = read_provider_journal(self.upstream_db)
        self.assertEqual(len(journal), 1)
        return result, journal[0]

    def check_completion(self, scenario: str, frame_count: int) -> None:
        body, record = self.drive(scenario)
        self.assertEqual(body.count(b"response.completed"), 2 * frame_count)
        self.assertEqual(record["state"], "completed")
        ledger = ReconciliationLedger(self.local_db)
        try:
            ledger.reconcile(record)
            summary = ledger.summary()
            self.assertTrue(summary["mock_accounting_complete"])
            self.assertEqual(summary["mock_observed_usage"]["input_tokens"], 120)
        finally:
            ledger.close()

    def test_normal_is_accounted_once(self) -> None:
        self.check_completion("normal", 1)

    def test_duplicate_is_accounted_once(self) -> None:
        self.check_completion("duplicate", 2)

    def test_conflict_rejected_without_double_counting(self) -> None:
        _body, record = self.drive("conflict")
        self.assertEqual(_body, b"")
        self.assertTrue((self.root / ("conflict-" + record["attempt_id"])).is_file())
        ledger = ReconciliationLedger(self.local_db)
        try:
            ledger.reconcile(record)
            self.assertEqual(ledger.summary()["mock_observed_usage"]["output_tokens"], 20)
        finally:
            ledger.close()

    def test_missing_completion_stays_unknown(self) -> None:
        _body, record = self.drive("no_completion")
        self.assertEqual(record["state"], "accepted")
        ledger = ReconciliationLedger(self.local_db)
        try:
            ledger.reconcile(record)
            self.assertFalse(ledger.summary()["mock_accounting_complete"])
            self.assertEqual(ledger.summary()["states"]["accepted_without_usage"], 1)
        finally:
            ledger.close()

    def test_client_disconnect_before_headers_keeps_accounting(self) -> None:
        body = b'{"scenario":"worker_killed"}'
        request = (b"POST /responses HTTP/1.1\r\nHost: broker\r\n"
                   b"Content-Type: application/json\r\nContent-Length: " +
                   str(len(body)).encode() + b"\r\n\r\n" + body)
        with socket.create_connection(("127.0.0.1", self.broker.server_port), timeout=2) as stream:
            stream.sendall(request)
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not self.upstream.state():
            time.sleep(0.01)
        rows = self.upstream.state()
        self.assertEqual(len(rows), 1)
        attempt = rows[0]["attempt_id"]
        self.assertEqual(self.upstream.release(attempt), 200)
        while time.monotonic() < deadline:
            with sqlite3.connect(self.local_db) as db:
                state = db.execute("SELECT state FROM attempts WHERE attempt_id=?",
                                   (attempt,)).fetchone()
            if state == ("completed",):
                break
            time.sleep(0.01)
        self.assertEqual(state, ("completed",))


if __name__ == "__main__":
    unittest.main()
