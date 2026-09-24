"""Recompute the frozen no-model positive-path decision for quiet calibration."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/research/data/2026-09-25-quiet-variance-positive-path-expectations.json'
RESULT = ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-positive-path-result.json'
PROBE = ROOT / 'docs/measurements/fixtures/2026-09-25-real-cli-late-checkpoint-probe.py'
GATE = ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-positive-path-gate.py'
AUDIT = ROOT / 'docs/measurements/fixtures/2026-09-25-real-cli-first-diagnostic-audit.py'
CALIBRATION_RUNNER = ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-calibration-runner.py'
CANDIDATE = ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-calibration-candidate.json'
LEDGER = ROOT / 'scripts/usage_attempt_ledger.py'
META = {'schema', 'expectations_sha256', 'private_source_sha256', 'public_head',
        'predeclared_match', 'mismatched_fields', 'trace_sha256',
        'quality_report_sha256'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_sha(value):
    return (isinstance(value, str) and len(value) == 64
            and all(char in '0123456789abcdef' for char in value))


def is_git_commit(value):
    return (isinstance(value, str) and len(value) == 40
            and all(char in '0123456789abcdef' for char in value))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode()


def reduce():
    plan = json.loads(PLAN.read_text())
    result = json.loads(RESULT.read_text())
    expected = plan['expected_observations']
    pins = plan['private_source_sha256']
    mismatched = sorted(key for key, value in expected.items()
                         if canonical(result.get(key)) != canonical(value))
    if set(result) != set(expected) | META:
        mismatched.append('observation_key_set')
    if not all(is_sha(result.get(key)) for key in
               ('trace_sha256', 'quality_report_sha256')):
        mismatched.append('artifact_hash_shape')
    provenance = (
        plan.get('schema') == 'solcodex.quiet-variance-positive-path-expectations.v1'
        and plan.get('model_run_authorized') is False
        and result.get('schema') == 'solcodex.quiet-variance-positive-path-observations.v1'
        and result.get('expectations_sha256') == sha(PLAN)
        and result.get('private_source_sha256') == pins
        and sha(PROBE) == pins['cli_late_checkpoint_probe.py']
        and sha(GATE) == pins['frozen_quiet_variance_positive_path.py']
        and sha(AUDIT) == pins['audit.py']
        and sha(CALIBRATION_RUNNER) == pins['variance_calibration.py']
        and sha(CANDIDATE) == plan['calibration_candidate_sha256']
        and sha(LEDGER) == plan['usage_ledger_sha256']
        and sha(Path(__file__)) == plan['reducer_sha256']
        and is_git_commit(result.get('public_head'))
    )
    decision = (not mismatched and provenance
                and result.get('predeclared_match') is True
                and result.get('mismatched_fields') == [])
    return {'schema': 'solcodex.quiet-variance-positive-path-decision.v1',
            'no_model_positive_path_pass': decision,
            'mismatched_fields': mismatched,
            'provenance_consistent': provenance,
            'full_measurement_path_qualified': False,
            'plugin_savings_established': False}


if __name__ == '__main__':
    print(json.dumps(reduce(), sort_keys=True))
