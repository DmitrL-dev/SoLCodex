"""Ensure request-marker evidence is checked and marker text is never emitted."""
import json
from pathlib import Path
import tempfile
import unittest

try:
    from scripts.audit_prehook_request_probe import inspect
except ModuleNotFoundError:
    from audit_prehook_request_probe import inspect


class RequestProbeAuditTests(unittest.TestCase):
    def fixture(self, root, prehook):
        marker = b"HIDDEN_" + b"a" * 24
        work, artifacts = root / "work", root / "artifacts"
        work.mkdir()
        artifacts.mkdir()
        payload = b"ordinary output\n" + marker + b"\n"
        (work / "payload.txt").write_bytes(payload)
        if prehook:
            (artifacts / "captured.bin").write_bytes(payload)
        requests = []
        for index in range(2):
            requests.append({"upstream_status": 200, "body_bytes": 100 + index,
                             "client_disconnected": False, "upstream_error": None,
                             "done": True,
                             "request_has_probe_marker": index == 1 and not prehook,
                             "completions": [{"input_tokens": 10 + index,
                                              "output_tokens": 1, "cached_tokens": 2}]})
        summary = {"exit": 0, "proxy_blocked": 0, "sink_requests": requests,
                   "journal": {"attempts": 2, "states": {"completed": 2},
                               "all_attempts_have_observed_usage": True,
                               "observed_completed_usage": {"input_tokens": 21,
                                                            "output_tokens": 2,
                                                            "cached_input_tokens": 4},
                               "provider_billing_complete": False}}
        (artifacts / "proxy-summary.json").write_text(json.dumps(summary))
        output = "CAPTURED_BYTES=51 STATUS=0" if prehook else payload.decode()
        final = "UNKNOWN" if prehook else marker.decode()
        trace = [{"type": "item.completed", "item": {"type": "command_execution",
                                                   "aggregated_output": output}},
                 {"type": "item.completed", "item": {"type": "agent_message",
                                                   "text": final}},
                 {"type": "turn.completed", "usage": {"input_tokens": 1,
                                                      "output_tokens": 1}}]
        (artifacts / "trace.jsonl").write_text("\n".join(json.dumps(e) for e in trace) + "\n")
        return marker, summary

    def test_direct_and_prehook_controls_withhold_marker(self):
        for prehook in (False, True):
            with self.subTest(prehook=prehook), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker, _ = self.fixture(root, prehook)
                result = inspect(root, prehook)
                self.assertEqual(result["request_marker_flags"],
                                 [False, False] if prehook else [False, True])
                self.assertNotIn(marker.decode(), json.dumps(result))

    def test_wrong_next_request_flag_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, summary = self.fixture(root, False)
            summary["sink_requests"][1]["request_has_probe_marker"] = False
            (root / "artifacts" / "proxy-summary.json").write_text(json.dumps(summary))
            with self.assertRaises(ValueError):
                inspect(root, False)

    def test_missing_final_usage_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, summary = self.fixture(root, True)
            summary["sink_requests"][1]["completions"] = []
            (root / "artifacts" / "proxy-summary.json").write_text(json.dumps(summary))
            with self.assertRaises(ValueError):
                inspect(root, True)


if __name__ == "__main__":
    unittest.main()
