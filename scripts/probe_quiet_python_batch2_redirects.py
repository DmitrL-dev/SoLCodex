"""Capture four distinct API destinations from five pinned batch-two redirects.

Prepare a private queue and journal, publish their hashes, then execute. This
collects metadata only; it does not approve repository ancestry or tasks.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    from scripts import probe_quiet_python_repositories as base
    from scripts import probe_quiet_python_repositories_batch2 as batch2
    from scripts import probe_quiet_python_redirect_targets as redirect
    from scripts import rank_quiet_python_lineages as scoped
    from scripts.rank_confirm_candidates import outside_repository
except ModuleNotFoundError:
    import probe_quiet_python_repositories as base
    import probe_quiet_python_repositories_batch2 as batch2
    import probe_quiet_python_redirect_targets as redirect
    import rank_quiet_python_lineages as scoped
    from rank_confirm_candidates import outside_repository


SOURCE_JOURNAL_SHA256 = '4f4e2fea0f74763bd7a1279c10a6082281284c806faf9f674e655750eff786e0'
SOURCE_CONTROLLER_SHA256 = 'dab7e6ac7930eaf917452a9b0d0769c190b59bdf45b1c42ef0926b325222b5fe'
REDIRECT_HELPER_SHA256 = '1baf07a2d738fb7fe6b7b2f9aad7588c10b97c1ed5ec0f072a77e5b84a295d6b'
SOURCE_AGGREGATE = (Path(__file__).resolve().parent.parent /
                    'docs/measurements/data/2026-09-25-quiet-python-repository-batch2.json')
QUEUE_SCHEMA = 'solcodex.quiet-python-batch2-redirect-queue.v1'
JOURNAL_SCHEMA = 'solcodex.quiet-python-batch2-redirect-journal.v1'
SOURCE_COUNT = 5
TARGET_COUNT = 4


def source(projection, prior_journal, source_queue, source_journal):
    if scoped.digest(Path(batch2.__file__).read_bytes()) != SOURCE_CONTROLLER_SHA256 or \
            scoped.digest(Path(redirect.__file__).read_bytes()) != REDIRECT_HELPER_SHA256:
        raise ValueError('source controller or redirect helper differs from freeze')
    expected_queue, queue_bytes = batch2.verified_queue(projection, prior_journal)
    base.require_private(source_queue)
    base.require_private(source_journal)
    if source_queue.read_bytes() != queue_bytes or \
            scoped.digest(source_journal.read_bytes()) != SOURCE_JOURNAL_SHA256:
        raise ValueError('source batch queue or journal differs from published result')
    batch2.configure_probe()
    observed, records, attempts = base.read_journal(
        source_journal, queue_bytes, expected_queue)
    if observed != batch2.COUNT or attempts != batch2.COUNT:
        raise ValueError('source batch is incomplete')
    expected_aggregate = batch2.summarize(
        expected_queue, queue_bytes, source_journal, projection)
    if scoped.strict_json(SOURCE_AGGREGATE.read_bytes()) != expected_aggregate:
        raise ValueError('source aggregate differs from pinned journal')
    sources = []
    for record in records:
        if record['http_status'] in (301, 302, 307, 308):
            sources.append({
                'source_index': record['index'],
                'source_repository': record['requested_repository'],
                'target_endpoint': redirect.target_from_location(
                    record['headers'].get('location'))})
    targets = sorted({row['target_endpoint'] for row in sources})
    if len(sources) != SOURCE_COUNT or len(targets) != TARGET_COUNT:
        raise ValueError('redirect source or destination count changed')
    queue = {'schema': QUEUE_SCHEMA,
             'source_queue_sha256': scoped.digest(queue_bytes),
             'source_journal_sha256': SOURCE_JOURNAL_SHA256,
             'repository_count': TARGET_COUNT,
             'redirect_source_count': SOURCE_COUNT,
             'order': 'lexicographic numeric repository endpoint',
             'task_selection_authorized': False,
             'repositories': targets,
             'redirect_sources': sources}
    return queue, base.encode(queue), records


def configure_target_probe():
    base.JOURNAL_SCHEMA = JOURNAL_SCHEMA
    base.PILOT_REPOSITORY_LIMIT = TARGET_COUNT
    base.PILOT_HTTP_LIMIT = TARGET_COUNT


def header(queue_bytes):
    return {'schema': JOURNAL_SCHEMA,
            'queue_sha256': scoped.digest(queue_bytes),
            'api_version': base.API_VERSION,
            'api_origin': base.API_ORIGIN,
            'pilot_repository_limit': TARGET_COUNT,
            'pilot_http_limit': TARGET_COUNT}


def summarize(queue, queue_bytes, journal):
    observed, records, attempts = base.read_journal(journal, queue_bytes, queue)
    statuses = {}
    for record in records:
        key = str(record['http_status'])
        statuses[key] = statuses.get(key, 0) + 1
    valid = [record for record in records if record['http_status'] == 200 and
             record['summary'] and record['summary'].get('metadata_status') == 'observed']
    matching = sum(
        row['summary']['repository_id'] == int(row['requested_repository'].split('/')[1])
        for row in valid)
    return {'schema': 'solcodex.quiet-python-batch2-redirect-aggregate.v1',
            'redirect_source_count': SOURCE_COUNT,
            'target_endpoint_count': TARGET_COUNT,
            'observed_target_count': observed,
            'http_attempt_count': attempts,
            'response_status_counts': statuses,
            'valid_200_metadata_count': len(valid),
            'id_matches_endpoint_count': matching,
            'fork_true_count': sum(row['summary']['fork'] for row in valid),
            'parent_present_count': sum(row['summary']['parent_full_name'] is not None
                                        for row in valid),
            'source_present_count': sum(row['summary']['source_full_name'] is not None
                                        for row in valid),
            'source_journal_sha256': SOURCE_JOURNAL_SHA256,
            'queue_sha256': scoped.digest(queue_bytes),
            'journal_sha256': scoped.digest(journal.read_bytes()),
            'controller_sha256': scoped.digest(Path(__file__).read_bytes()),
            'redirect_helper_sha256': REDIRECT_HELPER_SHA256,
            'ancestry_independently_verified': False,
            'sample_selected': False, 'model_run_authorized': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('projection', 'prior-journal', 'source-queue',
                 'source-journal', 'queue', 'journal'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--mode', required=True,
                        choices=('prepare', 'execute', 'summarize'))
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('private capture requires POSIX')
    for name in ('projection', 'prior_journal', 'source_queue',
                 'source_journal', 'queue', 'journal'):
        outside_repository(getattr(args, name))
    try:
        queue, queue_bytes, source_records = source(
            args.projection, args.prior_journal,
            args.source_queue, args.source_journal)
        configure_target_probe()
        if args.mode == 'prepare':
            base.create_private(args.queue, queue_bytes)
            base.create_private(args.journal, base.encode(header(queue_bytes)))
            result = {'prepared': True, 'redirect_source_count': SOURCE_COUNT,
                      'target_endpoint_count': TARGET_COUNT,
                      'queue_sha256': scoped.digest(queue_bytes),
                      'http_attempts': 0}
        else:
            base.require_private(args.queue)
            base.require_private(args.journal)
            if args.queue.read_bytes() != queue_bytes:
                raise ValueError('redirect target queue differs from source evidence')
            if args.mode == 'execute':
                with redirect.redirect_budget_lock(args.journal):
                    cooldown = redirect.source_cooldown(source_records)
                    if cooldown:
                        result = {'attempts_this_run': 0, 'stop_reason': cooldown}
                    else:
                        result = base.probe(queue, args.journal, queue_bytes,
                                            TARGET_COUNT, fetch=redirect.fetch_target)
            else:
                result = summarize(queue, queue_bytes, args.journal)
    except (OSError, ValueError) as error:
        parser.error(type(error).__name__ + ': private redirect capture stopped')
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
