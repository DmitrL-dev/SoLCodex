"""Recompute the pinned real-CLI integration control from public evidence."""

import hashlib
import json
from pathlib import Path
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/research/data/2026-09-25-quiet-variance-bundle-v3-integrated-expectations.json'
RESULT = ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-integrated-result.json'
CANDIDATE = ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-calibration-bundle-v3-candidate.json'
DATA = ROOT / 'docs/measurements/data'
FILES = {
    'cli_integrated_bundle_probe_v3.py': ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-integrated-probe.py',
    'frozen_quiet_variance_bundle_integrated_v3.py': ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-integrated-gate.py',
    'cli_normal_bundle_probe_v3.py': ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-normal-cli-exploratory-probe.py',
    'bundled_cli_v3.py': ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundled-cli-v3.py',
    'variance_calibration_bundle_v3.py': ROOT / 'docs/measurements/fixtures/2026-09-25-quiet-variance-calibration-bundle-v3-runner.py',
    'audit.py': ROOT / 'docs/measurements/fixtures/2026-09-25-real-cli-first-diagnostic-audit.py',
}
META = {'schema', 'scope', 'expectations_sha256', 'private_source_sha256',
        'public_head', 'cli_bundle_sha256', 'predeclared_match',
        'mismatched_fields', 'private_artifacts_verified', 'cases'}
DYNAMIC = {'trace_sha256', 'patch_sha256', 'quality_report_sha256',
           'sealed_inventory_sha256', 'cleanup_observation_sha256',
           'ledger_projection_sha256'}
SHAPES = [
    ['thread.started', None, None], ['turn.started', None, None],
    ['item.started', 'command_execution', 'in_progress'],
    ['item.completed', 'command_execution', 'failed'],
    ['item.started', 'file_change', 'in_progress'],
    ['item.completed', 'file_change', 'completed'],
    ['turn.completed', None, None],
]
USAGE = {'input_tokens': 390, 'output_tokens': 90, 'cached_input_tokens': 195}
LEDGER_USAGES = [(120, 30, 40), (200, 50, 150), (70, 10, 5)]


def strict(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON key')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_sha(value):
    return isinstance(value, str) and len(value) == 64 and all(
        char in '0123456789abcdef' for char in value)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode()


def candidate_core_digest():
    candidate = strict(CANDIDATE.read_bytes())
    core = {key: item for key, item in candidate.items()
            if key not in ('status', 'model_run_authorized', 'qualification')}
    return hashlib.sha256(canonical(core)).hexdigest()


def frozen_commit_valid(commit):
    if not isinstance(commit, str) or len(commit) != 40 or any(
            char not in '0123456789abcdef' for char in commit):
        return False
    try:
        frozen = subprocess.check_output(
            ['git', 'show', commit + ':' + PLAN.relative_to(ROOT).as_posix()],
            cwd=ROOT, timeout=10)
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'HEAD'],
                                  cwd=ROOT, capture_output=True, timeout=10)
        return frozen == PLAN.read_bytes() and ancestor.returncode == 0
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def trace_consistent(case):
    if case.get('case') not in ('wrong_complete', 'wrong_cleanup_uncertain'):
        return False
    path = DATA / ('2026-09-25-quiet-variance-bundle-v3-integrated-'
                   + case['case'] + '-trace.jsonl')
    if not path.is_file() or sha(path) != case.get('trace_sha256'):
        return False
    try:
        events = [strict(line) for line in path.read_bytes().splitlines()]
        shapes = [[event['type'], (event.get('item') or {}).get('type'),
                   (event.get('item') or {}).get('status')] for event in events]
        if shapes != SHAPES or shapes != case['trace_event_shapes']:
            return False
        command, completed = events[2]['item'], events[3]['item']
        changed, changed_done = events[4]['item'], events[5]['item']
        outer = shlex.split(command['command'])
        args = shlex.split(outer[2]) if len(outer) == 3 else []
        suffix = ['-m', 'pytest', 'tests/test_metadata.py', '-q', '--tb=short',
                  '-p', 'no:cacheprovider', '-o', 'addopts=']
        changes = changed.get('changes')
        usage = events[6].get('usage') or {}
        return (
            outer[:2] == ['/bin/zsh', '-lc']
            and len(args) == len(suffix) + 1
            and args[0].endswith('/' + case['case'] + '/02-b1-packaging-quiet/tools/venv/bin/python')
            and args[1:] == suffix
            and command['id'] == 'item_0'
            and command['id'] == completed['id']
            and command['command'] == completed['command']
            and canonical(completed['exit_code']) == canonical(1)
            and 'FAILED tests/test_metadata.py::test_nested_spdx_regression_probe'
                in completed.get('aggregated_output', '')
            and '1 failed, 290 passed' in completed.get('aggregated_output', '')
            and isinstance(changes, list) and len(changes) == 1
            and changed['id'] == 'item_1'
            and changed['id'] == changed_done['id']
            and changes == changed_done.get('changes')
            and changes[0].get('kind') == 'update'
            and changes[0].get('path', '').endswith(
                '/' + case['case'] + '/02-b1-packaging-quiet/workspace/src/packaging/licenses/__init__.py')
            and canonical({key: usage.get(key) for key in USAGE}) == canonical(USAGE)
            and canonical(case.get('usage')) == canonical(USAGE)
        )
    except (AttributeError, KeyError, TypeError, ValueError, UnicodeError):
        return False


def ledger_consistent(case):
    if case.get('case') not in ('wrong_complete', 'wrong_cleanup_uncertain'):
        return False
    path = DATA / ('2026-09-25-quiet-variance-bundle-v3-integrated-'
                   + case['case'] + '-ledger-projection.json')
    if not path.is_file() or sha(path) != case.get('ledger_projection_sha256'):
        return False
    try:
        value = strict(path.read_bytes())
        expected = [{'ordinal': index, 'state': 'completed',
                     'input_tokens': usage[0], 'output_tokens': usage[1],
                     'cached_input_tokens': usage[2], 'reason': None}
                    for index, usage in enumerate(LEDGER_USAGES, 1)]
        return (set(value) == {'schema', 'case', 'delivery', 'upstream',
                               'attempt_ids_match', 'raw_sha256'}
                and value['schema'] == 'solcodex.quiet-variance-bundle-ledger-projection.v1'
                and value['case'] == case['case']
                and canonical(value['delivery']) == canonical(expected)
                and canonical(value['upstream']) == canonical(expected)
                and value['attempt_ids_match'] is True
                and set(value['raw_sha256']) == {'delivery', 'upstream'}
                and all(is_sha(item) for item in value['raw_sha256'].values()))
    except (AttributeError, KeyError, TypeError, ValueError, UnicodeError):
        return False


def reduce():
    plan = strict(PLAN.read_bytes())
    result = strict(RESULT.read_bytes())
    candidate = strict(CANDIDATE.read_bytes())
    expected = plan['expected_cases']
    cases = result.get('cases', [])
    mismatches = []
    if (not isinstance(cases, list) or not isinstance(expected, list)
            or len(cases) != 2 or len(expected) != 2):
        mismatches.append('case_count')
        cases = cases if isinstance(cases, list) else []
        expected = expected if isinstance(expected, list) else []
    if ([case.get('case') for case in cases if isinstance(case, dict)]
            != ['wrong_complete', 'wrong_cleanup_uncertain']):
        mismatches.append('case_order')
    for index, case in enumerate(cases):
        if index >= len(expected) or not isinstance(case, dict):
            mismatches.append('unexpected_case')
            continue
        target = expected[index]
        mismatches += [case.get('case', str(index)) + ':' + key
                       for key, item in target.items()
                       if canonical(case.get(key)) != canonical(item)]
        if set(case) != set(target) | DYNAMIC:
            mismatches.append(case.get('case', str(index)) + ':key_set')
        if any(not is_sha(case.get(key)) for key in DYNAMIC - {'quality_report_sha256'}):
            mismatches.append(case.get('case', str(index)) + ':dynamic_hash')
        if ((index == 0 and not is_sha(case.get('quality_report_sha256')))
                or (index == 1 and case.get('quality_report_sha256') is not None)):
            mismatches.append(case.get('case', str(index)) + ':quality_hash')
    pins = plan['private_source_sha256']
    host_runtime = plan.get('host_python_runtime', {})
    provenance = (
        plan.get('schema') == 'solcodex.quiet-variance-bundle-integrated-expectations.v1'
        and plan.get('status') == 'prospective_no_model_development_control'
        and plan.get('model_run_authorized') is False
        and plan.get('case_order') == ['wrong_complete', 'wrong_cleanup_uncertain']
        and plan.get('control_timeout_seconds') == 90
        and plan.get('wrong_tree_sha256')
            == '607c7f277ab7b7ff9bbe94a77feb5e6aee3f3e131cc3abc63d06d71d14b509b5'
        and plan.get('schedule_row') == {'id': '02-b1-packaging-quiet',
                                         'block': 1, 'task': 'packaging', 'arm': 'quiet'}
        and isinstance(host_runtime, dict)
        and set(host_runtime) == {'executable', 'resolved_executable',
                                  'base_prefix', 'module_origins', 'tree_sha256'}
        and is_sha(host_runtime['tree_sha256'])
        and set(host_runtime['module_origins']) == {
            'json', 'hashlib', 'sqlite3', 'subprocess', 'shutil',
            'pathlib', 'fcntl', 'os'}
        and len(expected) == 2
        and expected[0].get('quality_status') == 'fail'
        and expected[0].get('quality') is False
        and expected[0].get('technical_ok') is True
        and expected[0].get('admissible') is True
        and expected[1].get('checkpoint_status') == 'blocked_unverified_cleanup'
        and expected[1].get('quality') is None
        and expected[1].get('evaluator_calls') == 0
        and expected[1].get('technical_ok') is False
        and expected[1].get('admissible') is False
        and result.get('schema') == 'solcodex.quiet-variance-bundle-integrated-observations.v1'
        and set(result) == META
        and result.get('scope') == 'pinned_bundle_no_model_real_cli_integration'
        and result.get('expectations_sha256') == sha(PLAN)
        and result.get('private_source_sha256') == pins
        and result.get('cli_bundle_sha256') == {
            'codex': plan['cli_binary_sha256'],
            'codex-code-mode-host': plan['code_mode_host_sha256']}
        and all(sha(path) == pins[name] for name, path in FILES.items())
        and sha(ROOT / 'scripts/usage_attempt_ledger.py') == plan['usage_ledger_sha256']
        and sha(Path(__file__)) == plan['reducer_sha256']
        and candidate_core_digest() == plan['calibration_core_sha256']
        and candidate['cli']['sha256'] == plan['cli_binary_sha256']
        and candidate['cli']['code_mode_host_sha256'] == plan['code_mode_host_sha256']
        and candidate['cli']['version'] == plan['cli_version']
        and candidate['venv_tree_sha256'] == plan['runtime_tree_sha256']
        and candidate['quality']['evaluator_runtime_sha256']
            == plan['evaluator_runtime_tree_sha256']
        and candidate['quality']['assets_lock_sha256']
            == plan['quality_assets_lock_sha256']
        and candidate['source_tree_sha256']['packaging'] == plan['source_tree_sha256']
        and frozen_commit_valid(result.get('public_head'))
    )
    traces_ok = len(cases) == 2 and all(trace_consistent(case) for case in cases)
    ledgers_ok = len(cases) == 2 and all(ledger_consistent(case) for case in cases)
    reported = (not mismatches and provenance and traces_ok and ledgers_ok
                and result.get('predeclared_match') is True
                and result.get('mismatched_fields') == []
                and result.get('private_artifacts_verified') is True)
    return {'schema': 'solcodex.quiet-variance-bundle-integrated-decision.v1',
            'control_pass': reported, 'private_gate_reported_pass': reported,
            'public_trace_consistent': traces_ok,
            'public_ledger_projection_consistent': ledgers_ok,
            'quality_report_publicly_verified': False,
            'host_python_runtime_publicly_verified': False,
            'provenance_consistent': provenance,
            'mismatched_fields': sorted(mismatches),
            'full_measurement_path_qualified': False,
            'plugin_savings_established': False}


if __name__ == '__main__':
    print(json.dumps(reduce(), sort_keys=True))
