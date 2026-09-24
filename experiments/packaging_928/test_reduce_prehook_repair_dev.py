"""Request-ledger invariants for the private-evidence reducer."""
from __future__ import annotations

import copy
import unittest

from experiments.packaging_928.reduce_prehook_repair_dev import validate_request_accounting


def fixture() -> dict:
    requests = []
    for index in (1, 2):
        requests.append({
            "attempt_id": f"attempt-{index}", "method": "POST",
            "path": "/backend-api/codex/responses", "authorization_present": True,
            "upstream_status": 200, "done": True, "upstream_error": None,
            "completions": [{"id_present": True, "response_id_sha256": f"response-{index}",
                             "input_tokens": 10, "output_tokens": 2, "cached_tokens": 4}],
        })
    return {
        "all_proxy_handlers_done": True, "listener_stopped": True,
        "active_handlers": 0, "handler_errors": 0, "rejected_client_auth": 0,
        "auth_file_in_home": False, "broker_injected": 2, "sink_requests": requests,
        "journal": {
            "all_attempts_have_observed_usage": True,
            "attempts": 2, "states": {"completed": 2, "pending": 0, "unknown": 0},
            "observed_completed_usage": {
                "input_tokens": 20, "output_tokens": 4, "cached_input_tokens": 8},
        },
    }


class RequestAccountingTests(unittest.TestCase):
    def test_complete_requests_reconcile(self):
        self.assertEqual(validate_request_accounting(fixture())["attempts"], 2)

    def test_empty_journal_cannot_erase_completed_requests(self):
        data = fixture()
        data["journal"]["attempts"] = 0
        data["journal"]["states"]["completed"] = 0
        data["journal"]["observed_completed_usage"] = {
            "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
        with self.assertRaises(ValueError):
            validate_request_accounting(data)

    def test_response_usage_must_equal_journal(self):
        data = fixture()
        data["sink_requests"][0]["completions"][0]["cached_tokens"] += 1
        with self.assertRaises(ValueError):
            validate_request_accounting(data)

    def test_duplicate_response_cannot_be_counted_twice(self):
        data = copy.deepcopy(fixture())
        data["sink_requests"][1]["completions"][0]["response_id_sha256"] = "response-1"
        with self.assertRaises(ValueError):
            validate_request_accounting(data)


if __name__ == "__main__":
    unittest.main()
