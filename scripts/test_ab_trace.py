#!/usr/bin/env python3
"""Focused tests for aggregate-only A/B trace reporting."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ab_trace import parse_trace, summarize_manifest


def write_trace(path: Path, *, cached: int | None, command: str, failed: bool = False) -> None:
    usage = {"input_tokens": 100, "output_tokens": 20, "cache_write_input_tokens": 10}
    if cached is not None:
        usage["cached_input_tokens"] = cached
    events = [
        {"type": "item.started", "item": {"id": "1", "type": "command_execution", "command": command}},
        {"type": "item.completed", "item": {"id": "1", "type": "command_execution", "command": command, "exit_code": 0}},
        {"type": "item.completed", "item": {"id": "2", "type": "command_execution", "command": command, "exit_code": 1 if failed else 0}},
        {"type": "turn.completed", "usage": usage},
    ]
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")


class TraceTest(unittest.TestCase):
    def test_counts_tokens_and_calls_without_reporting_raw_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            command = "sed -n '1,5p' /private/obs_" + "a" * 24 + ".txt"
            write_trace(path, cached=40, command=command, failed=True)
            result = parse_trace(path)
            self.assertEqual(result["tokens"]["uncached_input_tokens"], 50)
            self.assertEqual(result["tokens"]["total_input_output_tokens"], 120)
            self.assertEqual(result["tool_calls"], 2)
            self.assertEqual(result["nonzero_tool_exits"], 1)
            self.assertEqual(result["failed_tool_items"], 0)
            self.assertEqual(result["repeated_exact_commands"], 1)
            self.assertEqual(result["artifact_read_commands"], 2)
            self.assertNotIn(command, json.dumps(result))

    def test_missing_cached_field_stays_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            write_trace(path, cached=None, command="pwd")
            result = parse_trace(path)
            self.assertIsNone(result["tokens"]["cached_input_tokens"])
            self.assertIsNone(result["tokens"]["uncached_input_tokens"])

    def test_pair_report_keeps_unverified_run_inconclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_trace(root / "off.jsonl", cached=20, command="pwd")
            write_trace(root / "on.jsonl", cached=30, command="pwd")
            manifest = {
                "schema_version": 1,
                "pairs": [{
                    "id": "fixture-1", "fixture_sha256": "f" * 64,
                    "model": "gpt-6-luna", "reasoning_effort": "low", "order": ["off", "on"],
                    "off": {"trace": "off.jsonl", "elapsed_seconds": 4, "process_exit_code": 0, "verifier_exit_code": 0},
                    "on": {"trace": "on.jsonl", "elapsed_seconds": 3, "process_exit_code": 0, "verifier_exit_code": None},
                }],
            }
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            result = summarize_manifest(path)
            self.assertEqual(result["pair_count"], 1)
            self.assertEqual(result["measurement_complete_pairs"], 0)
            self.assertEqual(result["pairs"][0]["on_minus_off"]["cached_input_tokens"], 10)
            self.assertIsNone(result["pairs"][0]["on"]["verified"])

    def test_malformed_trace_is_rejected_with_line_number(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text('{}\n{invalid\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid JSONL at line 2"):
                parse_trace(path)

    def test_nonfinite_elapsed_time_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_trace(root / "trace.jsonl", cached=10, command="pwd")
            manifest = {"schema_version": 1, "pairs": [{
                "id": "fixture-1", "fixture_sha256": "f" * 64,
                "model": "gpt-6-luna", "reasoning_effort": "low", "order": ["off", "on"],
                "off": {"trace": "trace.jsonl", "elapsed_seconds": float("nan"),
                        "process_exit_code": 0, "verifier_exit_code": 0},
                "on": {"trace": "trace.jsonl", "elapsed_seconds": 1,
                       "process_exit_code": 0, "verifier_exit_code": 0},
            }]}
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "finite nonnegative"):
                summarize_manifest(path)


if __name__ == "__main__":
    unittest.main()
