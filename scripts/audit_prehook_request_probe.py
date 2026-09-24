"""Reduce private next-request marker probes without emitting prompt or marker text."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


MARKER = re.compile(rb"HIDDEN_[0-9a-f]{24}")


def tokens(record):
    usage = record["completions"][0]
    fields = ("input_tokens", "output_tokens", "cached_tokens")
    if any(type(usage.get(key)) is not int or usage[key] < 0 for key in fields):
        raise ValueError("incomplete response usage")
    if usage["cached_tokens"] > usage["input_tokens"]:
        raise ValueError("cached input exceeds input")
    return {key: usage[key] for key in fields}


def inspect(root, prehook):
    root = Path(root)
    payload = (root / "work" / "payload.txt").read_bytes()
    matches = MARKER.findall(payload)
    if len(matches) != 1:
        raise ValueError("expected one private random marker")
    marker = matches[0]
    summary_bytes = (root / "artifacts" / "proxy-summary.json").read_bytes()
    summary = json.loads(summary_bytes)
    blocked = summary.get("proxy_blocked")
    if (summary.get("exit") != 0 or type(blocked) is not int or blocked < 0):
        raise ValueError("CLI completion or network observation incomplete")
    requests = summary.get("sink_requests")
    if not isinstance(requests, list) or len(requests) != 2:
        raise ValueError("expected exactly two model requests")
    for request in requests:
        if (request.get("upstream_status") != 200 or
                type(request.get("body_bytes")) is not int or
                request["body_bytes"] < 0 or
                request.get("client_disconnected") is not False or
                request.get("upstream_error") is not None or
                request.get("done") is not True or
                not isinstance(request.get("completions"), list) or
                len(request["completions"]) != 1 or
                type(request.get("request_has_probe_marker")) is not bool):
            raise ValueError("incomplete request observation")
        tokens(request)
    flags = [item["request_has_probe_marker"] for item in requests]
    if flags != ([False, False] if prehook else [False, True]):
        raise ValueError("next-request marker control failed")
    journal = summary.get("journal") or {}
    if (journal.get("attempts") != 2 or
            (journal.get("states") or {}).get("completed") != 2 or
            not journal.get("all_attempts_have_observed_usage") or
            journal.get("provider_billing_complete") is not False):
        raise ValueError("proxy journal does not cover both requests")
    observed = journal.get("observed_completed_usage") or {}
    expected = {"input_tokens": sum(tokens(item)["input_tokens"] for item in requests),
                "output_tokens": sum(tokens(item)["output_tokens"] for item in requests),
                "cached_input_tokens": sum(tokens(item)["cached_tokens"] for item in requests)}
    if observed != expected:
        raise ValueError("journal usage differs from response observations")
    trace_bytes = (root / "artifacts" / "trace.jsonl").read_bytes()
    events = [json.loads(line) for line in trace_bytes.splitlines() if line.strip()]
    if sum(e.get("type") == "turn.completed" for e in events) != 1:
        raise ValueError("expected one completed CLI turn")
    items = [e.get("item", {}) for e in events if e.get("type") == "item.completed"]
    commands = [item for item in items if item.get("type") == "command_execution"]
    finals = [item.get("text", "") for item in items if item.get("type") == "agent_message"]
    if len(commands) != 1 or not finals:
        raise ValueError("missing single command or final answer")
    output = str(commands[0].get("aggregated_output", "")).encode()
    if (marker in output) == prehook:
        raise ValueError("tool result does not match marker control")
    if (marker.decode() in finals[-1]) == prehook:
        raise ValueError("model answer does not match marker control")
    capture = root / "artifacts" / "captured.bin"
    if prehook and (not capture.is_file() or capture.read_bytes() != payload or
                    finals[-1].strip() != "UNKNOWN"):
        raise ValueError("prehook artifact or answer mismatch")
    if not prehook and capture.exists():
        raise ValueError("direct arm unexpectedly captured an artifact")
    return {"request_marker_flags": flags,
            "blocked_non_model_connections": blocked,
            "command_output_bytes": len(output),
            "artifact_contains_exact_original": prehook,
            "next_request_body_bytes": requests[1]["body_bytes"],
            "next_request_usage": tokens(requests[1]),
            "proxy_summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
            "cli_trace_sha256": hashlib.sha256(trace_bytes).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for label in ("direct", "prehook", "direct-code", "prehook-code"):
        parser.add_argument("--" + label, required=True, type=Path)
    args = parser.parse_args()
    report = {"schema": "solcodex.next-request-boundary-development.v1",
              "codex_cli": "0.155.0-alpha.16.3",
              "installed_solcodex_plugin": False,
              "nested_javascript_await_verified": False,
              "all_network_attempts_accounted": False,
              "private_request_bodies_retained": False,
              "provider_billing_complete": False,
              "runs": {label: inspect(getattr(args, label.replace("-", "_")),
                                      label.startswith("prehook"))
                       for label in ("direct", "prehook", "direct-code", "prehook-code")}}
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
