"""Audit the public, no-model 16-slot calibration candidate."""

import hashlib
import json
from pathlib import Path
import random


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-calibration-candidate.json'
SCHEDULE = ROOT / 'docs/research/data/2026-09-25-quiet-variance-calibration-schedule.json'
RUNNER = ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-calibration-runner.py'
EVIDENCE = {
    'unit': ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-current-unit-probe.json',
    'late_missing': ROOT / 'docs/measurements/data/2026-09-25-real-cli-late-missing-usage-v2-result.json',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_rows(seed):
    rng = random.Random(int(seed, 16))
    click = ['verbose', 'verbose', 'quiet', 'quiet']
    first = ['click', 'click', 'packaging', 'packaging']
    rng.shuffle(click)
    rng.shuffle(first)
    rows = []
    for block in range(1, 5):
        click_first = click[block - 1]
        packaging_first = 'quiet' if click_first == 'verbose' else 'verbose'
        arms = {
            'click': [click_first, 'quiet' if click_first == 'verbose' else 'verbose'],
            'packaging': [packaging_first,
                          'quiet' if packaging_first == 'verbose' else 'verbose'],
        }
        tasks = [first[block - 1], 'packaging' if first[block - 1] == 'click' else 'click']
        for task in tasks:
            for arm in arms[task]:
                rows.append({'id': f'{len(rows)+1:02d}-b{block}-{task}-{arm}',
                             'block': block, 'task': task, 'arm': arm})
    return rows


def audit():
    protocol = json.loads(PROTOCOL.read_text())
    schedule = json.loads(SCHEDULE.read_text())
    qualifications = protocol['qualification']['evidence_sha256']
    checks = {
        'candidate_disallows_model': (protocol['status'] == 'candidate'
                                      and protocol['model_run_authorized'] is False
                                      and protocol['qualification']['status'] == 'pending'),
        'schedule_pinned': sha(SCHEDULE) == protocol['schedule_sha256'],
        'schedule_reproduced': schedule['schedule'] == expected_rows(schedule['seed']),
        'runner_snapshot_pinned': (sha(RUNNER) ==
                                   protocol['code_sha256']['variance_calibration.py']),
        'existing_evidence_pinned': all(sha(path) == qualifications[name]
                                        for name, path in EVIDENCE.items()),
        'remaining_evidence_unset': all(qualifications[name] is None for name in
                                       ('positive_path', 'integrated_path', 'independent_review')),
        'four_balanced_blocks': len(schedule['schedule']) == 16 and all(
            len([row for row in schedule['schedule'] if row['block'] == block]) == 4
            for block in range(1, 5)),
    }
    return {'schema': 'solcodex.quiet-variance-candidate-audit.v1',
            'pass': all(checks.values()), 'checks': checks,
            'pinned_private_code_files': len(protocol['code_sha256']),
            'qualification_evidence_present': sum(value is not None
                                                   for value in qualifications.values()),
            'qualification_evidence_required': len(qualifications),
            'model_run_authorized': protocol['model_run_authorized'],
            'private_code_bytes_independently_verified': False}


if __name__ == '__main__':
    print(json.dumps(audit(), sort_keys=True))
