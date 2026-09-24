from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from audit_otel_usage import audit


def record(name, **fields):
    values = {"event.name": name, "user.email": "private@example.invalid",
              "output": "private tool output", **fields}
    return {"attributes": [{"key": key, "value": {"stringValue": str(value)}}
                            for key, value in values.items()]}


class OTelAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def batch(self, *records):
        path = self.root / "batch.json"
        path.write_text(json.dumps({"resourceLogs": [{"scopeLogs": [{"logRecords": records}]}]}))
        return path

    def trace(self, usage=None):
        path = self.root / "trace.jsonl"
        events = [{"type": "turn.started"}]
        if usage is not None:
            events.append({"type": "turn.completed", "usage": usage})
        path.write_text("".join(json.dumps(item) + "\n" for item in events))
        return path

    def test_websocket_startup_usage_is_not_silently_merged_into_cli(self):
        batch = self.batch(
            record("codex.websocket_request"),
            record("codex.sse_event", **{"event.kind": "response.completed",
                  "input_token_count": "10", "cached_token_count": "0",
                  "cache_write_token_count": "0", "output_token_count": "0",
                  "reasoning_token_count": "0"}),
            record("codex.startup_phase", **{"startup.phase": "startup_prewarm_websocket_warmup"}),
            record("codex.websocket_request"),
            record("codex.sse_event", **{"event.kind": "response.completed",
                  "input_token_count": "20", "cached_token_count": "5",
                  "cache_write_token_count": "0", "output_token_count": "2",
                  "reasoning_token_count": "1"}),
        )
        result = audit([batch], self.trace({"input_tokens": 20, "cached_input_tokens": 5,
                                          "cache_write_input_tokens": 0, "output_tokens": 2,
                                          "reasoning_output_tokens": 1}), 0)
        self.assertEqual(result["otel_reported_tokens"]["total_input_output_tokens"], 32)
        self.assertEqual(result["otel_minus_cli"]["total_input_output_tokens"], 10)
        self.assertEqual(result["startup_prewarm_websocket_warmup_phases"], 1)
        self.assertFalse(result["end_to_end_usage_complete"])
        self.assertNotIn("private@example.invalid", json.dumps(result))
        self.assertNotIn("private tool output", json.dumps(result))

    def test_http_duplicate_empty_completion_is_not_counted_as_zero_request(self):
        batch = self.batch(
            record("codex.api_request", endpoint="/responses", attempt="0"),
            record("codex.sse_event", **{"event.kind": "response.completed"}),
            record("codex.sse_event", **{"event.kind": "response.completed",
                  "input_token_count": "7", "cached_token_count": "0",
                  "cache_write_token_count": "0", "output_token_count": "3",
                  "reasoning_token_count": "1"}),
        )
        result = audit([batch], self.trace({"input_tokens": 7, "cached_input_tokens": 0,
                                          "cache_write_input_tokens": 0, "output_tokens": 3,
                                          "reasoning_output_tokens": 1}), 0)
        self.assertEqual(result["response_completions_without_usage"], 1)
        self.assertEqual(result["response_completions_with_usage"], 1)
        self.assertEqual(result["otel_minus_cli"]["total_input_output_tokens"], 0)
        self.assertFalse(result["end_to_end_usage_complete"])

    def test_abrupt_kill_preserves_unknown_usage(self):
        batch = self.batch(record("codex.api_request", endpoint="/responses", attempt="0"),
                           record("codex.sse_event", **{"event.kind": "response.created"}))
        result = audit([batch], self.trace(), -9)
        self.assertEqual(result["model_request_events"], 1)
        self.assertEqual(result["request_count_minus_usage_completion_count"], 1)
        self.assertIsNone(result["otel_reported_tokens"]["total_input_output_tokens"])
        self.assertIsNone(result["cli"]["tokens"]["total_input_output_tokens"])

    def test_replayed_completion_remains_ambiguous_without_request_id(self):
        completion = record("codex.sse_event", **{"event.kind": "response.completed",
            "input_token_count": "10", "cached_token_count": "0",
            "cache_write_token_count": "0", "output_token_count": "1",
            "reasoning_token_count": "0"})
        batch = self.batch(record("codex.api_request", endpoint="/responses"),
                           completion, completion)
        result = audit([batch], self.trace({"input_tokens": 10, "cached_input_tokens": 0,
            "cache_write_input_tokens": 0, "output_tokens": 1,
            "reasoning_output_tokens": 0}))
        self.assertEqual(result["request_count_minus_usage_completion_count"], -1)
        self.assertEqual(result["otel_minus_cli"]["total_input_output_tokens"], 11)
        self.assertEqual(result["request_identity_keys_present"], [])
        self.assertFalse(result["end_to_end_usage_complete"])

    def test_invalid_cache_partition_is_rejected(self):
        batch = self.batch(record("codex.sse_event", **{"event.kind": "response.completed",
            "input_token_count": "10", "cached_token_count": "9",
            "cache_write_token_count": "2", "output_token_count": "1"}))
        with self.assertRaisesRegex(ValueError, "cache categories"):
            audit([batch])


if __name__ == "__main__":
    unittest.main()
