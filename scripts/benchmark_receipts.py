#!/usr/bin/env python3
"""Run deterministic SoL Codex receipt mechanism checks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "plugins" / "sol-codex" / "scripts" / "sol_hook.py"
ARTIFACT_RE = re.compile(r"^Artifact: (.+)$", re.MULTILINE)


def event(model: str, output: str, exit_code: int | None) -> Dict[str, Any]:
    response: Dict[str, Any] = {"output": output}
    if exit_code is not None:
        response["exit_code"] = exit_code
    return {
        "session_id": "deterministic-benchmark",
        "cwd": "/tmp/sol-codex-benchmark",
        "hook_event_name": "PostToolUse",
        "model": model,
        "tool_name": "Bash",
        "tool_input": {"command": "deterministic-benchmark"},
        "tool_response": response,
    }


def run_hook(data: Path, payload: Dict[str, Any]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PLUGIN_DATA"] = str(data)
    environment.pop("SOL_CODEX_PACK_THRESHOLD_BYTES", None)
    environment.pop("SOL_CODEX_ASTRA_PACK_THRESHOLD_BYTES", None)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )


def packed_case(
    data: Path, name: str, model: str, output: str, redacted_secret: str = "",
    plain_string: bool = False,
) -> Dict[str, Any]:
    payload = event(model, output, 0)
    if plain_string:
        payload["tool_response"] = output
    result = run_hook(data, payload)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"{name}: expected a packed receipt")
    receipt = json.loads(result.stdout)["reason"]
    if plain_string and "Status: exit_code=unknown" not in receipt:
        raise RuntimeError(f"{name}: plain-string status was not marked unknown")
    artifact_match = ARTIFACT_RE.search(receipt)
    if artifact_match is None:
        raise RuntimeError(f"{name}: receipt omitted artifact path")
    artifact = Path(artifact_match.group(1))
    if artifact.read_text(encoding="utf-8") != output:
        raise RuntimeError(f"{name}: archived bytes differ from source")
    digest = hashlib.sha256(output.encode("utf-8")).hexdigest()
    if f"sha256={digest}" not in receipt:
        raise RuntimeError(f"{name}: receipt hash mismatch")
    if redacted_secret and (redacted_secret in receipt or "[REDACTED]" not in receipt):
        raise RuntimeError(f"{name}: supported credential was not redacted")
    source_bytes = len(output.encode("utf-8"))
    receipt_bytes = len(receipt.encode("utf-8"))
    if receipt_bytes >= source_bytes:
        raise RuntimeError(f"{name}: receipt failed net-savings guard")
    case: Dict[str, Any] = {
        "name": name,
        "model": model,
        "source_bytes": source_bytes,
        "receipt_bytes": receipt_bytes,
        "saved_bytes": source_bytes - receipt_bytes,
        "packed": True,
    }
    if redacted_secret:
        case["redacted"] = True
    if plain_string:
        case["status_unknown"] = True
    return case


def benchmark() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as temporary:
        data = Path(temporary) / "plugin-data"
        secret = "sk-" + ("b" * 32)
        cases: List[Dict[str, Any]] = [
            packed_case(data, "astra-5000", "gpt-6-astra", "a" * 5_000),
            packed_case(data, "default-8000", "gpt-6-luna", "l" * 8_000),
            packed_case(data, "default-13000", "gpt-5.6-sol", "d" * 13_000),
            packed_case(
                data, "plain-string-unknown", "gpt-6-sol", "s" * 13_000,
                plain_string=True,
            ),
        ]
        unknown_output = "u" * 13_000
        unknown = run_hook(data, event("gpt-5.6-sol", unknown_output, None))
        if unknown.returncode != 0 or unknown.stdout:
            raise RuntimeError("unknown-exit-status: original output must remain unchanged")
        cases.append(
            {
                "name": "unknown-exit-status",
                "model": "gpt-5.6-sol",
                "source_bytes": len(unknown_output.encode("utf-8")),
                "receipt_bytes": 0,
                "saved_bytes": 0,
                "packed": False,
            }
        )
        credential_output = f"ERROR api_key={secret}\n" + ("r" * 5_000)
        cases.append(
            packed_case(
                data,
                "credential-redaction",
                "gpt-6-astra",
                credential_output,
                redacted_secret=secret,
            )
        )
        return {"schema_version": 1, "cases": cases}


def main() -> int:
    try:
        print(json.dumps(benchmark(), sort_keys=True, separators=(",", ":")))
    except Exception as error:
        print(f"Benchmark failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
