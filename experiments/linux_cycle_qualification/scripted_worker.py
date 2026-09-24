"""Deterministic worker for lifecycle tests; it does not run a model."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError


PARENT_SHA256 = "cba812ea43e3005bc1937a88d5696bdcbb6ef011c4e6ea11589651cd7ee8f0b2"
GOLD_SHA256 = "9896aa3d83c745a7e91c32256cfe1c4cdd93a89c6a14c0268ca8ecb267cdc26f"
OLD = '    if isinstance(value, bool) or not isinstance(value, int):\n        raise TypeError("duration must be an integer number of seconds")\n'
NEW = ('    if isinstance(value, bool) or not isinstance(value, int):\n'
       '        if isinstance(value, str):\n'
       '            units = {"s": 1, "m": 60, "h": 3600}\n'
       '            if len(value) > 1 and value[-1] in units and value[:-1].isascii() and value[:-1].isdecimal():\n'
       '                return int(value[:-1]) * units[value[-1]]\n'
       '            raise ValueError("invalid duration string")\n'
       '        raise TypeError("duration must be an integer number of seconds")\n')


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def direct_upstream_denied() -> bool:
    try:
        with socket.create_connection((os.environ["UPSTREAM_GATEWAY"],
                                       int(os.environ["UPSTREAM_PORT"])), timeout=1):
            return False
    except OSError:
        return True


def patch_source(work: Path) -> None:
    path = work / "duration.py"
    source = path.read_bytes()
    if digest(source) != PARENT_SHA256 or source.count(OLD.encode()) != 1:
        raise ValueError("unexpected parent source")
    result = source.replace(OLD.encode(), NEW.encode())
    if digest(result) != GOLD_SHA256:
        raise ValueError("unexpected scripted patch")
    path.write_bytes(result)


def main() -> int:
    scenario = sys.argv[1]
    work = Path("/work")
    forbidden = [os.environ[key] for key in
                 ("GOLD_CANARY", "SIBLING_CANARY", "HOST_CANARY", "FAKE_AUTH_CANARY")]
    checks = {"workspace_readable": (work / "duration.py").is_file(),
              "host_files_denied": all(not Path(path).exists() for path in forbidden),
              "docker_socket_absent": not Path("/var/run/docker.sock").exists(),
              "direct_upstream_denied": direct_upstream_denied(),
              "nonroot_uid": os.geteuid() != 0,
              "ci_tokens_absent": not any(key in os.environ for key in
                                          ("GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN",
                                           "ACTIONS_ID_TOKEN_REQUEST_TOKEN"))}
    print(json.dumps({"phase": "preflight", "checks": checks}, sort_keys=True), flush=True)
    if not all(checks.values()):
        return 2
    if scenario == "timeout_checkpoint":
        patch_source(work)
        (work / "forged-evaluation.json").write_text('{"accepted":false}\n')
        marker = work / ".checkpoint_ready"
        with marker.open("x") as stream:
            stream.write(GOLD_SHA256 + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    upstream_scenario = ("normal" if scenario in
                         {"retry", "reconciliation_unavailable", "ledger_write_failure"}
                         else scenario)
    payload = json.dumps({"scenario": upstream_scenario}).encode()
    request = Request("http://broker:8000/responses", data=payload,
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=60) as response:
            body = response.read()
            status = response.status
    except HTTPError as error:
        print(json.dumps({"phase": "request_error", "type": "HTTPError",
                          "http_status": error.code}), flush=True)
        return 3
    except Exception as error:
        print(json.dumps({"phase": "request_error", "type": type(error).__name__}), flush=True)
        return 3
    completed = (b'"type": "response.completed"' in body or
                 b'"type":"response.completed"' in body)
    if scenario == "normal" and status == 200 and completed:
        patch_source(work)
    print(json.dumps({"phase": "finished", "http_status": status,
                      "completion_seen": completed, "source_sha256": digest((work / "duration.py").read_bytes())},
                     sort_keys=True), flush=True)
    return 0 if status == 200 and completed else 4


if __name__ == "__main__":
    raise SystemExit(main())
