"""Synthetic checks for pinned redirect-target queue construction."""

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from scripts import probe_quiet_python_redirect_targets as redirects
from scripts import probe_quiet_python_repositories as probe
from scripts import rank_quiet_python_lineages as scoped


def fixture():
    queue = {'schema': probe.QUEUE_SCHEMA,
             'repositories': [f'example/old{index}' for index in range(6)]}
    queue_bytes = probe.encode(queue)
    journal_bytes = b'pinned private journal\n'
    records = [{'index': index,
                'requested_repository': f'example/old{index}',
                'http_status': 301,
                'headers': {'location':
                    f'https://api.github.com/repositories/{100 + index // 2}'}}
               for index in range(6)]
    aggregate = {'schema': 'solcodex.quiet-python-repository-probe-aggregate.v1',
                 'queue_sha256': scoped.digest(queue_bytes),
                 'journal_sha256': scoped.digest(journal_bytes),
                 'observed_queue_entry_count': 20,
                 'redirect_observed_count': 6}
    return queue_bytes, journal_bytes, queue, records, aggregate


class RedirectTargetProbeTests(unittest.TestCase):
    def test_numeric_id_transport_makes_one_nonredirecting_request(self):
        class FakeResponse:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _):
                return json.dumps({'id': 123, 'full_name': 'example/new',
                                   'fork': False}).encode()

            def geturl(self):
                return 'https://api.github.com/repositories/123'

        class FakeOpener:
            calls = 0

            def open(self, request, timeout):
                self.calls += 1
                self.request_url = request.full_url
                return FakeResponse()

        opener = FakeOpener()
        with mock.patch.object(redirects, 'build_opener', return_value=opener) as built:
            record = redirects.fetch_target('repositories/123', 0)
        self.assertIsInstance(built.call_args.args[0], probe.NoRedirect)
        self.assertEqual(opener.calls, 1)
        self.assertEqual(opener.request_url,
                         'https://api.github.com/repositories/123')
        self.assertTrue(record['complete'])
        self.assertEqual(record['summary']['repository_id'], 123)

    def test_location_accepts_only_exact_api_repository_endpoint(self):
        self.assertEqual(redirects.target_from_location(
            'https://api.github.com/repositories/123'), 'repositories/123')
        for bad in (None, 'http://api.github.com/repositories/123',
                    'https://other.example/repositories/123',
                    'https://api.github.com/repositories/123?x=1',
                    'https://api.github.com/repositories/123/extra',
                    'https://api.github.com/repositories/abc',
                    'https://api.github.com/repos/a/b'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                redirects.target_from_location(bad)

    def test_source_pin_and_all_six_redirects_are_required(self):
        args = fixture()
        queue = redirects.make_queue(*args)
        self.assertEqual(queue['redirect_source_count'], 6)
        self.assertEqual(queue['repository_count'], 3)
        self.assertEqual(len(queue['redirect_sources']), 6)
        self.assertFalse(queue['task_selection_authorized'])
        self.assertNotIn(b'example__', probe.encode(queue))
        wrong = copy.deepcopy(args[4])
        wrong['journal_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            redirects.make_queue(*args[:4], wrong)
        with self.assertRaises(ValueError):
            redirects.make_queue(*args[:3], args[3][:-1], args[4])

    def test_cumulative_budget_and_source_cooldown_block_requests(self):
        self.assertEqual(redirects.allowed_requests(0, 6), 6)
        self.assertEqual(redirects.allowed_requests(5, 6), 1)
        self.assertEqual(redirects.allowed_requests(6, 6), 0)
        with self.assertRaises(ValueError):
            redirects.allowed_requests(7, 1)
        now = time.time()
        source = [{'headers': {'x-ratelimit-remaining': '0',
                               'x-ratelimit-reset': str(int(now) + 600)},
                   'next_allowed_at_utc': None}]
        self.assertEqual(redirects.source_cooldown(source, now),
                         'source_rate_limit_window')
        source[0]['headers'] = {'x-ratelimit-remaining': '20'}
        source[0]['next_allowed_at_utc'] = datetime.fromtimestamp(
            now + 120, timezone.utc).isoformat()
        self.assertEqual(redirects.source_cooldown(source, now),
                         'source_retry_after_window')
        self.assertIsNone(redirects.source_cooldown(source, now + 121))

    def test_redirect_budget_lock_rejects_concurrent_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private-journal.jsonl'
            with redirects.redirect_budget_lock(path):
                with self.assertRaises(ValueError):
                    with redirects.redirect_budget_lock(path):
                        self.fail('second writer acquired the same budget')


if __name__ == '__main__':
    unittest.main()
