"""Select eight reviewed lineages per family for the quiet diagnostic screen.

This CLI has dedicated public rules and approval. A selected sample never
authorizes a model run; all campaign qualification gates remain separate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

try:
    from scripts.rank_confirm_candidates import EXPOSURE_PATH, outside_repository, publish_no_replace
    from scripts.rank_confirm_lineages import APPROVAL_PATH as MAP_APPROVAL_PATH
    from scripts.select_confirm_sample import select_sample, verify_current_map_approval
except ModuleNotFoundError:
    from rank_confirm_candidates import EXPOSURE_PATH, outside_repository, publish_no_replace
    from rank_confirm_lineages import APPROVAL_PATH as MAP_APPROVAL_PATH
    from select_confirm_sample import select_sample, verify_current_map_approval


ROOT = Path(__file__).resolve().parent.parent
APPROVAL_PATH = ROOT / 'docs/research/data/2026-09-25-quiet-screen-sample-approval.json'
RULES_PATH = ROOT / 'docs/research/2026-09-25-quiet-screen-selector.md'
CORE_PATH = Path(__file__).with_name('select_confirm_sample.py')
RANKER_PATH = Path(__file__).with_name('rank_confirm_lineages.py')
HELPER_PATH = Path(__file__).with_name('rank_confirm_candidates.py')
QUOTA = 8


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


def verify_approval(lineage_bytes, review_bytes, selector_hash, core_hash, helper_hash,
                    rules_hash, approval):
    required = {'schema', 'status', 'lineage_ranking_sha256', 'task_review_sha256',
                'selector_sha256', 'core_selector_sha256', 'metadata_helper_sha256',
                'rules_sha256',
                'independent_curators'}
    if (not isinstance(approval, dict) or set(approval) != required or
            approval['schema'] != 'solcodex.quiet-screen-sample-approval.v1' or
            approval['status'] != 'approved' or
            approval['lineage_ranking_sha256'] != digest(lineage_bytes) or
            approval['task_review_sha256'] != digest(review_bytes) or
            approval['selector_sha256'] != selector_hash or
            approval['core_selector_sha256'] != core_hash or
            approval['metadata_helper_sha256'] != helper_hash or
            approval['rules_sha256'] != rules_hash):
        raise ValueError('quiet-screen sample inputs lack current approval')
    names = approval['independent_curators']
    if (not isinstance(names, list) or len(names) != 2 or
            any(not isinstance(name, str) or not name.strip() for name in names) or
            names[0].strip().casefold() == names[1].strip().casefold()):
        raise ValueError('two distinct independent curators are required')


def select_quiet_sample(ranked, review):
    result = select_sample(ranked, review, quota=QUOTA)
    if (result['selected_count'] != 3 * QUOTA or
            result['family_counts'] != {'state': QUOTA, 'build': QUOTA, 'api': QUOTA}):
        raise ValueError('quiet-screen quota not satisfied')
    result['schema'] = 'solcodex.quiet-screen-sample.v1'
    result['screen_run_authorized'] = False
    del result['confirmation_run_authorized']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lineage-ranking', required=True, type=Path)
    parser.add_argument('--task-review', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('private publication requires POSIX')
    for path in (args.lineage_ranking, args.task_review, args.out):
        outside_repository(path)
    if args.out.exists():
        parser.error('output already exists')
    lineage_bytes = args.lineage_ranking.read_bytes()
    review_bytes = args.task_review.read_bytes()
    selector_hash = digest(Path(__file__).read_bytes())
    core_hash = digest(CORE_PATH.read_bytes())
    helper_hash = digest(HELPER_PATH.read_bytes())
    rules_hash = digest(RULES_PATH.read_bytes())
    try:
        verify_approval(lineage_bytes, review_bytes, selector_hash, core_hash, helper_hash,
                        rules_hash,
                        strict_json(APPROVAL_PATH.read_bytes()))
        ranked = strict_json(lineage_bytes)
        map_approval = strict_json(MAP_APPROVAL_PATH.read_bytes())
        exposure_hash = digest(EXPOSURE_PATH.read_bytes())
        ranker_hash = digest(RANKER_PATH.read_bytes())
        verify_current_map_approval(ranked, map_approval, exposure_hash, ranker_hash)
        report = select_quiet_sample(ranked, strict_json(review_bytes))
    except ValueError as error:
        parser.error(str(error))
    report.update(lineage_ranking_sha256=digest(lineage_bytes),
                  task_review_sha256=digest(review_bytes),
                  selector_sha256=selector_hash, core_selector_sha256=core_hash,
                  metadata_helper_sha256=helper_hash,
                  rules_sha256=rules_hash)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='.quiet-screen-sample-',dir=str(args.out.parent))
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(report,stream,sort_keys=True,indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        publish_no_replace(temp_path,args.out)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    print(json.dumps({key:report[key] for key in
                      ('selected_count','family_counts','reviewed_lineages',
                       'reviewed_tasks','screen_run_authorized')},sort_keys=True))


if __name__ == '__main__':
    main()
