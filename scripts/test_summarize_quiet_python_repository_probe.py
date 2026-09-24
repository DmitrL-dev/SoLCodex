"""Synthetic checks that public metadata aggregates contain no repository names."""

import json
import unittest

from scripts import probe_quiet_python_repositories as probe
from scripts import summarize_quiet_python_repository_probe as summarize
from scripts.test_probe_quiet_python_repositories import queue_fixture, response


class ProbeAggregateTests(unittest.TestCase):
    def test_aggregate_counts_redirects_without_claiming_ancestry(self):
        queue = queue_fixture()
        first = response('example/alpha', 0)
        second = response('example/beta', 1, status=301)
        second['headers']['location'] = 'https://api.github.com/repos/example/new'
        second['complete'] = True
        result = summarize.summarize(b'private projection', probe.encode(queue),
                                     b'private journal', queue, [first, second], 2)
        self.assertEqual(result['observed_queue_entry_count'], 2)
        self.assertEqual(result['http_attempt_count'], 2)
        self.assertEqual(result['response_status_counts'], {'200': 1, '301': 1})
        self.assertEqual(result['valid_200_metadata_count'], 1)
        self.assertEqual(result['without_valid_200_metadata_count'], 1)
        self.assertEqual(result['redirect_to_api_github_count'], 1)
        self.assertFalse(result['ancestry_independently_verified'])
        self.assertFalse(result['sample_selected'])
        self.assertFalse(result['model_run_authorized'])
        serialized = json.dumps(result)
        self.assertNotIn('example/alpha', serialized)
        self.assertNotIn('example/beta', serialized)


if __name__ == '__main__':
    unittest.main()
