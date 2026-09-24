"""Privately project the pinned metadata ranking to Python screen candidates.

This deterministic projection neither groups repository ancestry nor selects
tasks. Ranked IDs remain outside the public repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

try:
    from scripts.rank_confirm_candidates import (
        REVISION, SEED, SOURCE, outside_repository, publish_no_replace, validate_row)
    from scripts.rank_confirm_lineages import CANDIDATE_FIELDS, PINNED_RANK_SHA256
    from scripts.select_quiet_screen_sample import strict_json
except ModuleNotFoundError:
    from rank_confirm_candidates import (
        REVISION, SEED, SOURCE, outside_repository, publish_no_replace, validate_row)
    from rank_confirm_lineages import CANDIDATE_FIELDS, PINNED_RANK_SHA256
    from select_quiet_screen_sample import strict_json


LANGUAGE = 'python'
SCHEMA = 'solcodex.quiet-python-candidate-projection.v1'
SCRIPTS = Path(__file__).resolve().parent
DEPENDENCIES = ('rank_confirm_candidates.py', 'rank_confirm_lineages.py',
                'select_quiet_screen_sample.py')


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def project(ranked: dict) -> dict:
    if (not isinstance(ranked, dict) or
            ranked.get('schema') != 'solcodex.candidate-rank.v1' or
            ranked.get('candidate_source') != SOURCE or
            ranked.get('candidate_revision') != REVISION or
            ranked.get('selection_seed') != SEED or
            ranked.get('export_manifest_checks_passed') is not True):
        raise ValueError('invalid pinned candidate ranking')
    rows = ranked.get('candidates')
    if not isinstance(rows, list) or not rows:
        raise ValueError('candidate rows are required')
    seen_ids, previous = set(), None
    selected, repos = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != CANDIDATE_FIELDS:
            raise ValueError('candidate contains missing or outcome-bearing fields')
        instance_id, repo, language, _ = validate_row({key: row[key] for key in
            ('instance_id', 'repo', 'language', 'license')})
        key = (row['rank_sha256'], instance_id)
        if (row['repo'] != repo or instance_id in seen_ids or
                row['lineage_review'] != 'required' or
                row['rank_sha256'] != sha((SEED + '\n' + instance_id).encode()) or
                (previous is not None and key <= previous)):
            raise ValueError('candidate identity or frozen task order is invalid')
        seen_ids.add(instance_id)
        previous = key
        if language == LANGUAGE:
            selected.append(row)
            repos.add(repo)
    if not selected:
        raise ValueError('pinned ranking has no Python candidates')
    return {'schema': SCHEMA, 'language': LANGUAGE,
            'source_candidate_count': len(rows),
            'projected_candidate_count': len(selected),
            'projected_direct_repository_count': len(repos),
            'lineage_review_required': True,
            'sample_selection_authorized': False,
            'candidates': selected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-ranking', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('private publication requires POSIX')
    for path in (args.candidate_ranking, args.out):
        outside_repository(path)
    if args.out.exists():
        parser.error('output already exists')
    raw = args.candidate_ranking.read_bytes()
    source_hash = sha(raw)
    if source_hash != PINNED_RANK_SHA256:
        parser.error('source ranking differs from pinned metadata export')
    try:
        report = project(strict_json(raw))
    except ValueError as error:
        parser.error(str(error))
    report['source_candidate_sha256'] = source_hash
    report['projection_code_sha256'] = sha(Path(__file__).read_bytes())
    report['dependency_sha256'] = {name: sha((SCRIPTS / name).read_bytes())
                                   for name in DEPENDENCIES}
    args.out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_path = tempfile.mkstemp(prefix='.quiet-python-candidates-',
                                      dir=str(args.out.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        publish_no_replace(temp_path, args.out)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    print(json.dumps({key: report[key] for key in (
        'source_candidate_count', 'projected_candidate_count',
        'projected_direct_repository_count', 'lineage_review_required',
        'sample_selection_authorized')}, sort_keys=True))


if __name__ == '__main__':
    main()
