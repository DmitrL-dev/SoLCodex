"""Select the Python quiet screen only after replaying scoped ancestry ranking.

The CLI keeps every task identity private and does not authorize model runs.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

try:
    from scripts import rank_quiet_python_lineages as scoped
    from scripts.rank_confirm_candidates import (
        EXPOSURE_PATH, canonical_repo, outside_repository, publish_no_replace)
    from scripts.rank_confirm_lineages import rank_lineages
    from scripts.select_quiet_screen_sample import select_quiet_sample
except ModuleNotFoundError:
    import rank_quiet_python_lineages as scoped
    from rank_confirm_candidates import (
        EXPOSURE_PATH, canonical_repo, outside_repository, publish_no_replace)
    from rank_confirm_lineages import rank_lineages
    from select_quiet_screen_sample import select_quiet_sample


ROOT = Path(__file__).resolve().parent.parent
APPROVAL_PATH = ROOT / 'docs/research/data/2026-09-25-quiet-python-sample-approval.json'
RULES_PATH = ROOT / 'docs/research/2026-09-25-quiet-python-sample-selector.md'
PROPOSAL_PATH = ROOT / 'docs/research/2026-09-25-heldout-quiet-screen-proposal.md'
MAP_APPROVAL_PATH = scoped.APPROVAL_PATH
AGGREGATE_PATH = scoped.AGGREGATE_PATH
QUIET_HELPER_PATH = Path(__file__).with_name('select_quiet_screen_sample.py')
CORE_SELECTOR_PATH = Path(__file__).with_name('select_confirm_sample.py')
APPROVAL_FIELDS = {'schema', 'status', 'lineage_ranking_sha256',
                   'task_review_sha256', 'projected_candidate_sha256',
                   'lineage_map_sha256', 'ancestry_evidence_sha256',
                   'map_approval_sha256', 'projection_aggregate_sha256',
                   'selector_sha256', 'quiet_helper_sha256',
                   'core_selector_sha256', 'rules_sha256', 'screen_proposal_sha256',
                   'independent_curators'}


def verify_sample_approval(lineage_bytes, review_bytes, projection_bytes,
                           map_bytes, evidence_bytes, map_approval_bytes,
                           aggregate_bytes, approval):
    expected = {
        'lineage_ranking_sha256': scoped.digest(lineage_bytes),
        'task_review_sha256': scoped.digest(review_bytes),
        'projected_candidate_sha256': scoped.digest(projection_bytes),
        'lineage_map_sha256': scoped.digest(map_bytes),
        'ancestry_evidence_sha256': scoped.digest(evidence_bytes),
        'map_approval_sha256': scoped.digest(map_approval_bytes),
        'projection_aggregate_sha256': scoped.digest(aggregate_bytes),
        'selector_sha256': scoped.digest(Path(__file__).read_bytes()),
        'quiet_helper_sha256': scoped.digest(QUIET_HELPER_PATH.read_bytes()),
        'core_selector_sha256': scoped.digest(CORE_SELECTOR_PATH.read_bytes()),
        'rules_sha256': scoped.digest(RULES_PATH.read_bytes()),
        'screen_proposal_sha256': scoped.digest(PROPOSAL_PATH.read_bytes()),
    }
    if not isinstance(approval, dict) or set(approval) != APPROVAL_FIELDS or \
            approval['schema'] != 'solcodex.quiet-python-sample-approval.v1' or \
            approval['status'] != 'approved' or \
            any(approval[key] != value for key, value in expected.items()):
        raise ValueError('Python quiet-screen sample lacks current public approval')
    curators = approval['independent_curators']
    if not isinstance(curators, list) or len(curators) != 2 or \
            any(not isinstance(name, str) or not name.strip() for name in curators) or \
            curators[0].strip().casefold() == curators[1].strip().casefold():
        raise ValueError('two distinct independent task curators are required')
    return expected


def replay_scoped_ranking(projection_bytes, map_bytes, evidence_bytes,
                          exposure_bytes, map_approval_bytes, aggregate_bytes):
    _, source = scoped.verify_projection(
        projection_bytes, scoped.strict_json(aggregate_bytes))
    lineage_map = scoped.strict_json(map_bytes)
    evidence = scoped.strict_json(evidence_bytes)
    exposure = scoped.strict_json(exposure_bytes)
    if not isinstance(exposure, dict) or \
            exposure.get('schema') != 'solcodex.development-exposure.v1' or \
            not isinstance(exposure.get('lineages'), list):
        raise ValueError('invalid global development exposure manifest')
    exposed = {canonical_repo(repo) for group in exposure['lineages']
               for repo in group['repositories']}
    scoped.verify_evidence(evidence, lineage_map,
                           {row['repo'] for row in source['candidates']}, exposed)
    pins = scoped.verify_approval(projection_bytes, map_bytes, evidence_bytes,
                                   exposure_bytes,
                                   scoped.strict_json(map_approval_bytes))
    replayed = rank_lineages(source, lineage_map, exposed)
    if replayed['candidate_count'] != scoped.EXPECTED_CANDIDATE_COUNT or \
            len({repo for group in replayed['lineages']
                 for repo in group['repositories']}) != scoped.EXPECTED_REPOSITORY_COUNT:
        raise ValueError('scoped lineage ranking changed candidate coverage')
    replayed['public_map_approval_checked'] = True
    replayed['ancestry_independently_verified'] = True
    replayed.update(pins)
    replayed['projected_candidate_sha256'] = scoped.digest(projection_bytes)
    return replayed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projected-candidates', required=True, type=Path)
    parser.add_argument('--lineage-map', required=True, type=Path)
    parser.add_argument('--ancestry-evidence', required=True, type=Path)
    parser.add_argument('--lineage-ranking', required=True, type=Path)
    parser.add_argument('--task-review', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('private publication requires POSIX')
    for path in (args.projected_candidates, args.lineage_map,
                 args.ancestry_evidence, args.lineage_ranking,
                 args.task_review, args.out):
        outside_repository(path)
    if args.out.exists():
        parser.error('output already exists')
    try:
        projection_bytes = args.projected_candidates.read_bytes()
        map_bytes = args.lineage_map.read_bytes()
        evidence_bytes = args.ancestry_evidence.read_bytes()
        lineage_bytes = args.lineage_ranking.read_bytes()
        review_bytes = args.task_review.read_bytes()
        exposure_bytes = EXPOSURE_PATH.read_bytes()
        map_approval_bytes = MAP_APPROVAL_PATH.read_bytes()
        aggregate_bytes = AGGREGATE_PATH.read_bytes()
        pins = verify_sample_approval(
            lineage_bytes, review_bytes, projection_bytes, map_bytes,
            evidence_bytes, map_approval_bytes, aggregate_bytes,
            scoped.strict_json(APPROVAL_PATH.read_bytes()))
        replayed = replay_scoped_ranking(
            projection_bytes, map_bytes, evidence_bytes, exposure_bytes,
            map_approval_bytes, aggregate_bytes)
        submitted = scoped.strict_json(lineage_bytes)
        if submitted != replayed:
            raise ValueError('submitted lineage ranking differs from full scoped replay')
        result = select_quiet_sample(submitted, scoped.strict_json(review_bytes))
    except (ValueError, OSError) as error:
        parser.error(str(error))
    result['schema'] = 'solcodex.quiet-python-screen-sample.v1'
    result.update(pins)
    result['scoped_lineage_map_approval_checked'] = True
    args.out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_path = tempfile.mkstemp(prefix='.quiet-python-sample-',
                                      dir=str(args.out.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        publish_no_replace(temp_path, args.out)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    print(json.dumps({key: result[key] for key in (
        'selected_count', 'family_counts', 'reviewed_lineages',
        'reviewed_tasks', 'screen_run_authorized')}, sort_keys=True))


if __name__ == '__main__':
    main()
