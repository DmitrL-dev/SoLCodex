"""Disposable broker for fault-injected Linux lifecycle checks; no credentials."""
from __future__ import annotations

import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path

from scripts.usage_attempt_ledger import AttemptLedger


SCENARIOS = frozenset({"normal", "worker_killed", "broker_killed",
                       "timeout_checkpoint", "duplicate", "conflict", "no_completion"})


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_POST(self) -> None:
        if self.path != "/responses":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise ValueError("invalid body length")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or set(body) != {"scenario"} or body["scenario"] not in SCENARIOS:
                raise ValueError("invalid scenario")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.send_error(400)
            return
        scenario = body["scenario"]
        ledger = None
        try:
            ledger = AttemptLedger(self.server.ledger_path)
            attempt_id = ledger.start("linux-cycle", scenario)
        except Exception:
            if ledger is not None:
                ledger.close()
            self.send_error(503)
            return
        connection = http.client.HTTPConnection(self.server.upstream_host,
                                                self.server.upstream_port, timeout=60)
        completed = False
        disconnected = False
        try:
            connection.request("POST", "/responses", body=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json",
                                        "X-Attempt-ID": attempt_id})
            upstream = connection.getresponse()
            if upstream.status != 200:
                raise RuntimeError("mock upstream did not accept request")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError, OSError):
                disconnected = True
            pending = b""
            while chunk := upstream.read1(65536):
                pending += chunk
                while b"\n\n" in pending:
                    frame, pending = pending.split(b"\n\n", 1)
                    for line in frame.splitlines():
                        if not line.startswith(b"data: "):
                            continue
                        event = json.loads(line[6:])
                        if event.get("type") == "response.completed":
                            response = event["response"]
                            usage = response["usage"]
                            try:
                                ledger.complete(attempt_id, response["id"],
                                                usage["input_tokens"], usage["output_tokens"],
                                                usage["input_tokens_details"]["cached_tokens"])
                            except ValueError:
                                (self.server.ledger_path.parent /
                                 ("conflict-" + attempt_id)).write_text("rejected\n")
                                raise
                            completed = True
                if not disconnected:
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        disconnected = True
        except (OSError, RuntimeError, KeyError, TypeError, ValueError):
            pass
        finally:
            if not completed:
                try:
                    ledger.unknown(attempt_id, "no_final_usage")
                except ValueError:
                    pass
            ledger.close()
            connection.close()
            self.close_connection = True

    def log_message(self, *_args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--upstream-host", required=True)
    parser.add_argument("--upstream-port", type=int, required=True)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.upstream_host = args.upstream_host
    server.upstream_port = args.upstream_port
    server.ledger_path = args.db
    server.serve_forever()


if __name__ == "__main__":
    main()
