"""False-pass controls for the action-fusion development reducer."""
from __future__ import annotations

import copy
import json
import unittest

from experiments.action_fusion.reduce_pilot import check_usage, trace_summary


def proxy_fixture() -> dict:
    return {
        "sink_requests": [{"method": "POST", "path": "/backend-api/codex/responses",
                           "upstream_status": 200, "done": True,
                           "client_disconnected": False, "upstream_error": None,
                           "attempt_id": "attempt-1",
                           "completions": [{"id_present": True,
                                            "response_id_sha256": "a" * 64,
                                            "input_tokens": 10, "output_tokens": 2,
                                            "cached_tokens": 3}]}],
        "journal": {"attempts": 1, "states": {"pending": 0, "completed": 1,
                                               "unknown": 0},
                    "all_attempts_have_observed_usage": True,
                    "provider_billing_complete": False,
                    "observed_completed_usage": {"input_tokens": 10,
                                                 "output_tokens": 2,
                                                 "cached_input_tokens": 3}},
        "auth_file_in_home": False, "broker_injected": 1,
        "rejected_client_auth": 0, "handler_errors": 0,
        "all_proxy_handlers_done": True, "listener_stopped": True,
        "accepted_connections": 1, "finished_connections": 1,
    }


class ReducerTests(unittest.TestCase):
    def test_completed_usage(self):
        self.assertEqual(check_usage(proxy_fixture())["input_plus_output_tokens"], 12)

    def test_pending_request_cannot_be_zeroed(self):
        proxy = proxy_fixture()
        proxy["journal"]["states"] = {"pending": 1, "completed": 0, "unknown": 0}
        proxy["journal"]["all_attempts_have_observed_usage"] = False
        with self.assertRaises(ValueError):
            check_usage(proxy)

    def test_missing_completion_rejected(self):
        proxy = proxy_fixture()
        proxy["sink_requests"][0]["completions"] = []
        with self.assertRaises(ValueError):
            check_usage(proxy)

    def test_failed_edit_then_test_is_detected(self):
        event = {"type": "item.completed", "item": {
            "type": "command_execution", "command":
                "p.write_text(s)\nPY\npython3 -m unittest discover -s tests -v",
            "aggregated_output": "can't create temp file for here document\n"
                                 "test_existing_integer_seconds ... ok\n",
            "exit_code": 1}}
        turn = {"type": "turn.completed", "usage": {
            "input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 3}}
        trace = (json.dumps(event) + "\n" + json.dumps(turn) + "\n").encode()
        report = trace_summary(trace, check_usage(proxy_fixture()))
        self.assertEqual(report["combined_shell_edit_test_attempts"], 1)
        self.assertEqual(report["combined_attempt_edit_failed_but_test_ran"], 1)
        self.assertFalse(report["separately_permissioned_fusion_attested"])


if __name__ == "__main__":
    unittest.main()
