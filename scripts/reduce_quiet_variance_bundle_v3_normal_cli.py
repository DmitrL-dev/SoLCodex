"""Recompute the pinned-bundle normal CLI control from public evidence."""

import hashlib
import json
from pathlib import Path
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/research/data/2026-09-25-quiet-variance-bundle-v3-normal-cli-expectations.json'
RESULT = ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-normal-cli-result.json'
CANDIDATE = ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-calibration-bundle-v3-candidate.json'
SCHEDULE = ROOT / 'docs/research/data/2026-09-25-quiet-variance-calibration-schedule.json'
TRACES = ROOT / 'docs/measurements/data'
FILES = {
    'cli_normal_bundle_probe_v3.py': ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-normal-cli-exploratory-probe.py',
    'frozen_quiet_variance_bundle_normal_cli_v3.py': ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-normal-cli-gate.py',
    'click_source_only_patch_v3.txt': ROOT / 'docs/measurements/fixtures/2026-09-25-click-source-only-v3.patch.txt',
    'click_extended_regressions_v3.py': ROOT / 'docs/measurements/fixtures/2026-09-25-click-extended-regressions-v3.py',
    'bundled_cli_v3.py': ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundled-cli-v3.py',
    'variance_calibration_bundle_v3.py': ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-calibration-bundle-v3-runner.py',
    'audit.py': ROOT / 'docs/measurements/fixtures/2026-09-25-real-cli-first-diagnostic-audit.py',
}
DYNAMIC = {'trace_sha256', 'quality_report_sha256', 'patch_sha256',
           'sealed_inventory_sha256'}
RESULT_META = {'schema', 'scope', 'expectations_sha256', 'private_source_sha256',
               'public_head', 'cli_bundle_sha256', 'predeclared_match',
               'mismatched_fields', 'private_artifacts_verified', 'cases'}
SHAPES = [
    ['thread.started', None, None], ['turn.started', None, None],
    ['item.started', 'command_execution', 'in_progress'],
    ['item.completed', 'command_execution', 'failed'],
    ['item.started', 'file_change', 'in_progress'],
    ['item.completed', 'file_change', 'completed'],
    ['turn.completed', None, None],
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode()


def is_sha(value):
    return isinstance(value, str) and len(value) == 64 and all(
        char in '0123456789abcdef' for char in value)


def candidate_core_digest():
    candidate = json.loads(CANDIDATE.read_text())
    core = {key: value for key, value in candidate.items()
            if key not in ('status', 'model_run_authorized', 'qualification')}
    return hashlib.sha256(canonical(core)).hexdigest()


def frozen_commit_valid(commit):
    if not isinstance(commit, str) or len(commit) != 40 or any(
            char not in '0123456789abcdef' for char in commit):
        return False
    try:
        published = subprocess.check_output(
            ['git', 'show', commit + ':' + PLAN.relative_to(ROOT).as_posix()],
            cwd=ROOT, timeout=10)
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'HEAD'],
                                  cwd=ROOT, capture_output=True, timeout=10)
        return published == PLAN.read_bytes() and ancestor.returncode == 0
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def trace_consistent(case):
    path = TRACES / ('2026-09-25-quiet-variance-bundle-v3-normal-cli-'
                     + case['id'] + '-trace.jsonl')
    if not path.is_file() or sha(path) != case.get('trace_sha256'):
        return False
    try:
        events = [json.loads(line) for line in path.read_text().splitlines()]
        shapes = [[event['type'], (event.get('item') or {}).get('type'),
                   (event.get('item') or {}).get('status')] for event in events]
        if shapes != SHAPES or case['trace_event_shapes'] != shapes:
            return False
        command = events[2]['item']
        completed = events[3]['item']
        started_change = events[4]['item']
        completed_change = events[5]['item']
        outer = shlex.split(command['command'])
        if outer[:2] != ['/bin/zsh', '-lc'] or len(outer) != 3:
            return False
        args = shlex.split(outer[2])
        task, arm = case['task'], case['arm']
        target = 'tests/' if task == 'click' else 'tests/test_metadata.py'
        suffix = (['-v'] if arm == 'verbose' else ['-q', '--tb=short'])
        suffix += ['-p', 'no:cacheprovider', '-o', 'addopts=']
        if (len(args) != len(suffix) + 4
                or not args[0].endswith('/' + case['id'] + '/tools/venv/bin/python')
                or args[1:] != ['-m', 'pytest', target] + suffix):
            return False
        output = completed.get('aggregated_output', '')
        diagnostic = ('FAILED tests/test_context.py::test_resource_exit_receives_original_exception'
                      if task == 'click' else
                      'FAILED tests/test_metadata.py::test_nested_spdx_regression_probe')
        summary = ('1 failed, 1282 passed, 22 skipped, 1 xfailed'
                   if task == 'click' else '1 failed, 290 passed')
        source = ('/workspace/src/click/core.py' if task == 'click' else
                  '/workspace/src/packaging/licenses/__init__.py')
        changes = started_change.get('changes')
        usage = events[6].get('usage', {})
        return (
            command['id'] == completed['id']
            and command['command'] == completed['command']
            and completed['exit_code'] == 1
            and diagnostic in output
            and summary in output
            and isinstance(changes, list) and len(changes) == 1
            and changes == completed_change.get('changes')
            and changes[0].get('kind') == 'update'
            and changes[0].get('path', '').endswith(source)
            and {key: usage.get(key) for key in
                 ('input_tokens', 'output_tokens', 'cached_input_tokens')} ==
                 case.get('usage') == {'input_tokens': 390,
                                       'output_tokens': 90,
                                       'cached_input_tokens': 195}
        )
    except (AttributeError, KeyError, TypeError, ValueError, UnicodeError):
        return False


def reduce():
    plan = json.loads(PLAN.read_text())
    result = json.loads(RESULT.read_text())
    candidate = json.loads(CANDIDATE.read_text())
    expected = plan['expected_cases']
    cases = result.get('cases', [])
    rows = json.loads(SCHEDULE.read_text())['schedule'][:4]
    mismatches = []
    if len(cases) != len(expected) or len(expected) != 4:
        mismatches.append('case_count')
    if [row['id'] for row in rows] != [item.get('id') for item in expected]:
        mismatches.append('schedule_order')
    for index, case in enumerate(cases):
        if index >= len(expected) or not isinstance(case, dict):
            mismatches.append('unexpected_case')
            continue
        target = expected[index]
        mismatches += [case.get('id', str(index)) + ':' + key
                       for key, value in target.items()
                       if canonical(case.get(key)) != canonical(value)]
        if set(case) != set(target) | DYNAMIC:
            mismatches.append(case.get('id', str(index)) + ':key_set')
        if any(not is_sha(case.get(key)) for key in DYNAMIC):
            mismatches.append(case.get('id', str(index)) + ':dynamic_hash')
    pins = plan['private_source_sha256']
    provenance = (
        plan.get('schema') == 'solcodex.quiet-variance-bundle-normal-cli-expectations.v1'
        and plan.get('model_run_authorized') is False
        and plan.get('status') == 'prospective_no_model_development_control'
        and result.get('schema') == 'solcodex.quiet-variance-bundle-normal-cli-observations.v1'
        and set(result) == RESULT_META
        and result.get('scope') == 'pinned_bundle_no_model_normal_cli_matrix'
        and result.get('expectations_sha256') == sha(PLAN)
        and result.get('private_source_sha256') == pins
        and result.get('cli_bundle_sha256') == {
            'codex': plan['cli_binary_sha256'],
            'codex-code-mode-host': plan['code_mode_host_sha256']}
        and plan['schedule_rows'] == rows
        and all(sha(path) == pins[name] for name, path in FILES.items())
        and sha(ROOT / 'scripts/usage_attempt_ledger.py') == plan['usage_ledger_sha256']
        and sha(Path(__file__)) == plan['reducer_sha256']
        and sha(FILES['click_source_only_patch_v3.txt']) == plan['click_patch_sha256']
        and candidate_core_digest() == plan['calibration_core_sha256']
        and candidate['cli']['sha256'] == plan['cli_binary_sha256']
        and candidate['cli']['code_mode_host_sha256'] == plan['code_mode_host_sha256']
        and candidate['cli']['version'] == plan['cli_version']
        and candidate['usage_ledger_sha256'] == plan['usage_ledger_sha256']
        and candidate['venv_tree_sha256'] == plan['runtime_tree_sha256']
        and candidate['quality']['evaluator_runtime_sha256']
            == plan['evaluator_runtime_tree_sha256']
        and candidate['quality']['assets_lock_sha256']
            == plan['quality_assets_lock_sha256']
        and candidate['source_tree_sha256'] == plan['source_tree_sha256']
        and frozen_commit_valid(result.get('public_head'))
    )
    traces_ok = len(cases) == 4 and all(trace_consistent(case) for case in cases)
    reported = (not mismatches and provenance and traces_ok
                and result.get('predeclared_match') is True
                and result.get('mismatched_fields') == []
                and result.get('private_artifacts_verified') is True)
    return {'schema': 'solcodex.quiet-variance-bundle-normal-cli-decision.v1',
            'control_pass': reported, 'private_gate_reported_pass': reported,
            'public_trace_consistent': traces_ok,
            'quality_report_publicly_verified': False,
            'provenance_consistent': provenance,
            'mismatched_fields': sorted(mismatches),
            'full_measurement_path_qualified': False,
            'plugin_savings_established': False}


if __name__ == '__main__':
    print(json.dumps(reduce(), sort_keys=True))
