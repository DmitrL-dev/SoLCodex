"""Publish only aggregate counts from the private redirect-target probe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from scripts import probe_quiet_python_redirect_targets as redirects
    from scripts import probe_quiet_python_repositories as probe
    from scripts import rank_quiet_python_lineages as scoped
    from scripts.rank_confirm_candidates import outside_repository
except ModuleNotFoundError:
    import probe_quiet_python_redirect_targets as redirects
    import probe_quiet_python_repositories as probe
    import rank_quiet_python_lineages as scoped
    from rank_confirm_candidates import outside_repository


REDIRECT_PROBE_PATH = Path(__file__).with_name('probe_quiet_python_redirect_targets.py')
BASE_PROBE_PATH = Path(__file__).with_name('probe_quiet_python_repositories.py')


def summarize(source_queue_bytes, source_journal_bytes, target_queue_bytes,
              target_journal_bytes, queue, records, started_count,
              source_aggregate_bytes):
    observed = [record for record in records if record['http_status'] == 200 and
                isinstance(record['summary'], dict) and
                record['summary'].get('metadata_status') == 'observed']
    by_endpoint = {record['requested_repository']: record['summary']
                   for record in observed}
    status_counts = {}
    for record in records:
        status = str(record['http_status'])
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        'schema': 'solcodex.quiet-python-redirect-probe-aggregate.v1',
        'source_pilot_aggregate_sha256': scoped.digest(source_aggregate_bytes),
        'source_queue_sha256': scoped.digest(source_queue_bytes),
        'source_journal_sha256': scoped.digest(source_journal_bytes),
        'target_queue_sha256': scoped.digest(target_queue_bytes),
        'target_journal_sha256': scoped.digest(target_journal_bytes),
        'base_probe_code_sha256': scoped.digest(BASE_PROBE_PATH.read_bytes()),
        'redirect_probe_code_sha256': scoped.digest(REDIRECT_PROBE_PATH.read_bytes()),
        'summarizer_code_sha256': scoped.digest(Path(__file__).read_bytes()),
        'source_redirect_count': queue['redirect_source_count'],
        'unique_target_id_endpoint_count': queue['repository_count'],
        'redirect_http_limit': queue['redirect_http_limit'],
        'target_http_attempt_count': started_count,
        'target_observed_queue_entry_count': sum(record['complete'] for record in records),
        'target_response_status_counts': status_counts,
        'valid_200_target_metadata_count': len(observed),
        'response_id_matches_requested_id_count': sum(
            str(record['summary']['repository_id']) ==
            record['requested_repository'].split('/')[1] for record in observed),
        'distinct_current_full_name_count': len({
            value['full_name'].casefold() for value in by_endpoint.values()}),
        'source_name_differs_from_current_count': sum(
            item['source_repository'].casefold() !=
            by_endpoint[item['target_endpoint']]['full_name'].casefold()
            for item in queue['redirect_sources']
            if item['target_endpoint'] in by_endpoint),
        'fork_true_count': sum(value['fork'] for value in by_endpoint.values()),
        'parent_present_count': sum(value['parent_full_name'] is not None
                                    for value in by_endpoint.values()),
        'source_present_count': sum(value['source_full_name'] is not None
                                    for value in by_endpoint.values()),
        'without_valid_200_target_metadata_count': sum(
            record['complete'] for record in records) - len(observed),
        'ancestry_independently_verified': False,
        'sample_selected': False,
        'model_run_authorized': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-queue', required=True, type=Path)
    parser.add_argument('--source-journal', required=True, type=Path)
    parser.add_argument('--target-queue', required=True, type=Path)
    parser.add_argument('--target-journal', required=True, type=Path)
    args = parser.parse_args()
    for path in (args.source_queue, args.source_journal,
                 args.target_queue, args.target_journal):
        outside_repository(path)
    try:
        for path in (args.source_queue, args.source_journal,
                     args.target_queue, args.target_journal):
            probe.require_private(path)
        source_queue_bytes = args.source_queue.read_bytes()
        source_journal_bytes = args.source_journal.read_bytes()
        source_queue = scoped.strict_json(source_queue_bytes)
        _, source_records, _ = probe.read_journal(
            args.source_journal, source_queue_bytes, source_queue)
        source_aggregate_bytes = redirects.PILOT_AGGREGATE_PATH.read_bytes()
        expected_queue = redirects.make_queue(
            source_queue_bytes, source_journal_bytes, source_queue,
            source_records, scoped.strict_json(source_aggregate_bytes))
        target_queue_bytes = args.target_queue.read_bytes()
        if target_queue_bytes != probe.encode(expected_queue):
            raise ValueError('target queue differs from pinned redirect source')
        target_journal_bytes = args.target_journal.read_bytes()
        _, target_records, started_count = probe.read_journal(
            args.target_journal, target_queue_bytes, expected_queue)
        if started_count > redirects.REDIRECT_HTTP_LIMIT:
            raise ValueError('target journal exceeds redirect HTTP budget')
        result = summarize(source_queue_bytes, source_journal_bytes,
                           target_queue_bytes, target_journal_bytes,
                           expected_queue, target_records, started_count,
                           source_aggregate_bytes)
    except (OSError, ValueError) as error:
        parser.error(type(error).__name__ + ': private redirect aggregate verification failed')
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
