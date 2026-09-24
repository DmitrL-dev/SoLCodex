"""Check that interruption and missing usage are reported without false zeros."""
import unittest

try:
    from scripts.audit_proxy_probe import audit
except ModuleNotFoundError:
    from audit_proxy_probe import audit


class AuditProxyProbeTests(unittest.TestCase):
    def test_completion_after_cli_kill(self):
        attempt = {"upstream_status": 200, "client_disconnected": True,
                   "cli_killed_at": 4.0, "completion_at": 19.25,
                   "completions": [{"input_tokens": 100, "output_tokens": 25,
                                    "cached_tokens": 40}],
                   "authorization": "DO_NOT_EMIT", "body": "DO_NOT_EMIT"}
        result = audit({"exit": -9, "sink_requests": [attempt]},
                       [{"type": "turn.started"}])
        self.assertEqual(result["kill_to_completion_seconds"], [15.25])
        self.assertEqual(result["observed_response_usage"]["output_tokens"], 25)
        self.assertIsNone(result["cli_completed_usage"])
        self.assertFalse(result["provider_billing_complete"])
        self.assertNotIn("DO_NOT_EMIT", str(result))

    def test_missing_usage_is_unknown_not_zero(self):
        result = audit({"exit": 1, "sink_requests": [
            {"upstream_status": 200, "completions": []}]}, [])
        self.assertEqual(result["attempt_states"]["missing_or_ambiguous_usage"], 1)
        self.assertFalse(result["all_attempts_have_observed_usage"])

    def test_normal_cli_and_proxy_usage(self):
        result = audit({"exit": 0, "sink_requests": [
            {"upstream_status": 200, "completions": [
                {"input_tokens": 20, "output_tokens": 2, "cached_tokens": 10}]}]},
            [{"type": "turn.completed", "usage": {"input_tokens": 20,
                "output_tokens": 2, "cached_input_tokens": 10}}])
        self.assertEqual(result["observed_response_usage"], result["cli_completed_usage"])
        self.assertEqual(result["cli_turn_completions"], 1)

    def test_untrusted_exit_and_duplicate_response_do_not_escape_or_double_count(self):
        completion = {"response_id_sha256": "a" * 64,
                      "input_tokens": 20, "output_tokens": 2, "cached_tokens": 10}
        result = audit({"exit": {"error": "Bearer SYNTHETIC_SECRET"},
                        "sink_requests": [{"upstream_status": 200,
                                           "completions": [completion]},
                                          {"upstream_status": 200,
                                           "completions": [completion]}]}, [])
        self.assertIsNone(result["cli_exit_code"])
        self.assertEqual(result["observed_response_usage"]["input_tokens"], 20)
        self.assertEqual(result["attempt_states"]["missing_or_ambiguous_usage"], 1)
        self.assertFalse(result["all_attempts_have_observed_usage"])
        self.assertNotIn("SYNTHETIC_SECRET", str(result))

    def test_invalid_cli_cache_partition_is_unknown(self):
        result = audit({"exit": 0, "sink_requests": []}, [
            {"type": "turn.completed", "usage": {"input_tokens": 100,
             "output_tokens": 3, "cached_input_tokens": 999}}])
        self.assertIsNone(result["cli_completed_usage"])

    def test_journal_summary_is_sanitized_and_cross_checked(self):
        attempt = {"upstream_status": 200, "completions": [
            {"input_tokens": 20, "output_tokens": 2, "cached_tokens": 10}]}
        journal = {"attempts": 1,
                   "states": {"pending": 0, "completed": 1, "unknown": 0},
                   "observed_completed_usage": {"input_tokens": 20,
                       "output_tokens": 2, "cached_input_tokens": 10},
                   "private": "Bearer SYNTHETIC_SECRET"}
        result = audit({"exit": 0, "sink_requests": [attempt], "journal": journal}, [])
        self.assertTrue(result["attempt_journal"]["matches_proxy_observation"])
        self.assertFalse(result["attempt_journal"]["provider_billing_complete"])
        self.assertNotIn("SYNTHETIC_SECRET", str(result))


if __name__ == "__main__":
    unittest.main()
