"""Synthetic aggregate checks for explicit repository ID redirect probes."""

import json
import unittest

from scripts import summarize_quiet_python_redirect_probe as summary


class RedirectAggregateTests(unittest.TestCase):
    def test_id_alias_counts_are_public_without_repository_names(self):
        queue = {'redirect_source_count': 2, 'repository_count': 2,
                 'redirect_http_limit': 6,
                 'redirect_sources': [
                     {'source_repository': 'example/old-a',
                      'target_endpoint': 'repositories/101'},
                     {'source_repository': 'example/old-b',
                      'target_endpoint': 'repositories/102'}]}
        records = [
            {'requested_repository': 'repositories/101', 'http_status': 200,
             'complete': True,
             'summary': {'metadata_status': 'observed', 'repository_id': 101,
                         'full_name': 'example/new-a', 'fork': False,
                         'parent_full_name': None, 'source_full_name': None}},
            {'requested_repository': 'repositories/102', 'http_status': 200,
             'complete': True,
             'summary': {'metadata_status': 'observed', 'repository_id': 102,
                         'full_name': 'example/new-b', 'fork': True,
                         'parent_full_name': 'example/root',
                         'source_full_name': 'example/root'}}]
        result = summary.summarize(b'source queue', b'source journal',
                                   b'target queue', b'target journal',
                                   queue, records, 2, b'published source aggregate')
        self.assertEqual(result['valid_200_target_metadata_count'], 2)
        self.assertEqual(result['response_id_matches_requested_id_count'], 2)
        self.assertEqual(result['source_name_differs_from_current_count'], 2)
        self.assertEqual(result['fork_true_count'], 1)
        self.assertFalse(result['ancestry_independently_verified'])
        self.assertFalse(result['sample_selected'])
        self.assertNotIn('example/', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
