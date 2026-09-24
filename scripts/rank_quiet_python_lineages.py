"""Rank the pinned Python quiet-screen pool after independent ancestry approval.

All task identities and ancestry evidence remain private. This wrapper leaves
the broader confirmation ranking and its approval path unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

try:
    from scripts.project_quiet_python_candidates import (
        DEPENDENCIES, LANGUAGE, SCHEMA as PROJECTION_SCHEMA, SCRIPTS,
        project as project_candidates)
    from scripts.rank_confirm_candidates import (
        EXPOSURE_PATH, REVISION, SEED as TASK_SEED, SOURCE, canonical_repo,
        outside_repository, publish_no_replace)
    from scripts.rank_confirm_lineages import (
        CANDIDATE_FIELDS, PINNED_RANK_SHA256, rank_lineages)
except ModuleNotFoundError:
    from project_quiet_python_candidates import (
        DEPENDENCIES, LANGUAGE, SCHEMA as PROJECTION_SCHEMA, SCRIPTS,
        project as project_candidates)
    from rank_confirm_candidates import (
        EXPOSURE_PATH, REVISION, SEED as TASK_SEED, SOURCE, canonical_repo,
        outside_repository, publish_no_replace)
    from rank_confirm_lineages import (
        CANDIDATE_FIELDS, PINNED_RANK_SHA256, rank_lineages)


ROOT = Path(__file__).resolve().parent.parent
AGGREGATE_PATH = ROOT / 'docs/measurements/data/2026-09-25-quiet-python-projection.json'
APPROVAL_PATH = ROOT / 'docs/research/data/2026-09-25-quiet-python-lineage-map-approval.json'
CORE_PATH = SCRIPTS / 'rank_confirm_lineages.py'
PROJECTION_PATH = SCRIPTS / 'project_quiet_python_candidates.py'
EXPECTED_SOURCE_COUNT = 31797
EXPECTED_CANDIDATE_COUNT = 7124
EXPECTED_REPOSITORY_COUNT = 687
PROJECTION_FIELDS = {'schema', 'language', 'source_candidate_count',
                     'projected_candidate_count', 'projected_direct_repository_count',
                     'lineage_review_required', 'sample_selection_authorized',
                     'source_candidate_sha256', 'projection_code_sha256',
                     'dependency_sha256', 'candidates'}
APPROVAL_FIELDS = {'schema', 'status', 'source_candidate_ranking_sha256',
                   'projected_candidate_sha256', 'projection_code_sha256',
                   'projection_dependency_sha256', 'lineage_map_sha256',
                   'ancestry_evidence_sha256', 'exposure_manifest_sha256',
                   'core_lineage_ranker_sha256', 'scoped_lineage_ranker_sha256',
                   'independent_reviewers'}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw: bytes):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON key')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def verify_projection(projection_bytes, aggregate):
    projection = strict_json(projection_bytes)
    if not isinstance(projection, dict) or set(projection) != PROJECTION_FIELDS or \
            projection['schema'] != PROJECTION_SCHEMA or projection['language'] != LANGUAGE or \
            projection['source_candidate_count'] != EXPECTED_SOURCE_COUNT or \
            projection['projected_candidate_count'] != EXPECTED_CANDIDATE_COUNT or \
            projection['projected_direct_repository_count'] != EXPECTED_REPOSITORY_COUNT or \
            projection['lineage_review_required'] is not True or \
            projection['sample_selection_authorized'] is not False:
        raise ValueError('invalid Python candidate projection')
    dependency_hashes = {name: digest((SCRIPTS / name).read_bytes())
                         for name in DEPENDENCIES}
    if not isinstance(aggregate, dict) or \
            aggregate.get('schema') != 'solcodex.quiet-python-projection-aggregate.v1' or \
            aggregate.get('candidate_source') != SOURCE or \
            aggregate.get('candidate_revision') != REVISION or \
            aggregate.get('private_projection_sha256') != digest(projection_bytes) or \
            aggregate.get('source_candidate_sha256') != PINNED_RANK_SHA256 or \
            aggregate.get('projection_code_sha256') != digest(PROJECTION_PATH.read_bytes()) or \
            aggregate.get('dependency_sha256') != dependency_hashes or \
            aggregate.get('source_candidate_count') != EXPECTED_SOURCE_COUNT or \
            aggregate.get('projected_candidate_count') != EXPECTED_CANDIDATE_COUNT or \
            aggregate.get('projected_direct_repository_count') != EXPECTED_REPOSITORY_COUNT or \
            projection['source_candidate_sha256'] != PINNED_RANK_SHA256 or \
            projection['projection_code_sha256'] != aggregate['projection_code_sha256'] or \
            projection['dependency_sha256'] != dependency_hashes:
        raise ValueError('Python projection differs from published source and code pins')
    rows = projection['candidates']
    if not isinstance(rows, list) or len(rows) != EXPECTED_CANDIDATE_COUNT or \
            any(not isinstance(row, dict) or set(row) != CANDIDATE_FIELDS or
                row['language'] != LANGUAGE for row in rows):
        raise ValueError('projected candidates differ from the Python-only frame')
    synthetic_source = {'schema': 'solcodex.candidate-rank.v1',
                        'candidate_source': aggregate['candidate_source'],
                        'candidate_revision': aggregate['candidate_revision'],
                        'selection_seed': TASK_SEED,
                        'export_manifest_checks_passed': True, 'candidates': rows}
    checked = project_candidates(synthetic_source)
    if checked['candidates'] != rows or \
            len({row['repo'] for row in rows}) != EXPECTED_REPOSITORY_COUNT:
        raise ValueError('projected task order or repository coverage changed')
    return projection, synthetic_source


def verify_approval(projection_bytes, map_bytes, evidence_bytes, exposure_bytes,
                    approval):
    expected = {
        'source_candidate_ranking_sha256': PINNED_RANK_SHA256,
        'projected_candidate_sha256': digest(projection_bytes),
        'projection_code_sha256': digest(PROJECTION_PATH.read_bytes()),
        'projection_dependency_sha256': digest(json.dumps(
            {name: digest((SCRIPTS / name).read_bytes()) for name in DEPENDENCIES},
            sort_keys=True, separators=(',', ':')).encode()),
        'lineage_map_sha256': digest(map_bytes),
        'ancestry_evidence_sha256': digest(evidence_bytes),
        'exposure_manifest_sha256': digest(exposure_bytes),
        'core_lineage_ranker_sha256': digest(CORE_PATH.read_bytes()),
        'scoped_lineage_ranker_sha256': digest(Path(__file__).read_bytes()),
    }
    if not isinstance(approval, dict) or set(approval) != APPROVAL_FIELDS or \
            approval['schema'] != 'solcodex.quiet-python-lineage-map-approval.v1' or \
            approval['status'] != 'approved' or \
            any(approval[key] != value for key, value in expected.items()):
        raise ValueError('Python lineage map lacks current independent approval')
    reviewers = approval['independent_reviewers']
    if not isinstance(reviewers, list) or len(reviewers) != 2 or \
            any(not isinstance(name, str) or not name.strip() for name in reviewers) or \
            reviewers[0].strip().casefold() == reviewers[1].strip().casefold():
        raise ValueError('two distinct independent ancestry reviewers are required')
    return expected


def verify_evidence(evidence, lineage_map, candidate_repositories, exposed):
    if not isinstance(evidence, dict) or set(evidence) != {'schema', 'lineages'} or \
            evidence['schema'] != 'solcodex.quiet-python-ancestry-evidence.v1' or \
            not isinstance(evidence['lineages'], list) or \
            not isinstance(lineage_map, dict) or \
            lineage_map.get('schema') != 'solcodex.lineage-map.v1' or \
            not isinstance(lineage_map.get('lineages'), list) or \
            len(evidence['lineages']) != len(lineage_map['lineages']):
        raise ValueError('ancestry evidence must cover every lineage group')
    expected = {}
    for group in lineage_map['lineages']:
        if not isinstance(group, dict) or set(group) != {
                'repositories', 'exposure_witnesses'} or \
                not isinstance(group['repositories'], list) or \
                not isinstance(group['exposure_witnesses'], list):
            raise ValueError('invalid lineage group in evidence comparison')
        names = group['repositories']
        if not names or any(not isinstance(name, str) for name in names) or \
                names != sorted(set(names)) or \
                any(canonical_repo(name) != name for name in names) or \
                any(name not in candidate_repositories for name in names):
            raise ValueError('noncanonical or foreign lineage repository')
        key = tuple(names)
        if key in expected:
            raise ValueError('duplicate lineage group')
        expected[key] = group['exposure_witnesses']
    if {name for group in expected for name in group} != candidate_repositories:
        raise ValueError('lineage map does not cover the Python pool')
    seen = set()
    for record in evidence['lineages']:
        if not isinstance(record, dict) or set(record) != {
                'repositories', 'resolution', 'external_repositories',
                'exposure_witnesses', 'evidence_references'}:
            raise ValueError('incomplete ancestry evidence record')
        names = record['repositories']
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError('invalid evidence repositories')
        key = tuple(names)
        if key not in expected or key in seen or record['resolution'] != 'resolved':
            raise ValueError('unresolved or duplicate ancestry evidence')
        seen.add(key)
        external = record['external_repositories']
        witnesses = record['exposure_witnesses']
        references = record['evidence_references']
        if any(not isinstance(values, list) or
               any(not isinstance(value, str) or not value.strip() for value in values) or
               values != sorted(set(values)) for values in (external, witnesses, references)) or \
                any(canonical_repo(value) != value for value in external + witnesses) or \
                set(external).intersection(candidate_repositories) or \
                witnesses != expected[key] or \
                not set(witnesses) <= exposed or \
                not (set(external) | set(names)).intersection(exposed) <= set(witnesses) or \
                not references:
            raise ValueError('ancestry evidence conflicts with map or exposure')
    if seen != set(expected):
        raise ValueError('ancestry evidence is incomplete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projected-candidates', required=True, type=Path)
    parser.add_argument('--lineage-map', required=True, type=Path)
    parser.add_argument('--ancestry-evidence', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('private publication requires POSIX')
    for path in (args.projected_candidates, args.lineage_map,
                 args.ancestry_evidence, args.out):
        outside_repository(path)
    if args.out.exists():
        parser.error('output already exists')
    try:
        projection_bytes = args.projected_candidates.read_bytes()
        map_bytes = args.lineage_map.read_bytes()
        evidence_bytes = args.ancestry_evidence.read_bytes()
        exposure_bytes = EXPOSURE_PATH.read_bytes()
        _, source = verify_projection(
            projection_bytes, strict_json(AGGREGATE_PATH.read_bytes()))
        evidence = strict_json(evidence_bytes)
        lineage_map = strict_json(map_bytes)
        pins = verify_approval(projection_bytes, map_bytes, evidence_bytes,
                               exposure_bytes, strict_json(APPROVAL_PATH.read_bytes()))
        exposure = strict_json(exposure_bytes)
        if not isinstance(exposure, dict) or \
                exposure.get('schema') != 'solcodex.development-exposure.v1' or \
                not isinstance(exposure.get('lineages'), list):
            raise ValueError('invalid development exposure manifest')
        exposed = {canonical_repo(repo) for group in exposure['lineages']
                   for repo in group['repositories']}
        verify_evidence(evidence, lineage_map,
                        {row['repo'] for row in source['candidates']}, exposed)
        report = rank_lineages(source, lineage_map, exposed)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if report['candidate_count'] != EXPECTED_CANDIDATE_COUNT or \
            len({repo for group in report['lineages'] for repo in group['repositories']}) \
            != EXPECTED_REPOSITORY_COUNT:
        parser.error('scoped lineage ranking changed candidate coverage')
    report['public_map_approval_checked'] = True
    report['ancestry_independently_verified'] = True
    report.update(pins)
    report['projected_candidate_sha256'] = digest(projection_bytes)
    args.out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_path = tempfile.mkstemp(prefix='.quiet-python-lineage-rank-',
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
        'candidate_count', 'lineage_count', 'excluded_exposure_lineages',
        'ancestry_independently_verified', 'sample_selected')}, sort_keys=True))


if __name__ == '__main__':
    main()
