"""Secret-free, controller-released mock Responses upstream (standard library only).

POST /responses with X-Attempt-ID and {"scenario": SCENARIO} commits acceptance
before returning SSE headers, then waits for POST /release/<URL-encoded ID>.
GET /state exposes only IDs/states; GET /health and POST /shutdown are controls.
Usage is fixed at 120 input, 20 output, 80 cached input tokens. No model is called.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import sqlite3
import threading
from urllib.parse import unquote


SCENARIOS = frozenset({
    "normal", "worker_killed", "broker_killed", "timeout_checkpoint",
    "duplicate", "conflict", "no_completion",
})
INPUT_TOKENS, OUTPUT_TOKENS, CACHED_INPUT_TOKENS = 120, 20, 80


class MockUpstream(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, address: tuple[str, int], db: str | Path):
        self.db_path = str(db)
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.waiters: dict[str, threading.Event] = {}
        self.shutdown_thread: threading.Thread | None = None
        with self.database() as connection:
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode != "wal":
                raise RuntimeError("durable WAL database required")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS requests ("
                "attempt_id TEXT PRIMARY KEY, "
                "state TEXT NOT NULL CHECK(state IN ('accepted','completed')), "
                "response_id TEXT NOT NULL UNIQUE, "
                "input_tokens INTEGER, output_tokens INTEGER, cached_input_tokens INTEGER)"
            )
        super().__init__(address, Handler)

    @contextmanager
    def database(self):
        connection = sqlite3.connect(self.db_path, timeout=5)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def accept_attempt(self, attempt_id: str) -> tuple[str, threading.Event]:
        response_id = "mock_" + hashlib.sha256(attempt_id.encode("ascii")).hexdigest()
        with self.lock:
            if self.stopping.is_set():
                raise RuntimeError("stopping")
            with self.database() as connection:
                connection.execute(
                    "INSERT INTO requests VALUES (?, 'accepted', ?, NULL, NULL, NULL)",
                    (attempt_id, response_id),
                )
            # Registration and release share a lock; acceptance is already durable.
            waiter = threading.Event()
            self.waiters[attempt_id] = waiter
        return response_id, waiter

    def release(self, attempt_id: str) -> int:
        with self.lock:
            waiter = self.waiters.get(attempt_id)
            if waiter is not None:
                waiter.set()
                return 200
            with self.database() as connection:
                row = connection.execute(
                    "SELECT state FROM requests WHERE attempt_id=?", (attempt_id,)
                ).fetchone()
            # Accepted rows surviving a process crash are not silently replayed.
            return 404 if row is None else (200 if row[0] == "completed" else 409)

    def complete(self, attempt_id: str) -> None:
        with self.database() as connection:
            result = connection.execute(
                "UPDATE requests SET state='completed', input_tokens=?, output_tokens=?, "
                "cached_input_tokens=? WHERE attempt_id=? AND state='accepted'",
                (INPUT_TOKENS, OUTPUT_TOKENS, CACHED_INPUT_TOKENS, attempt_id),
            )
            if result.rowcount != 1:
                raise RuntimeError("completion state conflict")

    def state(self) -> list[dict]:
        with self.database() as connection:
            rows = connection.execute(
                "SELECT attempt_id,state,response_id FROM requests ORDER BY attempt_id"
            ).fetchall()
        return [dict(zip(("attempt_id", "state", "response_id"), row)) for row in rows]

    def request_shutdown(self) -> None:
        with self.lock:
            if self.shutdown_thread is not None:
                return
            self.stopping.set()
            for waiter in self.waiters.values():
                waiter.set()
            # BaseServer.shutdown must run outside the serve_forever thread.
            self.shutdown_thread = threading.Thread(target=self.shutdown, daemon=True)
            self.shutdown_thread.start()

    def server_close(self) -> None:
        self.stopping.set()
        with self.lock:
            for waiter in self.waiters.values():
                waiter.set()
        super().server_close()

    def handle_error(self, request, client_address) -> None:
        # Do not let socket errors print request information or tracebacks.
        pass


def completion_frame(response_id: str, output_tokens: int = OUTPUT_TOKENS) -> bytes:
    event = {
        "type": "response.completed",
        "response": {
            "id": response_id, "status": "completed",
            "usage": {
                "input_tokens": INPUT_TOKENS, "output_tokens": output_tokens,
                "total_tokens": INPUT_TOKENS + output_tokens,
                "input_tokens_details": {"cached_tokens": CACHED_INPUT_TOKENS},
            },
        },
    }
    return b"event: response.completed\ndata: " + json.dumps(
        event, separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n\n"


class Handler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_args) -> None:
        pass

    def reply(self, status: int, body: dict) -> None:
        raw = json.dumps(body, separators=(",", ":")).encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.reply(200, {"status": "ok"})
        elif self.path == "/state":
            self.reply(200, {"requests": self.server.state()})
        else:
            self.reply(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path == "/shutdown":
            try:
                self.reply(200, {"status": "stopping"})
            finally:
                self.server.request_shutdown()
            return
        if self.path.startswith("/release/"):
            try:
                attempt_id = unquote(self.path[len("/release/"):], errors="strict")
                status = self.server.release(attempt_id)
            except (UnicodeError, sqlite3.Error):
                self.reply(400, {"error": "release_failed"})
                return
            self.reply(status, {"status": "released"} if status == 200
                       else {"error": "not_found" if status == 404 else "not_active"})
            return
        if self.path != "/responses":
            self.reply(404, {"error": "not_found"})
            return
        try:
            ids = self.headers.get_all("X-Attempt-ID", [])
            lengths = self.headers.get_all("Content-Length", [])
            if len(ids) != 1 or len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                raise ValueError
            attempt_id = ids[0]
            if not 1 <= len(attempt_id) <= 256 or any(
                    not 33 <= ord(char) <= 126 for char in attempt_id):
                raise ValueError
            length = int(lengths[0])
            if not 0 < length <= 4096:
                raise ValueError
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError
            body = json.loads(raw)
            if not isinstance(body, dict) or set(body) != {"scenario"}:
                raise ValueError
            scenario = body["scenario"]
            if not isinstance(scenario, str) or scenario not in SCENARIOS:
                raise ValueError
        except (ValueError, UnicodeError, OSError):
            self.reply(400, {"error": "invalid_request"})
            return
        try:
            response_id, waiter = self.server.accept_attempt(attempt_id)
        except sqlite3.IntegrityError:
            self.reply(409, {"error": "duplicate_attempt"})
            return
        except (sqlite3.Error, RuntimeError):
            self.reply(503, {"error": "acceptance_unavailable"})
            return
        try:
            connected = True
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.flush()
            except OSError:
                connected = False
            waiter.wait()
            if self.server.stopping.is_set() or scenario == "no_completion":
                return
            self.server.complete(attempt_id)
            frame = completion_frame(response_id)
            payload = frame
            if scenario == "duplicate":
                payload += frame
            elif scenario == "conflict":
                payload += completion_frame(response_id, OUTPUT_TOKENS + 1)
            if connected:
                try:
                    self.wfile.write(payload)
                    self.wfile.flush()
                except OSError:
                    pass  # The independent journal survives a broker disconnect.
        finally:
            with self.server.lock:
                self.server.waiters.pop(attempt_id, None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    server = MockUpstream((args.host, args.port), args.db)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_args: server.request_shutdown())
    print(json.dumps({"status": "ready", "host": server.server_address[0],
                      "port": server.server_address[1]}), flush=True)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
