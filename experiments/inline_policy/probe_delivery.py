#!/usr/bin/env python3
"""Verify packaged policy delivery through Codex with a synthetic local provider."""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[2]
NEEDLES = ("Probe required tools and dependencies together", "30-60 second waits",
           "preserve complete data", "inspect their exit status")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def response(index: int) -> bytes:
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
             "input_tokens_details": {"cached_tokens": 0}}
    identifier = f"resp_inline_policy_probe_{index}"
    events = []
    if index == 1:
        item = {"id": "ctc_inline_policy_probe", "type": "custom_tool_call", "status": "completed",
                "call_id": "call_inline_policy_probe", "namespace": "functions", "name": "exec",
                "input": 'const r=await tools.exec_command({cmd:"printf POLICY_PROBE_OK"}); text(r.output);'}
        events.extend([
            {"type": "response.output_item.added", "response_id": identifier,
             "output_index": 0, "item": dict(item, status="in_progress", input="")},
            {"type": "response.output_item.done", "response_id": identifier,
             "output_index": 0, "item": item}])
    else:
        item = {"id": "msg_inline_policy_probe", "type": "message", "role": "assistant",
                "status": "completed", "content": [{"type": "output_text", "text": "DONE"}]}
    events.append({"type": "response.completed", "response": {
        "id": identifier, "status": "completed", "output": [item], "usage": usage}})
    return b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events) + b"data: [DONE]\n\n"


def run(codex: str, source: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="sol-inline-delivery-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        market, home, work = root / "market", root / "home", root / "work"
        (market / ".agents/plugins").mkdir(parents=True)
        home.mkdir()
        work.mkdir()
        shutil.copytree(source / "plugins/sol-codex", market / "plugins/sol-codex",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(source / ".agents/plugins/marketplace.json", market / ".agents/plugins/marketplace.json")
        environment = os.environ.copy()
        environment.update({"CODEX_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"})
        for argv in ([codex, "plugin", "marketplace", "add", str(market), "--json"],
                     [codex, "plugin", "add", "sol-codex@sol-codex", "--json"]):
            installed = subprocess.run(argv, env=environment, cwd=work,
                                       capture_output=True, text=True, timeout=60)
            if installed.returncode:
                raise RuntimeError("isolated plugin install failed: " + installed.stderr[-1500:])
        rows = []
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                body = json.loads(raw)
                visible = json.dumps(body.get("input", []), ensure_ascii=False)
                with lock:
                    index = len(rows) + 1
                    rows.append({"body_sha256": digest(raw), "body_bytes": len(raw),
                                 "policy_visible": all(needle in visible for needle in NEEDLES),
                                 "model": body.get("model")})
                if index > 2:
                    self.send_error(429)
                    return
                payload = response(index)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = home / "config.toml"
        installed_config = config.read_text(encoding="utf-8") if config.exists() else ""
        config.write_text(
            installed_config + '\n[model_providers.inline_probe]\nname="Synthetic local provider"\n'
            f'base_url="http://127.0.0.1:{server.server_address[1]}/backend-api/codex"\n'
            'requires_openai_auth=false\nsupports_websockets=false\n'
            'request_max_retries=0\nstream_max_retries=0\n', encoding="utf-8")
        argv = [codex, "-a", "never", "exec", "--strict-config", "--json",
                "--skip-git-repo-check", "-C", str(work), "-s", "workspace-write",
                "-m", "gpt-6-luna", "--enable", "code_mode", "--enable", "hooks",
                "--enable", "plugins", "--dangerously-bypass-hook-trust",
                "-c", "model_provider=inline_probe", "-c", "model_reasoning_effort=low",
                "-c", "suppress_unstable_features_warning=true", "Execute the local probe and finish."]
        try:
            result = subprocess.run(argv, env=environment, cwd=work, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=90)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        completed = [event for event in events if event.get("type") == "turn.completed"]
        commands = [event["item"] for event in events if event.get("type") == "item.completed"
                    and event.get("item", {}).get("type") == "command_execution"]
        passed = (result.returncode == 0 and len(rows) == 2 and
                  all(row["policy_visible"] for row in rows) and len(completed) == 1 and
                  len(commands) == 1 and commands[0].get("exit_code") == 0 and
                  "POLICY_PROBE_OK" in commands[0].get("aggregated_output", ""))
        if not passed:
            raise RuntimeError("delivery probe failed: " + json.dumps({
                "exit": result.returncode, "requests": rows, "completions": len(completed),
                "commands": len(commands), "stderr_tail": result.stderr[-1500:]}))
        version = subprocess.run([codex, "--version"], capture_output=True, text=True,
                                 check=True, timeout=15).stdout.strip()
        return {"schema": "solcodex.inline-policy-delivery.v1", "passed": True,
                "real_provider_requests": 0, "synthetic_requests": len(rows),
                "cli_version": version, "cli_sha256": digest(Path(shutil.which(codex) or codex).read_bytes()),
                "plugin_hook_sha256": digest((source / "plugins/sol-codex/scripts/sol_hook.py").read_bytes()),
                "bootstrap_sha256": digest((source / "plugins/sol-codex/scripts/sol_bootstrap.py").read_bytes()),
                "script_sha256": digest(Path(__file__).read_bytes()), "requests": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; preserve previous evidence")
    os.umask(0o077)
    record = run(args.codex, args.source.resolve(strict=True))
    args.output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "real_provider_requests": 0,
                      "cli_version": record["cli_version"], "requests": record["synthetic_requests"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
