"""Focused wire, persistence, and shutdown tests; no model or Docker needed."""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from experiments.linux_cycle_qualification.mock_upstream import MockUpstream, SCENARIOS


class MockUpstreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "upstream.sqlite3"
        self.server = MockUpstream(("127.0.0.1", 0), self.db)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.connections = []

    def tearDown(self):
        self.server.request_shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        for connection in self.connections:
            connection.close()
        self.temp.cleanup()
        self.assertFalse(self.thread.is_alive())

    def connection(self, address=None):
        connection = http.client.HTTPConnection(
            *(address or self.server.server_address), timeout=3)
        self.connections.append(connection)
        return connection

    def request(self, method, path, body=None, headers=None, address=None):
        connection = self.connection(address)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    def start(self, attempt, scenario, address=None):
        connection = self.connection(address)
        connection.request("POST", "/responses", json.dumps({"scenario": scenario}),
                           {"X-Attempt-ID": attempt, "Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        return response

    def row(self, attempt):
        connection = sqlite3.connect(self.db)
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            return connection.execute(
                "SELECT state,response_id,input_tokens,output_tokens,cached_input_tokens "
                "FROM requests WHERE attempt_id=?", (attempt,)).fetchone()
        finally:
            connection.close()

    @staticmethod
    def frames(raw):
        return [json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b"data: ")]

    def test_acceptance_is_committed_before_headers_and_release(self):
        response = self.start("normal-1", "normal")
        row = self.row("normal-1")
        self.assertEqual(row[0], "accepted")
        self.assertEqual(row[2:], (None, None, None))
        status, state = self.request("GET", "/state")
        self.assertEqual(status, 200)
        self.assertEqual(state, {"requests": [{"attempt_id": "normal-1",
                         "response_id": row[1], "state": "accepted"}]})
        self.assertEqual(self.request("POST", "/release/normal-1")[0], 200)
        frames = self.frames(response.read())
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["response"]["id"], row[1])
        self.assertEqual(frames[0]["response"]["usage"], {
            "input_tokens": 120, "output_tokens": 20, "total_tokens": 140,
            "input_tokens_details": {"cached_tokens": 80}})
        self.assertEqual(self.row("normal-1"), ("completed", row[1], 120, 20, 80))

    def test_duplicate_frames_are_identical_and_journal_counts_once(self):
        response = self.start("dup", "duplicate")
        self.request("POST", "/release/dup")
        raw_frames = response.read().split(b"\n\n")
        self.assertEqual(len(raw_frames), 3)
        self.assertEqual(raw_frames[0], raw_frames[1])
        self.assertEqual(raw_frames[2], b"")
        self.assertEqual(self.row("dup")[2:], (120, 20, 80))
        self.assertEqual(len(self.request("GET", "/state")[1]["requests"]), 1)

    def test_conflict_changes_second_usage_not_journal_or_response_id(self):
        response = self.start("conflict", "conflict")
        self.request("POST", "/release/conflict")
        first, second = self.frames(response.read())
        self.assertEqual(first["response"]["id"], second["response"]["id"])
        self.assertEqual(first["response"]["usage"]["output_tokens"], 20)
        self.assertEqual(second["response"]["usage"]["output_tokens"], 21)
        self.assertEqual(second["response"]["usage"]["total_tokens"], 141)
        self.assertEqual(self.row("conflict")[2:], (120, 20, 80))

    def test_no_completion_closes_without_inventing_usage(self):
        response = self.start("missing", "no_completion")
        self.request("POST", "/release/missing")
        self.assertEqual(response.read(), b"")
        self.assertEqual(self.row("missing")[0], "accepted")
        self.assertEqual(self.row("missing")[2:], (None, None, None))

    def test_broker_disconnect_does_not_cancel_upstream_accounting(self):
        response = self.start("disconnected", "broker_killed")
        response.close()
        self.assertEqual(self.request("POST", "/release/disconnected")[0], 200)
        deadline = time.monotonic() + 3
        while self.row("disconnected")[0] != "completed" and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(self.row("disconnected")[0], "completed")
        self.assertEqual(self.row("disconnected")[2:], (120, 20, 80))

    def test_release_is_per_attempt_and_ids_are_unique(self):
        first = self.start("first", "normal")
        second = self.start("second", "normal")
        self.assertEqual(self.request("POST", "/responses", '{"scenario":"normal"}',
                                     {"X-Attempt-ID": "first"})[0], 409)
        self.assertEqual(self.request("POST", "/release/absent")[0], 404)
        self.request("POST", "/release/second")
        self.assertEqual(len(self.frames(second.read())), 1)
        self.assertEqual(self.row("first")[0], "accepted")
        self.request("POST", "/release/first")
        self.assertEqual(len(self.frames(first.read())), 1)

    def test_external_fault_scenarios_use_the_normal_completion_contract(self):
        for scenario in SCENARIOS - {"normal", "duplicate", "conflict", "no_completion"}:
            with self.subTest(scenario=scenario):
                response = self.start(scenario, scenario)
                self.request("POST", "/release/" + scenario)
                self.assertEqual(len(self.frames(response.read())), 1)
                self.assertEqual(self.row(scenario)[0], "completed")

    def test_invalid_requests_do_not_create_acceptance(self):
        for body, headers in [('{"scenario":"normal"}', {}),
                              ('{"scenario":"other"}', {"X-Attempt-ID": "bad"}),
                              ('{"scenario":[]}', {"X-Attempt-ID": "bad"}),
                              ('{"scenario":"normal","prompt":"unused"}',
                               {"X-Attempt-ID": "bad"})]:
            with self.subTest(body=body):
                self.assertEqual(self.request("POST", "/responses", body, headers)[0], 400)
        self.assertEqual(self.request("GET", "/state")[1], {"requests": []})

    def test_shutdown_unblocks_pending_response_without_completing_it(self):
        self.assertEqual(self.request("GET", "/health"), (200, {"status": "ok"}))
        response = self.start("pending", "normal")
        self.assertEqual(self.request("POST", "/shutdown")[0], 200)
        self.assertEqual(response.read(), b"")
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.row("pending")[0], "accepted")

    def test_acceptance_survives_process_kill_and_rejects_reuse(self):
        db = Path(self.temp.name) / "crashed.sqlite3"
        script = Path(__file__).with_name("mock_upstream.py")
        process = subprocess.Popen(
            [sys.executable, "-B", str(script), "--port", "0", "--db", str(db)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready = json.loads(process.stdout.readline())
            address = (ready["host"], ready["port"])
            response = self.start("durable", "normal", address)
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual((stdout, stderr), ("", ""))
            response.close()
            restarted = MockUpstream(("127.0.0.1", 0), db)
            try:
                self.assertEqual(restarted.state()[0]["state"], "accepted")
                self.assertEqual(restarted.release("durable"), 409)
                with self.assertRaises(sqlite3.IntegrityError):
                    restarted.accept_attempt("durable")
            finally:
                restarted.server_close()
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
