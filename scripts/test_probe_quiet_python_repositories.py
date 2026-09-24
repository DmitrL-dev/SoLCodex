"""Synthetic checks for private, resumable repository metadata probes."""

import base64
from datetime import datetime, timezone
from email.message import Message
import fcntl
import io
import json
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest import mock
from urllib.error import HTTPError

from scripts import probe_quiet_python_repositories as probe
from scripts import rank_quiet_python_lineages as scoped
from scripts.test_rank_quiet_python_lineages import synthetic_projection


def queue_fixture():
    return {'schema': probe.QUEUE_SCHEMA, 'projection_sha256': 'a' * 64,
            'repository_count': 2, 'order': 'lexicographic repository name',
            'task_selection_authorized': False,
            'repositories': ['example/alpha', 'example/beta']}


def response(repository, index, remaining='40', status=200):
    body = json.dumps({'id': index + 1, 'full_name': repository, 'fork': False})
    return {'index': index, 'requested_repository': repository,
            'requested_url': f'https://api.github.com/repos/{repository}',
            'observed_at_utc': datetime.now(timezone.utc).isoformat(),
            'http_status': status,
            'final_url': f'https://api.github.com/repos/{repository}',
            'headers': {'x-ratelimit-remaining': remaining,
                        'x-ratelimit-reset': str(int(time.time()) + 3600)},
            'body_sha256': scoped.digest(body.encode()),
            'body_base64': base64.b64encode(body.encode()).decode(),
            'summary': probe.summary(body.encode(), status),
            'transport_error': None, 'complete': status == 200,
            'next_allowed_at_utc': None}


def journal_header(queue_bytes):
    return {'schema': probe.JOURNAL_SCHEMA,
            'queue_sha256': scoped.digest(queue_bytes),
            'api_version': probe.API_VERSION,
            'api_origin': probe.API_ORIGIN,
            'pilot_repository_limit': probe.PILOT_REPOSITORY_LIMIT,
            'pilot_http_limit': probe.PILOT_HTTP_LIMIT}


class QuietPythonRepositoryProbeTests(unittest.TestCase):
    def test_queue_contains_only_lexical_repository_names(self):
        projection_bytes, aggregate = synthetic_projection()
        with mock.patch.object(scoped, 'EXPECTED_SOURCE_COUNT', 3), \
             mock.patch.object(scoped, 'EXPECTED_CANDIDATE_COUNT', 2), \
             mock.patch.object(scoped, 'EXPECTED_REPOSITORY_COUNT', 1):
            queue = probe.make_queue(projection_bytes, aggregate)
        self.assertEqual(queue['repository_count'], 1)
        self.assertEqual(queue['repositories'], ['a/repo'])
        self.assertFalse(queue['task_selection_authorized'])
        self.assertNotIn(b'a__repo', probe.encode(queue))
        with self.assertRaises(ValueError):
            probe.make_queue(projection_bytes,
                             {**aggregate, 'private_projection_sha256': '0' * 64})

    def test_private_journal_resumes_exact_prefix_and_rejects_tamper(self):
        queue = queue_fixture()
        queue_bytes = probe.encode(queue)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.jsonl'
            probe.create_private(path, probe.encode(journal_header(queue_bytes)))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            called = []

            def fetch(repository, index):
                called.append(index)
                return response(repository, index)

            first = probe.probe(queue, path, queue_bytes, 1, pause_seconds=0, fetch=fetch)
            self.assertEqual(first['observed_queue_entry_count'], 1)
            second = probe.probe(queue, path, queue_bytes, 1, pause_seconds=0, fetch=fetch)
            self.assertEqual(second['observed_queue_entry_count'], 2)
            self.assertEqual(called, [0, 1])
            self.assertEqual(probe.read_journal(path, queue_bytes, queue)[0], 2)
            with self.assertRaises(ValueError):
                probe.read_journal(path, probe.encode({**queue, 'order': 'changed'}), queue)
            with path.open('ab') as stream:
                stream.write(probe.encode(response('example/beta', 1)))
            with self.assertRaises(ValueError):
                probe.read_journal(path, queue_bytes, queue)

    def test_rate_guard_stops_and_resume_obeys_reset(self):
        queue = queue_fixture()
        queue_bytes = probe.encode(queue)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.jsonl'
            probe.create_private(path, probe.encode(journal_header(queue_bytes)))
            first = probe.probe(queue, path, queue_bytes, 2, pause_seconds=0,
                                fetch=lambda name, index: response(name, index,
                                                                   remaining='5'))
            self.assertEqual(first['attempts_this_run'], 1)
            self.assertEqual(first['stop_reason'], 'rate_budget_guard')
            second = probe.probe(queue, path, queue_bytes, 2, pause_seconds=0,
                                 fetch=lambda *_: self.fail('rate-limited request sent'))
            self.assertEqual(second['attempts_this_run'], 0)
            self.assertEqual(second['stop_reason'], 'rate_limit_window')

    def test_fork_fields_are_evidence_not_independence(self):
        body = json.dumps({'id': 42, 'full_name': 'example/fork', 'fork': True,
                           'parent': {'full_name': 'example/parent'},
                           'source': {'full_name': 'example/source'},
                           'mirror_url': None}).encode()
        result = probe.summary(body, 200)
        self.assertEqual(result['parent_full_name'], 'example/parent')
        self.assertEqual(result['source_full_name'], 'example/source')
        self.assertIsNone(probe.summary(body, 404))

    def test_pilot_cap_is_cumulative_across_resume(self):
        names = [f'example/repo{index:02d}' for index in range(21)]
        queue = {**queue_fixture(), 'repository_count': 21, 'repositories': names}
        queue_bytes = probe.encode(queue)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.jsonl'
            probe.create_private(path, probe.encode(journal_header(queue_bytes)))
            called = []

            def fetch(name, index):
                called.append(index)
                return response(name, index)

            first = probe.probe(queue, path, queue_bytes, 45,
                                pause_seconds=0, fetch=fetch)
            self.assertEqual(first['observed_queue_entry_count'], 20)
            self.assertEqual(first['stop_reason'], 'pilot_complete')
            second = probe.probe(queue, path, queue_bytes, 45,
                                 pause_seconds=0, fetch=fetch)
            self.assertEqual(second['attempts_this_run'], 0)
            self.assertEqual(called, list(range(20)))

    def test_inflight_partial_tail_and_concurrent_writer_fail_closed(self):
        queue = queue_fixture()
        queue_bytes = probe.encode(queue)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.jsonl'
            probe.create_private(path, probe.encode(journal_header(queue_bytes)))
            with path.open('rb+') as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(ValueError):
                    probe.probe(queue, path, queue_bytes, 1,
                                fetch=lambda *_: self.fail('concurrent request'))
            with path.open('ab') as stream:
                stream.write(probe.encode({'event': 'start', 'index': 0,
                    'requested_repository': 'example/alpha',
                    'started_at_utc': datetime.now(timezone.utc).isoformat()}))
            with self.assertRaises(ValueError):
                probe.probe(queue, path, queue_bytes, 1,
                            fetch=lambda *_: self.fail('unanswered request retried'))
            with path.open('ab') as stream:
                stream.write(b'{"partial":')
            with self.assertRaises(ValueError):
                probe.read_journal(path, queue_bytes, queue)

    def test_malformed_200_and_redirect_are_not_followed_or_accepted(self):
        class FakeResponse:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _):
                return b'not json'

            def geturl(self):
                return 'https://api.github.com/repos/example/alpha'

        class FakeOpener:
            calls = 0

            def open(self, request, timeout):
                self.calls += 1
                return FakeResponse()

        opener = FakeOpener()
        with mock.patch.object(probe, 'build_opener', return_value=opener):
            record = probe.fetch_one('example/alpha', 0)
        self.assertEqual(opener.calls, 1)
        self.assertFalse(record['complete'])
        self.assertEqual(record['summary']['metadata_status'], 'invalid_json')
        self.assertIsNone(probe.NoRedirect().redirect_request(
            None, None, 302, 'Found', {},
            'https://api.github.com/repos/example/new'))
        headers = Message()
        headers['Location'] = 'https://api.github.com/repos/example/new'
        redirect = HTTPError('https://api.github.com/repos/example/alpha',
                             301, 'Moved', headers, io.BytesIO(b''))
        with mock.patch.object(probe, 'build_opener', return_value=mock.Mock(
                open=mock.Mock(side_effect=redirect))):
            record = probe.fetch_one('example/alpha', 0)
        self.assertEqual(record['http_status'], 301)
        self.assertEqual(record['headers']['location'],
                         'https://api.github.com/repos/example/new')
        self.assertTrue(record['complete'])

    def test_secondary_limit_backoff_starts_after_response(self):
        queue = queue_fixture()
        queue_bytes = probe.encode(queue)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.jsonl'
            probe.create_private(path, probe.encode(journal_header(queue_bytes)))
            first = probe.probe(queue, path, queue_bytes, 1, pause_seconds=0,
                                fetch=lambda name, index: response(name, index,
                                                                   remaining='10', status=429))
            self.assertEqual(first['stop_reason'], 'incomplete_attempt')
            _, attempts, starts = probe.read_journal(path, queue_bytes, queue)
            self.assertEqual(starts, 1)
            received = datetime.fromisoformat(attempts[-1]['observed_at_utc']).timestamp()
            allowed = datetime.fromisoformat(attempts[-1]['next_allowed_at_utc']).timestamp()
            self.assertGreaterEqual(allowed - received, 60)
            second = probe.probe(queue, path, queue_bytes, 1, pause_seconds=0,
                                 fetch=lambda *_: self.fail('cooldown ignored'))
            self.assertEqual(second['stop_reason'], 'retry_after_window')


if __name__ == '__main__':
    unittest.main()
