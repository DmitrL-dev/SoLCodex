#!/usr/bin/env python3
"""Aggregate private Codex OTLP JSON logs without publishing prompts or tool output.

This is an observation audit, not a provider-billing or completeness oracle.
Usage-bearing OTel records can be missing, duplicated, or include startup work
excluded from CLI turn usage. Keep the raw exports private for reconciliation.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any, Iterable

from ab_trace import parse_trace


TOKEN_ATTRIBUTES = {
    "input_tokens": "input_token_count",
    "cached_input_tokens": "cached_token_count",
    "cache_write_input_tokens": "cache_write_token_count",
    "output_tokens": "output_token_count",
    "reasoning_output_tokens": "reasoning_token_count",
}
IDENTITY_KEYS = {
    "request.id", "request_id", "response.id", "response_id", "x-oai-request-id",
}


def attribute_map(record: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for entry in record.get("attributes", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
            continue
        value = entry.get("value")
        if not isinstance(value, dict):
            continue
        for kind in ("stringValue", "intValue", "boolValue"):
            if kind in value:
                result[entry["key"]] = value[kind]
                break
    return result


def records_in(batch: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(batch, dict) or not isinstance(batch.get("resourceLogs"), list):
        raise ValueError("expected OTLP JSON resourceLogs")
    for resource in batch["resourceLogs"]:
        if not isinstance(resource, dict):
            raise ValueError("invalid OTLP resource log")
        for scope in resource.get("scopeLogs", []):
            if not isinstance(scope, dict):
                raise ValueError("invalid OTLP scope log")
            for record in scope.get("logRecords", []):
                if not isinstance(record, dict):
                    raise ValueError("invalid OTLP log record")
                yield record


def token_value(attributes: dict[str, Any], key: str) -> int | None:
    value = attributes.get(key)
    if value is None:
        return None
    if type(value) is int:
        number = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        number = int(value)
    else:
        raise ValueError(f"invalid {key}")
    if number < 0:
        raise ValueError(f"invalid {key}")
    return number


def sum_known(records: list[dict[str, int | None]], field: str) -> int | None:
    if not records or any(record[field] is None for record in records):
        return None
    return sum(record[field] for record in records if record[field] is not None)


def audit(paths: list[Path], cli_trace: Path | None = None,
          process_exit_code: int | None = None) -> dict[str, Any]:
    if not paths:
        raise ValueError("at least one OTLP batch is required")
    requests = Counter()
    kinds = Counter()
    completions: list[dict[str, int | None]] = []
    no_usage = 0
    websocket_requests = 0
    prewarm_phases = 0
    identity_keys_present = set()
    byte_count = 0
    for path in paths:
        raw = path.read_bytes()
        byte_count += len(raw)
        for record in records_in(json.loads(raw)):
            attrs = attribute_map(record)
            event = attrs.get("event.name")
            if event in {"codex.api_request", "codex.sse_event", "codex.websocket_request"}:
                identity_keys_present.update(IDENTITY_KEYS.intersection(attrs))
            if event == "codex.api_request":
                endpoint = attrs.get("endpoint")
                requests[endpoint if endpoint in {"/models", "/responses"} else "other"] += 1
            elif event == "codex.websocket_request":
                websocket_requests += 1
            elif event == "codex.startup_phase":
                prewarm_phases += attrs.get("startup.phase") == "startup_prewarm_websocket_warmup"
            elif event == "codex.sse_event":
                kind = attrs.get("event.kind")
                if not isinstance(kind, str):
                    continue
                kinds[kind] += 1
                if kind != "response.completed":
                    continue
                values = {field: token_value(attrs, key) for field, key in TOKEN_ATTRIBUTES.items()}
                if values["input_tokens"] is None or values["output_tokens"] is None:
                    no_usage += 1
                    continue
                cached = values["cached_input_tokens"]
                writes = values["cache_write_input_tokens"]
                reasoning = values["reasoning_output_tokens"]
                if cached is not None and writes is not None and cached + writes > values["input_tokens"]:
                    raise ValueError("cache categories exceed input tokens")
                if reasoning is not None and reasoning > values["output_tokens"]:
                    raise ValueError("reasoning tokens exceed output tokens")
                completions.append(values)
    totals = {field: sum_known(completions, field) for field in TOKEN_ATTRIBUTES}
    totals["total_input_output_tokens"] = (
        totals["input_tokens"] + totals["output_tokens"]
        if totals["input_tokens"] is not None and totals["output_tokens"] is not None else None
    )
    cli = None
    difference = None
    if cli_trace is not None:
        parsed = parse_trace(cli_trace)
        cli = {"completed_turns": parsed["completed_turns"],
               "failed_turns": parsed["failed_turns"], "error_events": parsed["error_events"],
               "tokens": parsed["tokens"]}
        difference = {field: totals[field] - cli["tokens"][field]
                      if totals[field] is not None and cli["tokens"][field] is not None else None
                      for field in totals}
    model_request_events = requests["/responses"] + websocket_requests
    return {
        "schema": "solcodex.otel-observation-audit.v1",
        "otlp_batches": len(paths),
        "otlp_bytes": byte_count,
        "api_requests_by_endpoint": dict(sorted(requests.items())),
        "websocket_request_events": websocket_requests,
        "model_request_events": model_request_events,
        "startup_prewarm_websocket_warmup_phases": prewarm_phases,
        "sse_events_by_kind": dict(sorted(kinds.items())),
        "response_completions_with_usage": len(completions),
        "response_completions_without_usage": no_usage,
        "otel_reported_tokens": totals,
        "cli": cli,
        "otel_minus_cli": difference,
        "process_exit_code": process_exit_code,
        "request_identity_keys_present": sorted(identity_keys_present),
        "request_count_minus_usage_completion_count": model_request_events - len(completions),
        "end_to_end_usage_complete": False,
        "limitations": [
            "OTel is emitted by the CLI, not an independent provider ledger.",
            "OTel may be buffered and lost on abrupt process termination.",
            "Response usage cannot be joined or deduplicated by count alone.",
            "Startup prewarm usage may be outside CLI turn usage; billing is unknown.",
            "Missing request usage is unknown, not zero; this audit does not estimate billed cost.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batches", nargs="+", type=Path, help="private OTLP JSON batch files")
    parser.add_argument("--cli-trace", type=Path)
    parser.add_argument("--process-exit-code", type=int)
    options = parser.parse_args()
    print(json.dumps(audit(options.batches, options.cli_trace, options.process_exit_code),
                     sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
