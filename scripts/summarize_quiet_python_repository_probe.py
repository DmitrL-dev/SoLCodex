"""Publish only aggregate counts from the private repository metadata pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

try:
    from scripts import probe_quiet_python_repositories as probe
    from scripts import rank_quiet_python_lineages as scoped
    from scripts.rank_confirm_candidates import outside_repository
except ModuleNotFoundError:
    import probe_quiet_python_repositories as probe
    import rank_quiet_python_lineages as scoped
    from rank_confirm_candidates import outside_repository


PROBE_PATH = Path(__file__).with_name('probe_quiet_python_repositories.py')


def summarize(projection_bytes, queue_bytes, journal_bytes, queue, records, starts):
    statuses = {}
    for record in records:
        status = str(record['http_status'])
        statuses[status] = statuses.get(status, 0) + 1
    valid = [record for record in records if record['http_status'] == 200 and
             record['summary'] is not None and
             record['summary'].get('metadata_status') == 'observed']
    redirects = [record for record in records if record['http_status'] in
                 (301, 302, 307, 308)]
    return {
        'schema': 'solcodex.quiet-python-repository-probe-aggregate.v1',
        'projection_sha256': scoped.digest(projection_bytes),
        'queue_sha256': scoped.digest(queue_bytes),
        'journal_sha256': scoped.digest(journal_bytes),
        'probe_code_sha256': scoped.digest(PROBE_PATH.read_bytes()),
        'summarizer_code_sha256': scoped.digest(Path(__file__).read_bytes()),
        'queue_repository_count': queue['repository_count'],
        'pilot_repository_limit': probe.PILOT_REPOSITORY_LIMIT,
        'pilot_http_limit': probe.PILOT_HTTP_LIMIT,
        'pilot_order': queue['order'],
        'observed_queue_entry_count': sum(record['complete'] for record in records),
        'http_attempt_count': starts,
        'response_status_counts': statuses,
        'valid_200_metadata_count': len(valid),
        'redirect_observed_count': len(redirects),
        'redirect_with_location_count': sum(bool(record['headers'].get('location'))
                                            for record in redirects),
        'redirect_to_api_github_count': sum(
            urlparse(record['headers'].get('location') or '').scheme == 'https' and
            urlparse(record['headers'].get('location') or '').hostname == 'api.github.com'
            for record in redirects),
        'fork_true_count': sum(record['summary']['fork'] for record in valid),
        'parent_present_count': sum(record['summary']['parent_full_name'] is not None
                                    for record in valid),
        'source_present_count': sum(record['summary']['source_full_name'] is not None
                                    for record in valid),
        'exact_spelling_difference_count': sum(
            record['requested_repository'] != record['summary']['full_name']
            for record in valid),
        'casefold_name_difference_count': sum(
            record['requested_repository'].casefold() !=
            record['summary']['full_name'].casefold() for record in valid),
        'without_valid_200_metadata_count': sum(record['complete'] for record in records) - len(valid),
        'ancestry_independently_verified': False,
        'sample_selected': False,
        'model_run_authorized': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projection', required=True, type=Path)
    parser.add_argument('--queue', required=True, type=Path)
    parser.add_argument('--journal', required=True, type=Path)
    args = parser.parse_args()
    for path in (args.projection, args.queue, args.journal):
        outside_repository(path)
    try:
        probe.require_private(args.queue)
        probe.require_private(args.journal)
        projection_bytes = args.projection.read_bytes()
        aggregate = scoped.strict_json(scoped.AGGREGATE_PATH.read_bytes())
        expected_queue_bytes = probe.encode(probe.make_queue(projection_bytes, aggregate))
        queue_bytes = args.queue.read_bytes()
        if queue_bytes != expected_queue_bytes:
            raise ValueError('private repository queue differs from pinned projection')
        queue = scoped.strict_json(queue_bytes)
        journal_bytes = args.journal.read_bytes()
        _, records, starts = probe.read_journal(args.journal, queue_bytes, queue)
        result = summarize(projection_bytes, queue_bytes, journal_bytes,
                           queue, records, starts)
    except (OSError, ValueError) as error:
        parser.error(type(error).__name__ + ': private aggregate verification failed')
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
