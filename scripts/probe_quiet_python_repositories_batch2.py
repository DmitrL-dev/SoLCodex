"""Capture the next 40 private Python repository names for ancestry review.

The first 20 must match the published pilot. This probe does not rank, select,
or approve any task. Prepare and publish the controller before execute mode.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

try:
    from scripts import probe_quiet_python_repositories as base
    from scripts import rank_quiet_python_lineages as scoped
    from scripts.rank_confirm_candidates import outside_repository
except ModuleNotFoundError:
    import probe_quiet_python_repositories as base
    import rank_quiet_python_lineages as scoped
    from rank_confirm_candidates import outside_repository


START = 20
COUNT = 40
HTTP_LIMIT = 48
FIRST_QUEUE_SHA256 = '0db6eb7e2a8c805357b5ab45bb5d22adbb88babaef4a46cad6b1be4f2002823c'
FIRST_JOURNAL_SHA256 = '8f5483e7c15595217c22620571ff50cf51017c2a9d9856afa7f3b8ffa83c1967'
BASE_SHA256 = '1009199b4e8bdb5ab53ea9335a9cbdbb9b8f811c07e888e2b8a3de9deda0b35f'
SCOPED_SHA256 = 'ff31c4577f2ef6c01592e94a4d395bb4d4e843c82f04e97cba59709b4d2928b0'
JOURNAL_SCHEMA = 'solcodex.quiet-python-repository-batch2-probe.v1'
QUEUE_SCHEMA = 'solcodex.quiet-python-repository-batch2-queue.v1'


def verified_queue(projection: Path, prior_journal: Path):
    if scoped.digest(Path(base.__file__).read_bytes()) != BASE_SHA256 or \
            scoped.digest(Path(scoped.__file__).read_bytes()) != SCOPED_SHA256:
        raise ValueError('source code differs from the frozen batch plan')
    aggregate = scoped.strict_json(scoped.AGGREGATE_PATH.read_bytes())
    full = base.make_queue(projection.read_bytes(), aggregate)
    full_bytes = base.encode(full)
    base.require_private(prior_journal)
    if scoped.digest(full_bytes) != FIRST_QUEUE_SHA256 or \
            scoped.digest(prior_journal.read_bytes()) != FIRST_JOURNAL_SHA256:
        raise ValueError('first pilot differs from published queue or journal')
    first_count, _, first_attempts = base.read_journal(prior_journal, full_bytes, full)
    if first_count != START or first_attempts != START:
        raise ValueError('first pilot is not a complete 20-entry prefix')
    subset = {
        'schema': QUEUE_SCHEMA,
        'source_queue_sha256': FIRST_QUEUE_SHA256,
        'projection_sha256': scoped.digest(projection.read_bytes()),
        'start_index': START,
        'repository_count': COUNT,
        'order': 'lexicographic repository name, indices 20 through 59',
        'task_selection_authorized': False,
        'repositories': full['repositories'][START:START + COUNT],
    }
    if len(subset['repositories']) != COUNT or \
            len(set(subset['repositories'])) != COUNT:
        raise ValueError('invalid second batch slice')
    return subset, base.encode(subset)


def configure_probe():
    # This process is dedicated to one batch. The original pilot module stays
    # byte-identical for replaying its already published result.
    base.PILOT_REPOSITORY_LIMIT = COUNT
    base.PILOT_HTTP_LIMIT = HTTP_LIMIT
    base.JOURNAL_SCHEMA = JOURNAL_SCHEMA


def header(queue_bytes):
    return {'schema': JOURNAL_SCHEMA,
            'queue_sha256': scoped.digest(queue_bytes),
            'api_version': base.API_VERSION,
            'api_origin': base.API_ORIGIN,
            'pilot_repository_limit': COUNT,
            'pilot_http_limit': HTTP_LIMIT}


def summarize(queue, queue_bytes, journal: Path, projection: Path):
    next_index, records, attempts = base.read_journal(journal, queue_bytes, queue)
    statuses = {}
    for record in records:
        key = str(record['http_status'])
        statuses[key] = statuses.get(key, 0) + 1
    valid = [record for record in records if record['http_status'] == 200 and
             record['summary'] and record['summary'].get('metadata_status') == 'observed']
    redirects = [record for record in records if record['http_status'] in
                 (301, 302, 307, 308)]
    return {'schema': 'solcodex.quiet-python-repository-batch2-aggregate.v1',
            'first_batch_repository_count': START,
            'batch_start_index': START, 'batch_repository_count': COUNT,
            'observed_batch_entry_count': next_index,
            'total_observed_prefix_count': START + next_index,
            'http_attempt_count': attempts,
            'response_status_counts': statuses,
            'valid_200_metadata_count': len(valid),
            'redirect_observed_count': len(redirects),
            'redirect_to_api_github_count': sum(
                urlparse(r['headers'].get('location') or '').scheme == 'https' and
                urlparse(r['headers'].get('location') or '').hostname == 'api.github.com'
                for r in redirects),
            'fork_true_count': sum(r['summary']['fork'] for r in valid),
            'parent_present_count': sum(r['summary']['parent_full_name'] is not None
                                        for r in valid),
            'source_present_count': sum(r['summary']['source_full_name'] is not None
                                        for r in valid),
            'casefold_name_difference_count': sum(
                r['requested_repository'].casefold() !=
                r['summary']['full_name'].casefold() for r in valid),
            'projection_sha256': scoped.digest(projection.read_bytes()),
            'queue_sha256': scoped.digest(queue_bytes),
            'journal_sha256': scoped.digest(journal.read_bytes()),
            'batch_code_sha256': scoped.digest(Path(__file__).read_bytes()),
            'base_probe_code_sha256': BASE_SHA256,
            'ancestry_independently_verified': False,
            'sample_selected': False, 'model_run_authorized': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projection', required=True, type=Path)
    parser.add_argument('--prior-journal', required=True, type=Path)
    parser.add_argument('--queue', required=True, type=Path)
    parser.add_argument('--journal', required=True, type=Path)
    parser.add_argument('--mode', required=True,
                        choices=('prepare', 'execute', 'summarize'))
    parser.add_argument('--max-requests', type=int, default=COUNT)
    args = parser.parse_args()
    if os.name != 'posix' or not 1 <= args.max_requests <= COUNT:
        parser.error('POSIX and a 1..40 request cap are required')
    for path in (args.projection, args.prior_journal, args.queue, args.journal):
        outside_repository(path)
    try:
        queue, queue_bytes = verified_queue(args.projection, args.prior_journal)
        configure_probe()
        if args.mode == 'prepare':
            base.create_private(args.queue, queue_bytes)
            base.create_private(args.journal, base.encode(header(queue_bytes)))
            result = {'prepared': True, 'batch_start_index': START,
                      'batch_repository_count': COUNT,
                      'queue_sha256': scoped.digest(queue_bytes),
                      'http_attempts': 0}
        else:
            base.require_private(args.queue)
            base.require_private(args.journal)
            if args.queue.read_bytes() != queue_bytes:
                raise ValueError('second batch queue differs from pinned projection')
            if args.mode == 'execute':
                result = base.probe(queue, args.journal, queue_bytes,
                                    args.max_requests)
            else:
                result = summarize(queue, queue_bytes, args.journal,
                                   args.projection)
    except (OSError, ValueError) as error:
        parser.error(type(error).__name__ + ': private batch stopped; inspect journal')
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
