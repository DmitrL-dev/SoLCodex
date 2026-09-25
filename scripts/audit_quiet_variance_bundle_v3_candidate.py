"""Audit the public pinned-bundle, no-model 16-slot candidate."""

import hashlib
import json
from pathlib import Path
import random


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-calibration-bundle-v3-candidate.json'
SCHEDULE = ROOT / 'docs/research/data/2026-09-25-quiet-variance-calibration-schedule.json'
RUNNER = ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-calibration-bundle-v3-runner.py'
BUNDLE = ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundled-cli-v3.py'
EVIDENCE = {
    'unit': ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-current-unit-probe.json',
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
    cli = protocol['cli']
    is_sha = lambda value: isinstance(value, str) and len(value) == 64 and all(
        char in '0123456789abcdef' for char in value)
    checks = {
        'candidate_disallows_model': (protocol['schema'] == 'solcodex.quiet-variance-calibration.v3'
                                      and protocol['status'] == 'candidate'
                                      and protocol['model_run_authorized'] is False
                                      and protocol['qualification']['status'] == 'pending'),
        'schedule_pinned': sha(SCHEDULE) == protocol['schedule_sha256'],
        'schedule_reproduced': schedule['schedule'] == expected_rows(schedule['seed']),
        'runner_snapshot_pinned': (sha(RUNNER) ==
                                   protocol['code_sha256']['variance_calibration_bundle_v3.py']),
        'bundle_adapter_pinned': sha(BUNDLE) == protocol['code_sha256']['bundled_cli_v3.py'],
        'bundle_hashes_declared': (cli['version'] == 'codex-cli 0.155.0-alpha.16.4'
                                   and is_sha(cli['sha256'])
                                   and is_sha(cli['code_mode_host_sha256'])),
        'existing_evidence_pinned': all(sha(path) == qualifications[name]
                                        for name, path in EVIDENCE.items()),
        'remaining_evidence_unset': all(qualifications[name] is None for name in
                                       ('late_missing', 'positive_path',
                                        'normal_cli_matrix', 'integrated_path',
                                        'independent_review')),
        'four_balanced_blocks': len(schedule['schedule']) == 16 and all(
            len([row for row in schedule['schedule'] if row['block'] == block]) == 4
            for block in range(1, 5)),
    }
    return {'schema': 'solcodex.quiet-variance-candidate-audit.v3',
            'pass': all(checks.values()), 'checks': checks,
            'pinned_private_code_files': len(protocol['code_sha256']),
            'qualification_evidence_present': sum(value is not None
                                                   for value in qualifications.values()),
            'qualification_evidence_required': len(qualifications),
            'model_run_authorized': protocol['model_run_authorized'],
            'private_code_bytes_independently_verified': False}


if __name__ == '__main__':
    print(json.dumps(audit(), sort_keys=True))
