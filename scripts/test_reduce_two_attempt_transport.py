"""Protect the frozen synthetic transport comparison and cache arithmetic."""

import copy
import unittest

from scripts.reduce_two_attempt_transport import EXPECTED, RESULT, reduce, strict_json


class TwoAttemptReducerTests(unittest.TestCase):
    def setUp(self):
        self.expected = strict_json(EXPECTED.read_bytes())
        self.result = strict_json(RESULT.read_bytes())

    def test_public_result_matches_and_cached_input_is_not_added_twice(self):
        decision = reduce(self.expected, self.result)
        self.assertTrue(decision['synthetic_transport_probe_pass'])
        self.assertEqual(decision['positive_input_plus_output'], 400)
        self.assertEqual(decision['positive_uncached_input'], 130)
        self.assertEqual(decision['partial_subtotal_input_plus_output'], 250)
        self.assertFalse(decision['model_run_authorized'])

    def test_missing_attempt_usage_cannot_be_replaced_with_partial_subtotal(self):
        changed = copy.deepcopy(self.result)
        changed['missing_first_usage']['usage'] = changed['missing_first_usage']['observed_subtotal']
        decision = reduce(self.expected, changed)
        self.assertFalse(decision['synthetic_transport_probe_pass'])
        self.assertFalse(decision['missing_usage_matches'])

    def test_published_fixture_hash_and_token_shape_are_required(self):
        changed = copy.deepcopy(self.result)
        changed['source_sha256']['test_live_pilot.py'] = '0' * 64
        with self.assertRaises(ValueError):
            reduce(self.expected, changed)
        changed = copy.deepcopy(self.result)
        changed['positive']['usage'].pop('output_tokens')
        with self.assertRaises(ValueError):
            reduce(self.expected, changed)


if __name__ == '__main__':
    unittest.main()
