"""Privately capture one explicit hop for pinned repository API redirects.

Only redirects in the published first-20 pilot are eligible. A target response
is metadata evidence, not lineage approval or task selection.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

try:
    from scripts import probe_quiet_python_repositories as probe
    from scripts import rank_quiet_python_lineages as scoped
    from scripts.rank_confirm_candidates import outside_repository
except ModuleNotFoundError:
    import probe_quiet_python_repositories as probe
    import rank_quiet_python_lineages as scoped
    from rank_confirm_candidates import outside_repository


ROOT = Path(__file__).resolve().parent.parent
PILOT_AGGREGATE_PATH = ROOT / 'docs/measurements/data/2026-09-25-quiet-python-repository-probe-pilot.json'
QUEUE_SCHEMA = 'solcodex.quiet-python-redirect-target-queue.v1'
TARGET = re.compile(r'[1-9][0-9]*\Z')
REDIRECT_HTTP_LIMIT = 6


def target_from_location(location):
    if not isinstance(location, str):
        raise ValueError('redirect has no location')
    url = urlsplit(location)
    parts = url.path.split('/')
    if url.scheme != 'https' or url.netloc != 'api.github.com' or \
            url.query or url.fragment or len(parts) != 3 or \
            parts[:2] != ['', 'repositories'] or \
            not TARGET.fullmatch(parts[2]):
        raise ValueError('redirect target is not a pinned GitHub repository ID endpoint')
    return 'repositories/' + parts[2]


def fetch_target(endpoint, index):
    if not isinstance(endpoint, str) or not endpoint.startswith('repositories/') or \
            not TARGET.fullmatch(endpoint.removeprefix('repositories/')):
        raise ValueError('invalid pinned repository ID endpoint')
    url = probe.API_ORIGIN + '/' + endpoint
    request = Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': probe.API_VERSION,
        'User-Agent': probe.USER_AGENT})
    try:
        response = build_opener(probe.NoRedirect()).open(request, timeout=20)
    except HTTPError as error:
        response = error
    except (URLError, TimeoutError, ValueError) as error:
        return {'index': index, 'requested_repository': endpoint,
                'requested_url': url,
                'observed_at_utc': datetime.now(timezone.utc).isoformat(),
                'http_status': None, 'final_url': None,
                'headers': {}, 'body_sha256': None, 'body_base64': None,
                'summary': None, 'transport_error': type(error).__name__,
                'complete': False, 'next_allowed_at_utc': None}
    with response:
        body = response.read(2_000_001)
        status = response.status if hasattr(response, 'status') else response.code
        headers = {name: response.headers.get(name) for name in probe.HEADER_NAMES
                   if response.headers.get(name) is not None}
        final_url = response.geturl()
        observed_at = datetime.now(timezone.utc).isoformat()
    if len(body) > 2_000_000:
        raise ValueError('GitHub API response exceeded the private capture limit')
    metadata = probe.summary(body, status)
    return {'index': index, 'requested_repository': endpoint,
            'requested_url': url, 'observed_at_utc': observed_at,
            'http_status': status, 'final_url': final_url,
            'headers': headers,
            'body_sha256': scoped.digest(body),
            'body_base64': base64.b64encode(body).decode('ascii'),
            'summary': metadata, 'transport_error': None,
            'complete': (status == 200 and metadata['metadata_status'] == 'observed')
                        or status in (301, 302, 307, 308, 404, 410),
            'next_allowed_at_utc': None}


def make_queue(source_queue_bytes, source_journal_bytes, source_queue, records,
               pilot_aggregate):
    if not isinstance(pilot_aggregate, dict) or \
            pilot_aggregate.get('schema') != \
            'solcodex.quiet-python-repository-probe-aggregate.v1' or \
            pilot_aggregate.get('queue_sha256') != scoped.digest(source_queue_bytes) or \
            pilot_aggregate.get('journal_sha256') != scoped.digest(source_journal_bytes) or \
            pilot_aggregate.get('observed_queue_entry_count') != 20 or \
            pilot_aggregate.get('redirect_observed_count') != 6 or \
            source_queue.get('schema') != probe.QUEUE_SCHEMA:
        raise ValueError('source pilot does not match published queue and journal pins')
    sources = []
    for record in records:
        if record['http_status'] not in (301, 302, 307, 308):
            continue
        sources.append({'source_index': record['index'],
                        'source_repository': record['requested_repository'],
                        'target_endpoint': target_from_location(
                            record['headers'].get('location'))})
    if len(sources) != 6:
        raise ValueError('source pilot redirect count changed')
    repositories = sorted({item['target_endpoint'] for item in sources})
    return {'schema': QUEUE_SCHEMA,
            'source_queue_sha256': scoped.digest(source_queue_bytes),
            'source_journal_sha256': scoped.digest(source_journal_bytes),
            'repository_count': len(repositories),
            'redirect_source_count': len(sources),
            'redirect_http_limit': REDIRECT_HTTP_LIMIT,
            'order': 'lexicographic repository ID endpoint',
            'task_selection_authorized': False,
            'repositories': repositories,
            'redirect_sources': sources}


def source_cooldown(records, now=None):
    if not records:
        return None
    now = time.time() if now is None else now
    last = records[-1]
    headers = last.get('headers') or {}
    remaining = headers.get('x-ratelimit-remaining')
    reset = headers.get('x-ratelimit-reset')
    if remaining is not None and remaining.isdigit() and int(remaining) <= 5 and \
            reset is not None and reset.isdigit() and now < int(reset):
        return 'source_rate_limit_window'
    next_allowed = last.get('next_allowed_at_utc')
    if next_allowed and now < datetime.fromisoformat(next_allowed).timestamp():
        return 'source_retry_after_window'
    return None


def allowed_requests(started_count, requested):
    if type(started_count) is not int or started_count < 0 or \
            started_count > REDIRECT_HTTP_LIMIT:
        raise ValueError('redirect journal exceeds the fixed HTTP budget')
    return min(requested, REDIRECT_HTTP_LIMIT - started_count)


@contextmanager
def redirect_budget_lock(journal_path):
    lock_path = journal_path.with_name(journal_path.name + '.budget-lock')
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('redirect probe already has an active writer') from error
        yield
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-queue', required=True, type=Path)
    parser.add_argument('--source-journal', required=True, type=Path)
    parser.add_argument('--queue', required=True, type=Path)
    parser.add_argument('--journal', required=True, type=Path)
    parser.add_argument('--max-requests', type=int, default=6)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('private publication requires POSIX')
    if not 1 <= args.max_requests <= 6:
        parser.error('redirect probe allows at most six requests per run')
    for path in (args.source_queue, args.source_journal, args.queue, args.journal):
        outside_repository(path)
    try:
        probe.require_private(args.source_queue)
        probe.require_private(args.source_journal)
        source_queue_bytes = args.source_queue.read_bytes()
        source_journal_bytes = args.source_journal.read_bytes()
        source_queue = scoped.strict_json(source_queue_bytes)
        _, records, _ = probe.read_journal(args.source_journal,
                                            source_queue_bytes, source_queue)
        queue = make_queue(source_queue_bytes, source_journal_bytes,
                           source_queue, records,
                           scoped.strict_json(PILOT_AGGREGATE_PATH.read_bytes()))
        queue_bytes = probe.encode(queue)
        header = {'schema': probe.JOURNAL_SCHEMA,
                  'queue_sha256': scoped.digest(queue_bytes),
                  'api_version': probe.API_VERSION,
                  'api_origin': probe.API_ORIGIN,
                  'pilot_repository_limit': probe.PILOT_REPOSITORY_LIMIT,
                  'pilot_http_limit': probe.PILOT_HTTP_LIMIT}
        if args.resume:
            probe.require_private(args.queue)
            if args.queue.read_bytes() != queue_bytes:
                raise ValueError('redirect target queue differs from pinned source')
            probe.require_private(args.journal)
        else:
            probe.create_private(args.queue, queue_bytes)
            probe.create_private(args.journal, probe.encode(header))
        with redirect_budget_lock(args.journal):
            observed, _, started_count = probe.read_journal(
                args.journal, queue_bytes, queue)
            budget = allowed_requests(started_count, args.max_requests)
            source_stop = source_cooldown(records)
            if source_stop is not None or budget == 0:
                result = {'attempts_this_run': 0,
                          'observed_queue_entry_count': observed,
                          'stop_reason': source_stop or 'redirect_http_budget'}
            else:
                result = probe.probe(queue, args.journal, queue_bytes,
                                     budget, fetch=fetch_target)
    except (OSError, ValueError) as error:
        parser.error(type(error).__name__ + ': private redirect probe stopped')
    print(json.dumps({**result, 'redirect_source_count': queue['redirect_source_count'],
                      'target_endpoint_count': queue['repository_count'],
                      'task_selection_authorized': False}, sort_keys=True))


if __name__ == '__main__':
    main()
