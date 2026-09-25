"""Recompute the pinned-bundle no-model missing-usage decision."""

import hashlib
import json
from pathlib import Path
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/research/data/2026-09-25-quiet-variance-bundle-v3-negative-expectations.json'
RESULT = ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-negative-result.json'
TRACE = ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-negative-trace.jsonl'
PROBE = ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-negative-probe.py'
GATE = ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-negative-gate.py'
BASE_PROBE = ROOT / 'docs/measurements/fixtures/2026-09-25-real-cli-late-checkpoint-bundle-v3-probe.py'
POSITIVE_GATE = ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-positive-gate.py'
POSITIVE_RESULT = ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-positive-result.json'
BUNDLE = ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundled-cli-v3.py'
AUDIT = ROOT / 'docs/measurements/fixtures/2026-09-25-real-cli-first-diagnostic-audit.py'
CALIBRATION_RUNNER = ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-calibration-bundle-v3-runner.py'
CANDIDATE = ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-calibration-bundle-v3-candidate.json'
LEDGER = ROOT / 'scripts/usage_attempt_ledger.py'
META = {'schema', 'expectations_sha256', 'private_source_sha256', 'public_head',
        'predeclared_match', 'mismatched_fields', 'trace_sha256',
        'quality_report_sha256', 'private_artifacts_verified', 'cli_bundle_sha256'}


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


def candidate_core_digest(path):
    candidate = json.loads(path.read_text())
    core = {key: value for key, value in candidate.items()
            if key not in ('status', 'model_run_authorized', 'qualification')}
    return hashlib.sha256(canonical(core)).hexdigest()


def frozen_commit_valid(commit):
    if not is_git_commit(commit):
        return False
    try:
        committed_plan = subprocess.check_output(
            ['git', 'show', commit + ':' + PLAN.relative_to(ROOT).as_posix()],
            cwd=ROOT, timeout=10)
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'HEAD'],
                                  cwd=ROOT, timeout=10, capture_output=True)
        return committed_plan == PLAN.read_bytes() and ancestor.returncode == 0
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def trace_consistent(result):
    if not TRACE.is_file() or not is_sha(result.get('trace_sha256')):
        return False
    raw = TRACE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != result['trace_sha256']:
        return False
    try:
        events = [json.loads(line) for line in raw.decode().splitlines()]
        shapes = [[event['type'], (event.get('item') or {}).get('type'),
                   (event.get('item') or {}).get('status')] for event in events]
        starts = [event['item'] for event in events if event['type'] == 'item.started'
                  and (event.get('item') or {}).get('type') == 'command_execution']
        completions = [event['item'] for event in events if event['type'] == 'item.completed'
                       and (event.get('item') or {}).get('type') == 'command_execution']
        if len(starts) != 1 or len(completions) != 1:
            return False
        outer = shlex.split(starts[0]['command'])
        if outer[:2] != ['/bin/zsh', '-lc'] or len(outer) != 3:
            return False
        args = shlex.split(outer[2])
        command_ok = (len(args) == 10 and args[0].endswith('/tools/venv/bin/python')
                      and args[1:] == ['-m', 'pytest', 'tests/test_metadata.py', '-q',
                                       '--tb=short', '-p', 'no:cacheprovider', '-o', 'addopts='])
        completed = completions[0]
        first_id = starts[0].get('id')
        output = completed.get('aggregated_output', '')
        diagnostic = ('1 failed, 290 passed' in output and
                      'FAILED tests/test_metadata.py::test_nested_spdx_regression_probe' in output)
        return (command_ok and isinstance(first_id, str) and bool(first_id)
                and completed.get('id') == first_id
                and completed.get('command') == starts[0]['command']
                and type(completed.get('exit_code')) is int
                and completed['exit_code'] == 1
                and completed.get('status') == 'failed'
                and diagnostic and result.get('diagnostic_signature') is True
                and result.get('trace_event_shapes') == shapes)
    except (AttributeError, KeyError, TypeError, ValueError, UnicodeError):
        return False


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
        plan.get('schema') == 'solcodex.quiet-variance-bundle-negative-expectations.v2'
        and plan.get('model_run_authorized') is False
        and result.get('schema') == 'solcodex.quiet-variance-bundle-negative-observations.v1'
        and result.get('expectations_sha256') == sha(PLAN)
        and result.get('private_source_sha256') == pins
        and result.get('cli_bundle_sha256') == {
            'codex': plan['cli_binary_sha256'],
            'codex-code-mode-host': plan['code_mode_host_sha256']}
        and sha(PROBE) == pins['cli_late_missing_usage_bundle_probe_v3.py']
        and sha(GATE) == pins['frozen_quiet_variance_bundle_negative_path_v3.py']
        and sha(BASE_PROBE) == pins['cli_late_checkpoint_bundle_probe_v3.py']
        and sha(POSITIVE_GATE) == pins['frozen_quiet_variance_bundle_positive_path_v3.py']
        and sha(POSITIVE_RESULT) == plan['positive_result_sha256']
        and sha(BUNDLE) == pins['bundled_cli_v3.py']
        and sha(AUDIT) == pins['audit.py']
        and sha(CALIBRATION_RUNNER) == pins['variance_calibration_bundle_v3.py']
        and candidate_core_digest(CANDIDATE) == plan['calibration_core_sha256']
        and sha(LEDGER) == plan['usage_ledger_sha256']
        and sha(Path(__file__)) == plan['reducer_sha256']
        and frozen_commit_valid(result.get('public_head'))
    )
    trace_ok = trace_consistent(result)
    negative_semantics = (result.get('usage') is None
        and result.get('reconciliation_complete') is False
        and result.get('technical_ok') is False
        and result.get('quality_status') == 'pass'
        and result.get('checkpoint_status') == 'captured'
        and result.get('broker_usage_status') == 'unknown'
        and (result.get('third_upstream') or {}).get('state') == 'unknown'
        and (result.get('third_upstream') or {}).get('error') == 'invalid_sse')
    reported = (negative_semantics and not mismatched and provenance and trace_ok
                and result.get('private_artifacts_verified') is True
                and result.get('predeclared_match') is True
                and result.get('mismatched_fields') == [])
    return {'schema': 'solcodex.quiet-variance-bundle-negative-decision.v1',
            'private_gate_reported_pass': reported,
            'control_pass': reported,
            'missing_usage_rejected': negative_semantics,
            'public_trace_consistent': trace_ok,
            'quality_report_publicly_verified': False,
            'mismatched_fields': mismatched,
            'provenance_consistent': provenance,
            'full_measurement_path_qualified': False,
            'plugin_savings_established': False}


if __name__ == '__main__':
    print(json.dumps(reduce(), sort_keys=True))
