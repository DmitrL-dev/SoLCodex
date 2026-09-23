#!/usr/bin/env python3
"""Summarize Codex exec JSONL A/B traces without copying prompts or tool output."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


TOKEN_FIELDS = (
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens",
)
TOOL_TYPES = {"command_execution", "file_change", "mcp_tool_call", "web_search"}
ARTIFACT_RE = re.compile(r"obs_[a-f0-9]{24}\.txt")
SHA256_RE = re.compile(r"[a-f0-9]{64}\Z")


def sum_field(records: Iterable[dict[str, Any]], field: str) -> int | None:
    values = [record.get(field) for record in records]
    if not values or any(type(value) is not int or value < 0 for value in values):
        return None
    return sum(values)


def parse_trace(path: Path) -> dict[str, Any]:
    usages: list[dict[str, Any]] = []
    command_counts: Counter[str] = Counter()
    item_types: Counter[str] = Counter()
    seen_items: set[str] = set()
    turn_failures = 0
    event_errors = 0
    nonzero_tool_exits = 0
    failed_tool_items = 0
    artifact_reads = 0
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSONL at line {number}") from error
            if not isinstance(event, dict):
                raise ValueError(f"expected event object at line {number}")
            kind = event.get("type")
            if kind == "turn.completed":
                usage = event.get("usage")
                usages.append(usage if isinstance(usage, dict) else {})
            elif kind == "turn.failed":
                turn_failures += 1
            elif kind == "error":
                event_errors += 1
            elif kind == "item.completed":
                item = event.get("item")
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if (not isinstance(item_type, str) or item_type not in TOOL_TYPES) and "command" not in item:
                    continue
                identifier = item.get("id")
                if isinstance(identifier, str):
                    if identifier in seen_items:
                        continue
                    seen_items.add(identifier)
                item_types[str(item_type)] += 1
                if item.get("status") == "failed":
                    failed_tool_items += 1
                if type(item.get("exit_code")) is int and item["exit_code"] != 0:
                    nonzero_tool_exits += 1
                command = item.get("command")
                if isinstance(command, str):
                    command_counts[command] += 1
                    if ARTIFACT_RE.search(command):
                        artifact_reads += 1
    tokens = {field: sum_field(usages, field) for field in TOKEN_FIELDS}
    input_tokens = tokens["input_tokens"]
    cached = tokens["cached_input_tokens"]
    writes = tokens["cache_write_input_tokens"]
    tokens["uncached_input_tokens"] = (
        input_tokens - cached - writes
        if input_tokens is not None and cached is not None and writes is not None
        and cached + writes <= input_tokens else None
    )
    tokens["total_input_output_tokens"] = (
        input_tokens + tokens["output_tokens"]
        if input_tokens is not None and tokens["output_tokens"] is not None else None
    )
    return {
        "completed_turns": len(usages),
        "failed_turns": turn_failures,
        "error_events": event_errors,
        "tokens": tokens,
        "tool_calls": sum(item_types.values()),
        "tool_calls_by_type": dict(sorted(item_types.items())),
        "nonzero_tool_exits": nonzero_tool_exits,
        "failed_tool_items": failed_tool_items,
        "repeated_exact_commands": sum(count - 1 for count in command_counts.values()),
        "artifact_read_commands": artifact_reads,
    }


def checked_number(value: Any, name: str) -> float:
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return float(value)


def checked_exit(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer or null")
    return value


def arm_result(arm: dict[str, Any], base: Path, name: str) -> dict[str, Any]:
    trace_name = arm.get("trace")
    if not isinstance(trace_name, str) or not trace_name:
        raise ValueError(f"{name}.trace must be a path")
    trace = Path(trace_name)
    if not trace.is_absolute():
        trace = base / trace
    result = parse_trace(trace)
    result["elapsed_seconds"] = checked_number(arm.get("elapsed_seconds"), f"{name}.elapsed_seconds")
    result["process_exit_code"] = checked_exit(arm.get("process_exit_code"), f"{name}.process_exit_code")
    result["verifier_exit_code"] = checked_exit(arm.get("verifier_exit_code"), f"{name}.verifier_exit_code")
    result["verified"] = (
        result["verifier_exit_code"] == 0 if result["verifier_exit_code"] is not None else None
    )
    return result


def summarize_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError("expected manifest schema_version 1")
    pairs = manifest.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("manifest must contain at least one pair")
    results = []
    seen: set[str] = set()
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise ValueError(f"pair {index} must be an object")
        pair_id = pair.get("id")
        if not isinstance(pair_id, str) or not pair_id or pair_id in seen:
            raise ValueError(f"pair {index} needs a unique nonempty id")
        seen.add(pair_id)
        fixture_sha256 = pair.get("fixture_sha256")
        if not isinstance(fixture_sha256, str) or not SHA256_RE.fullmatch(fixture_sha256):
            raise ValueError(f"{pair_id}.fixture_sha256 must be a lowercase SHA-256 digest")
        for field in ("model", "reasoning_effort"):
            if not isinstance(pair.get(field), str) or not pair[field]:
                raise ValueError(f"{pair_id}.{field} must be a nonempty string")
        if pair.get("order") not in (["off", "on"], ["on", "off"]):
            raise ValueError(f"{pair_id}.order must list both arms in execution order")
        arms = {}
        for name in ("off", "on"):
            arm = pair.get(name)
            if not isinstance(arm, dict):
                raise ValueError(f"{pair_id}.{name} must be an object")
            arms[name] = arm_result(arm, path.parent, f"{pair_id}.{name}")
        off = arms["off"]
        on = arms["on"]
        differences = {}
        for field in ("total_input_output_tokens", "input_tokens", "cached_input_tokens",
                      "cache_write_input_tokens", "uncached_input_tokens", "output_tokens"):
            first = off["tokens"][field]
            second = on["tokens"][field]
            differences[field] = second - first if first is not None and second is not None else None
        for field in ("tool_calls", "nonzero_tool_exits", "failed_tool_items", "repeated_exact_commands",
                      "artifact_read_commands", "elapsed_seconds"):
            differences[field] = on[field] - off[field]
        measurement_complete = all(
            arm["process_exit_code"] == 0
            and arm["failed_turns"] == 0
            and arm["error_events"] == 0
            and arm["completed_turns"] > 0
            and arm["verified"] is not None
            and arm["tokens"]["total_input_output_tokens"] is not None
            and arm["tokens"]["uncached_input_tokens"] is not None
            for arm in arms.values()
        )
        results.append({
            "id": pair_id,
            "fixture_sha256": fixture_sha256,
            "model": pair["model"],
            "reasoning_effort": pair["reasoning_effort"],
            "order": pair["order"],
            "off": off,
            "on": on,
            "on_minus_off": differences,
            "measurement_complete": measurement_complete,
        })
    return {
        "schema_version": 1,
        "pair_count": len(results),
        "measurement_complete_pairs": sum(pair["measurement_complete"] for pair in results),
        "pairs": results,
        "interpretation": (
            "Counts describe these runs only. Exact command repetition is a proxy, not proof of "
            "reacquisition. Verifier codes and elapsed time come from the manifest. "
            "Missing usage fields are null; complete fields do not establish adequate sample size. "
            "This report does not estimate API cost or prove causality."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="private JSON manifest with paired JSONL trace paths")
    arguments = parser.parse_args()
    print(json.dumps(summarize_manifest(arguments.manifest), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
