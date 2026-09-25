"""Adversarial arithmetic and incompleteness checks for the v4 reducer."""

import hashlib
import json
import unittest

import reduce_quiet_variance_calibration_v4 as reducer


SCHEDULE_RAW = reducer.SCHEDULE.read_bytes()
ASSIGNMENTS = json.loads(SCHEDULE_RAW)['schedule']


def campaign():
    rows = []
    for assignment in ASSIGNMENTS:
        quiet = assignment['arm'] == 'quiet'
        rows.append({**assignment, 'status': 'completed', 'quality': True,
                     'usage': {'input_tokens': 80 if quiet else 100,
                               'cached_input_tokens': 20,
                               'output_tokens': 20},
                     'elapsed_seconds': 8 if quiet else 10,
                     'timed_out': False, 'reason': None})
    return {'schema': reducer.SCHEMA,
            'schedule_sha256': hashlib.sha256(SCHEDULE_RAW).hexdigest(),
            'campaign_state': 'complete', 'terminal_reason': None,
            'slots': rows}


def stop_after(value, index, usage):
    value['campaign_state'] = 'stopped'
    value['terminal_reason'] = 'cleanup_uncertain'
    rows = value['slots']
    rows[index].update(status='stopped', quality=None, usage=usage,
                       elapsed_seconds=None, timed_out=None,
                       reason='cleanup_uncertain')
    for row in rows[index + 1:]:
        row.update(status='unstarted', quality=None, usage=None,
                   elapsed_seconds=None, timed_out=None, reason=None)


class ReducerChecks(unittest.TestCase):
    def test_complete_balanced_pairs_and_all_spend(self):
        result = reducer.reduce(campaign(), SCHEDULE_RAW)
        self.assertTrue(result['complete'])
        self.assertEqual(result['all_started_usage']['input_plus_output_tokens'], 1760)
        self.assertEqual(result['arm_summary']['quiet']['input_plus_output_per_accepted'], 100)
        self.assertEqual(result['arm_summary']['verbose']['input_plus_output_per_accepted'], 120)
        self.assertEqual(len(result['pairs']), 8)
        self.assertEqual({pair['quiet_minus_verbose_tokens'] for pair in result['pairs']}, {-20})
        self.assertEqual(result['order_effects']['first_arm']['quiet']['pairs'], 4)
        self.assertEqual(result['order_effects']['first_arm']['verbose']['pairs'], 4)
        self.assertEqual(result['order_effects']['first_task']['click']['blocks'], 2)
        self.assertEqual(result['order_effects']['first_task']['packaging']['blocks'], 2)

    def test_failed_repair_and_timeout_keep_spend(self):
        value = campaign()
        value['slots'][1]['quality'] = False
        value['slots'][1]['timed_out'] = True
        result = reducer.reduce(value, SCHEDULE_RAW)
        quiet = result['arm_summary']['quiet']
        self.assertEqual(quiet['accepted'], 7)
        self.assertEqual(quiet['failed_repairs'], 1)
        self.assertEqual(quiet['accounted_timeouts'], 1)
        self.assertEqual(quiet['usage']['input_plus_output_tokens'], 800)
        self.assertEqual(quiet['input_plus_output_per_accepted'], 800 / 7)
        self.assertEqual(result['pairs'][1]['acceptance'], 'verbose_only')

    def test_zero_accepted_is_undefined(self):
        value = campaign()
        for row in value['slots']:
            row['quality'] = False
        result = reducer.reduce(value, SCHEDULE_RAW)
        self.assertIsNone(result['arm_summary']['quiet']['input_plus_output_per_accepted'])
        self.assertIsNone(result['arm_summary']['verbose']['input_plus_output_per_accepted'])
        self.assertEqual(result['all_started_usage']['input_plus_output_tokens'], 1760)

    def test_stopped_slot_preserves_known_spend_but_no_effect(self):
        value = campaign()
        original_usage = value['slots'][1]['usage']
        stop_after(value, 1, original_usage)
        result = reducer.reduce(value, SCHEDULE_RAW)
        self.assertFalse(result['complete'])
        self.assertEqual(result['started_count'], 2)
        self.assertEqual(len(result['unstarted_ids']), 14)
        self.assertEqual(result['all_started_usage']['input_plus_output_tokens'], 220)
        self.assertIsNone(result['arm_summary'])
        self.assertIsNone(result['pairs'])

    def test_unknown_stopped_usage_is_not_imputed(self):
        value = campaign()
        stop_after(value, 1, None)
        result = reducer.reduce(value, SCHEDULE_RAW)
        self.assertFalse(result['all_started_usage_complete'])
        self.assertIsNone(result['all_started_usage'])
        self.assertEqual(result['known_started_usage']['input_plus_output_tokens'], 120)
        self.assertEqual(result['missing_usage_ids'], [ASSIGNMENTS[1]['id']])

    def test_prestart_stop_keeps_all_slots_unstarted(self):
        value = campaign()
        value['campaign_state'] = 'stopped'
        value['terminal_reason'] = 'preflight_drift'
        for row in value['slots']:
            row.update(status='unstarted', quality=None, usage=None,
                       elapsed_seconds=None, timed_out=None, reason=None)
        result = reducer.reduce(value, SCHEDULE_RAW)
        self.assertEqual(result['started_count'], 0)
        self.assertEqual(len(result['unstarted_ids']), 16)
        self.assertEqual(result['all_started_usage']['input_plus_output_tokens'], 0)
        self.assertIsNone(result['arm_summary'])

    def test_unresolved_start_cannot_be_called_stopped(self):
        value = campaign()
        stop_after(value, 1, None)
        value['slots'][1]['status'] = 'unresolved'
        with self.assertRaises(ValueError):
            reducer.reduce(value, SCHEDULE_RAW)
        value['campaign_state'] = 'unresolved'
        result = reducer.reduce(value, SCHEDULE_RAW)
        self.assertEqual(result['unresolved_ids'], [ASSIGNMENTS[1]['id']])
        self.assertIsNone(result['all_started_usage'])

    def test_rejects_gap_and_boolean_usage(self):
        value = campaign()
        value['slots'][0]['status'] = 'unstarted'
        with self.assertRaises(ValueError):
            reducer.reduce(value, SCHEDULE_RAW)
        value = campaign()
        value['slots'][0]['usage']['input_tokens'] = True
        with self.assertRaises(ValueError):
            reducer.reduce(value, SCHEDULE_RAW)


if __name__ == '__main__':
    unittest.main()
