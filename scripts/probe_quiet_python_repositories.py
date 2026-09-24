"""Privately capture GitHub repository metadata for ancestry review.

This serial probe observes repository metadata only. It never selects tasks,
groups lineages, or attests independence. The queue and journal stay private.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

try:
    from scripts import rank_quiet_python_lineages as scoped
    from scripts.rank_confirm_candidates import outside_repository
except ModuleNotFoundError:
    import rank_quiet_python_lineages as scoped
    from rank_confirm_candidates import outside_repository


QUEUE_SCHEMA = 'solcodex.quiet-python-repository-queue.v1'
JOURNAL_SCHEMA = 'solcodex.quiet-python-repository-probe.v1'
API_VERSION = '2022-11-28'
API_ORIGIN = 'https://api.github.com'
USER_AGENT = 'SoLCodex-ancestry-research'
HEADER_NAMES = ('etag', 'x-ratelimit-limit', 'x-ratelimit-remaining',
                'x-ratelimit-reset', 'retry-after', 'location')
PILOT_REPOSITORY_LIMIT = 20
PILOT_HTTP_LIMIT = 40


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def create_private(path: Path, raw: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def require_private(path: Path):
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError('private queue or journal is missing or has unsafe permissions')


def make_queue(projection_bytes: bytes, aggregate: dict):
    projection, _ = scoped.verify_projection(projection_bytes, aggregate)
    repositories = sorted({row['repo'] for row in projection['candidates']})
    if len(repositories) != scoped.EXPECTED_REPOSITORY_COUNT:
        raise ValueError('Python repository queue does not cover the projected pool')
    return {'schema': QUEUE_SCHEMA,
            'projection_sha256': scoped.digest(projection_bytes),
            'repository_count': len(repositories),
            'order': 'lexicographic repository name',
            'task_selection_authorized': False,
            'repositories': repositories}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        return None


def summary(body: bytes, status: int):
    if status != 200:
        return None
    try:
        value = scoped.strict_json(body)
    except (ValueError, UnicodeDecodeError):
        return {'metadata_status': 'invalid_json'}
    if not isinstance(value, dict) or type(value.get('id')) is not int or \
            not isinstance(value.get('full_name'), str) or \
            type(value.get('fork')) is not bool:
        return {'metadata_status': 'invalid_schema'}
    parent = value.get('parent')
    source = value.get('source')
    return {'metadata_status': 'observed', 'repository_id': value['id'],
            'full_name': value['full_name'], 'fork': value['fork'],
            'parent_full_name': parent.get('full_name') if isinstance(parent, dict) else None,
            'source_full_name': source.get('full_name') if isinstance(source, dict) else None,
            'mirror_url': value.get('mirror_url'),
            'archived': value.get('archived'), 'disabled': value.get('disabled')}


def fetch_one(repository: str, index: int):
    url = API_ORIGIN + '/repos/' + '/'.join(quote(part, safe='')
                                                for part in repository.split('/'))
    opener = build_opener(NoRedirect())
    request = Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': API_VERSION,
        'User-Agent': USER_AGENT})
    try:
        response = opener.open(request, timeout=20)
    except HTTPError as error:
        response = error
    except (URLError, TimeoutError, ValueError) as error:
        return {'index': index, 'requested_repository': repository,
                'requested_url': url,
                'observed_at_utc': datetime.now(timezone.utc).isoformat(),
                'http_status': None, 'final_url': None,
                'headers': {}, 'body_sha256': None, 'body_base64': None,
                'summary': None, 'transport_error': type(error).__name__,
                'complete': False, 'next_allowed_at_utc': None}
    with response:
        body = response.read(2_000_001)
        status = response.status if hasattr(response, 'status') else response.code
        headers = {name: response.headers.get(name) for name in HEADER_NAMES
                   if response.headers.get(name) is not None}
        final_url = response.geturl()
        observed_at = datetime.now(timezone.utc).isoformat()
    if len(body) > 2_000_000:
        raise ValueError('GitHub API response exceeded the private capture limit')
    return {'index': index, 'requested_repository': repository,
            'requested_url': url, 'observed_at_utc': observed_at,
            'http_status': status, 'final_url': final_url,
            'headers': headers,
            'body_sha256': scoped.digest(body),
            'body_base64': base64.b64encode(body).decode('ascii'),
            'summary': summary(body, status), 'transport_error': None,
            'complete': (status == 200 and summary(body, status)['metadata_status'] == 'observed')
                        or status in (301, 302, 307, 308, 404, 410),
            'next_allowed_at_utc': None}


def read_journal(path: Path, queue_bytes: bytes, queue: dict):
    require_private(path)
    raw = path.read_bytes()
    if not raw.endswith(b'\n'):
        raise ValueError('partial private journal tail requires manual recovery')
    lines = raw.splitlines()
    if not lines or scoped.strict_json(lines[0]) != {
            'schema': JOURNAL_SCHEMA,
            'queue_sha256': scoped.digest(queue_bytes),
            'api_version': API_VERSION,
            'api_origin': API_ORIGIN,
            'pilot_repository_limit': PILOT_REPOSITORY_LIMIT,
            'pilot_http_limit': PILOT_HTTP_LIMIT}:
        raise ValueError('private probe journal has different queue or pilot limits')
    next_index, started_count = 0, 0
    attempts, pending = [], None
    for line in lines[1:]:
        event = scoped.strict_json(line)
        if not isinstance(event, dict):
            raise ValueError('invalid private probe event')
        if pending is None:
            if next_index >= min(len(queue['repositories']), PILOT_REPOSITORY_LIMIT) or \
                    started_count >= PILOT_HTTP_LIMIT or \
                    event.get('event') != 'start' or \
                    event.get('index') != next_index or \
                    event.get('requested_repository') != queue['repositories'][next_index]:
                raise ValueError('private probe journal is not a valid queue prefix')
            pending = event
            started_count += 1
            continue
        if event.get('event') != 'result' or \
                event.get('index') != pending['index'] or \
                event.get('requested_repository') != pending['requested_repository'] or \
                type(event.get('complete')) is not bool:
            raise ValueError('private probe result does not match its durable start')
        body64 = event.get('body_base64')
        try:
            body = base64.b64decode(body64, validate=True) if body64 is not None else None
        except (ValueError, TypeError) as error:
            raise ValueError('invalid private probe response encoding') from error
        if body is not None and event.get('body_sha256') != scoped.digest(body):
            raise ValueError('private probe response hash differs from body')
        status = event.get('http_status')
        expected_complete = ((status == 200 and body is not None and
                              summary(body, status)['metadata_status'] == 'observed') or
                             status in (301, 302, 307, 308, 404, 410))
        if event['complete'] != expected_complete or \
                event.get('summary') != (summary(body, status) if body is not None else None):
            raise ValueError('private probe result has inconsistent response state')
        attempts.append(event)
        if event['complete']:
            next_index += 1
        pending = None
    if pending is not None:
        raise ValueError('durable request start lacks response; manual recovery required')
    return next_index, attempts, started_count


def append_event(stream, event: dict):
    stream.seek(0, os.SEEK_END)
    stream.write(encode(event))
    stream.flush()
    os.fsync(stream.fileno())


def probe(queue: dict, journal_path: Path, queue_bytes: bytes,
          max_requests: int, pause_seconds: float = 1.0, fetch=fetch_one):
    require_private(journal_path)
    with journal_path.open('rb+') as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('private probe journal already has an active writer') from error
        next_index, attempts, started_count = read_journal(
            journal_path, queue_bytes, queue)
        target = min(len(queue['repositories']), PILOT_REPOSITORY_LIMIT)
        if next_index >= target:
            return {'attempts_this_run': 0, 'observed_queue_entry_count': next_index,
                    'stop_reason': 'pilot_complete'}
        if started_count >= PILOT_HTTP_LIMIT:
            return {'attempts_this_run': 0, 'observed_queue_entry_count': next_index,
                    'stop_reason': 'pilot_http_budget'}
        last = attempts[-1] if attempts else None
        if last:
            headers = last.get('headers') or {}
            reset = headers.get('x-ratelimit-reset')
            remaining = headers.get('x-ratelimit-remaining')
            if remaining is not None and remaining.isdigit() and int(remaining) <= 5 and \
                    reset is not None and reset.isdigit() and time.time() < int(reset):
                return {'attempts_this_run': 0, 'observed_queue_entry_count': next_index,
                        'stop_reason': 'rate_limit_window'}
            next_allowed = last.get('next_allowed_at_utc')
            if next_allowed and time.time() < datetime.fromisoformat(next_allowed).timestamp():
                return {'attempts_this_run': 0, 'observed_queue_entry_count': next_index,
                        'stop_reason': 'retry_after_window'}
        requests_made, stop_reason = 0, 'request_budget'
        while next_index < target and requests_made < max_requests and \
                started_count < PILOT_HTTP_LIMIT:
            if requests_made:
                time.sleep(pause_seconds)
            repository = queue['repositories'][next_index]
            append_event(stream, {
                'event': 'start', 'index': next_index,
                'requested_repository': repository,
                'started_at_utc': datetime.now(timezone.utc).isoformat()})
            started_count += 1
            record = fetch(repository, next_index)
            if record.get('index') != next_index or \
                    record.get('requested_repository') != repository:
                raise ValueError('probe response does not match durable request start')
            if record.get('http_status') in (403, 429):
                failures = 1
                for previous in reversed(attempts):
                    if previous.get('http_status') not in (403, 429):
                        break
                    failures += 1
                delay = min(3600, 60 * (2 ** min(failures - 1, 6)))
                headers = record.get('headers') or {}
                retry_after = headers.get('retry-after')
                if retry_after and retry_after.isdigit():
                    delay = max(delay, int(retry_after))
                reset = headers.get('x-ratelimit-reset')
                received = datetime.fromisoformat(record['observed_at_utc']).timestamp()
                next_allowed = received + delay
                if headers.get('x-ratelimit-remaining') == '0' and \
                        reset and reset.isdigit():
                    next_allowed = max(next_allowed, int(reset))
                record['next_allowed_at_utc'] = datetime.fromtimestamp(
                    next_allowed, timezone.utc).isoformat()
            record['event'] = 'result'
            append_event(stream, record)
            attempts.append(record)
            requests_made += 1
            if not record['complete']:
                stop_reason = 'incomplete_attempt'
                break
            next_index += 1
            remaining = record['headers'].get('x-ratelimit-remaining')
            if remaining is not None and remaining.isdigit() and int(remaining) <= 5:
                stop_reason = 'rate_budget_guard'
                break
        if next_index == target:
            stop_reason = 'pilot_complete'
        elif started_count == PILOT_HTTP_LIMIT:
            stop_reason = 'pilot_http_budget'
        return {'attempts_this_run': requests_made,
                'observed_queue_entry_count': next_index,
                'stop_reason': stop_reason}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projection', required=True, type=Path)
    parser.add_argument('--queue', required=True, type=Path)
    parser.add_argument('--journal', required=True, type=Path)
    parser.add_argument('--max-requests', type=int, default=20)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('private publication requires POSIX')
    if not 1 <= args.max_requests <= 45:
        parser.error('max requests must be between 1 and 45')
    for path in (args.projection, args.queue, args.journal):
        outside_repository(path)
    try:
        aggregate = scoped.strict_json(scoped.AGGREGATE_PATH.read_bytes())
        queue = make_queue(args.projection.read_bytes(), aggregate)
        queue_bytes = encode(queue)
        header = {'schema': JOURNAL_SCHEMA,
                  'queue_sha256': scoped.digest(queue_bytes),
                  'api_version': API_VERSION, 'api_origin': API_ORIGIN,
                  'pilot_repository_limit': PILOT_REPOSITORY_LIMIT,
                  'pilot_http_limit': PILOT_HTTP_LIMIT}
        if args.resume:
            require_private(args.queue)
            if args.queue.read_bytes() != queue_bytes:
                raise ValueError('private repository queue no longer matches the projection')
            require_private(args.journal)
        else:
            create_private(args.queue, queue_bytes)
            create_private(args.journal, encode(header))
        result = probe(queue, args.journal, queue_bytes, args.max_requests)
    except (OSError, ValueError) as error:
        parser.error(type(error).__name__ + ': private probe stopped; inspect its journal')
    print(json.dumps({**result, 'queue_repository_count': queue['repository_count'],
                      'task_selection_authorized': False}, sort_keys=True))


if __name__ == '__main__':
    main()
