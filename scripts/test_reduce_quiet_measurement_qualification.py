"""Check the public no-model reducer against the frozen matrix and tampering."""

import copy
import unittest

from scripts.reduce_quiet_measurement_qualification import EXPECTED, RESULT, reduce, strict_json


class QualificationReducerTests(unittest.TestCase):
    def setUp(self):
        self.expected = strict_json(EXPECTED.read_bytes())
        self.result = strict_json(RESULT.read_bytes())

    def test_public_result_matches_frozen_matrix_without_model_authorization(self):
        decision = reduce(self.expected, self.result)
        self.assertTrue(decision['direct_probe_pass'])
        self.assertEqual((decision['trace_cases'], decision['ledger_cases']), (14, 5))
        self.assertFalse(decision['model_run_authorized'])

    def test_partial_usage_cannot_be_complete(self):
        changed = copy.deepcopy(self.result)
        changed['observations']['ledgers']['partial_usage']['complete'] = True
        changed['observations']['ledgers']['partial_usage']['usage'] = {'input_tokens': 10}
        decision = reduce(self.expected, changed)
        self.assertFalse(decision['direct_probe_pass'])
        self.assertEqual(decision['ledger_mismatches'], ['partial_usage'])

    def test_missing_case_and_duplicate_json_are_rejected(self):
        changed = copy.deepcopy(self.result)
        del changed['observations']['traces']['signal_exit']
        with self.assertRaises(ValueError):
            reduce(self.expected, changed)
        with self.assertRaises(ValueError):
            strict_json(b'{"status":"unknown","status":"observed"}')

    def test_published_probe_hash_is_bound(self):
        changed = copy.deepcopy(self.result)
        changed['source_sha256']['qualification_probe.py'] = '0' * 64
        with self.assertRaises(ValueError):
            reduce(self.expected, changed)


if __name__ == '__main__':
    unittest.main()
