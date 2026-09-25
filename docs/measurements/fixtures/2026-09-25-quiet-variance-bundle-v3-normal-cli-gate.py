"""Frozen no-model normal-completion matrix for the pinned v3 CLI bundle."""

import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import bundled_cli_v3 as bundled_cli
from broker_route import LEDGER_SHA256, LEDGER_SOURCE
import cli_normal_bundle_probe_v3 as probe
import evaluate
import pilot
from protocol_gate import strict_json
import runtime_manifest
from snapshot import manifest
import variance_calibration_bundle_v3 as calibration


PLAN = pilot.PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-bundle-v3-normal-cli-expectations.json'
RESULT = pilot.PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-normal-cli-result.json'
PUBLIC_PROBE = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-normal-cli-exploratory-probe.py'
PUBLIC_GATE = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-normal-cli-gate.py'
PUBLIC_PATCH = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-click-source-only-v3.patch.txt'
PUBLIC_REGRESSIONS = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-click-extended-regressions-v3.py'
PUBLIC_REDUCER = pilot.PUBLIC / 'scripts/reduce_quiet_variance_bundle_v3_normal_cli.py'
PUBLIC_BUNDLE = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundled-cli-v3.py'
PUBLIC_RUNNER = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-calibration-bundle-v3-runner.py'
PUBLIC_AUDIT = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-real-cli-first-diagnostic-audit.py'
PUBLIC_CANDIDATE = pilot.PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-calibration-bundle-v3-candidate.json'
PUBLIC_TRACES = {
    row['id']: pilot.PUBLIC / ('docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-normal-cli-'
                               + row['id'] + '-trace.jsonl')
    for row in strict_json(probe.SCHEDULE.read_bytes())['schedule'][:4]
}
PINNED_FILES = calibration.SOURCE_FILES | {
    'cli_toolcall_probe.py', 'test_host_bridge.py',
    'cli_normal_bundle_probe_v3.py', 'click_source_only_patch_v3.txt',
    'click_extended_regressions_v3.py', 'frozen_quiet_variance_bundle_normal_cli_v3.py',
}
DYNAMIC_CASE_FIELDS = {'trace_sha256', 'quality_report_sha256', 'patch_sha256',
                       'sealed_inventory_sha256'}
REGRESSION_NAMES = (
    'override_and_suppression', 'nested_suppression', 'replacement_exception',
    'outer_suppresses_replacement', 'override_errors', 'double_close',
    'explicit_close_and_nested_depth',
)
TRACE_SHAPES = [
    ['thread.started', None, None], ['turn.started', None, None],
    ['item.started', 'command_execution', 'in_progress'],
    ['item.completed', 'command_execution', 'failed'],
    ['item.started', 'file_change', 'in_progress'],
    ['item.completed', 'file_change', 'completed'],
    ['turn.completed', None, None],
]
REQUEST_ITEMS = [[],
    [{'type': 'custom_tool_call', 'call_id': 'call_synthetic_1'},
     {'type': 'custom_tool_call_output', 'call_id': 'call_synthetic_1'}],
    [{'type': 'custom_tool_call', 'call_id': 'call_synthetic_1'},
     {'type': 'custom_tool_call_output', 'call_id': 'call_synthetic_1'},
     {'type': 'custom_tool_call', 'call_id': 'call_synthetic_2'},
     {'type': 'custom_tool_call_output', 'call_id': 'call_synthetic_2'}]]
FIRST_COMMAND = {'commands_observed': 1, 'first_command_completed': True,
                 'first_command_exact': True, 'first_exit_code': 1,
                 'malformed_lines': 0, 'other_prior_actions': [],
                 'status': 'observed', 'trace_complete': True}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(path):
    return sha(path.read_bytes())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode()


def candidate_core_digest(path):
    candidate = strict_json(path.read_bytes())
    core = {key: item for key, item in candidate.items()
            if key not in ('status', 'model_run_authorized', 'qualification')}
    return sha(canonical(core))


def committed(path):
    relative = path.relative_to(pilot.PUBLIC).as_posix()
    return subprocess.check_output(['git', 'show', 'HEAD:' + relative],
                                   cwd=pilot.PUBLIC, timeout=10)


def preflight():
    bundled_cli.validate_source_snapshot(
        'frozen_quiet_variance_bundle_normal_cli_v3.py', PINNED_FILES)
    raw = PLAN.read_bytes()
    plan = strict_json(raw)
    required = {'schema', 'status', 'model_run_authorized', 'case',
                'private_source_sha256', 'calibration_core_sha256',
                'cli_binary_sha256', 'code_mode_host_sha256', 'cli_version',
                'python_version', 'runtime_tree_sha256', 'evaluator_runtime_tree_sha256',
                'quality_assets_lock_sha256', 'usage_ledger_sha256',
                'source_tree_sha256', 'schedule_rows', 'control_timeout_seconds',
                'click_patch_sha256', 'expected_cases', 'reducer_sha256'}
    if (set(plan) != required
            or plan['schema'] != 'solcodex.quiet-variance-bundle-normal-cli-expectations.v1'
            or plan['status'] != 'prospective_no_model_development_control'
            or plan['model_run_authorized'] is not False
            or plan['case'] != 'normal_completion_first_frozen_block'
            or plan['control_timeout_seconds'] != 90
            or sys.flags.optimize != 0
            or set(plan['private_source_sha256']) != PINNED_FILES):
        raise ValueError('invalid frozen normal CLI plan')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=pilot.PUBLIC,
                                   text=True, timeout=10).strip()
    remote = subprocess.check_output(['git', 'ls-remote', 'origin', 'refs/heads/main'],
                                     cwd=pilot.PUBLIC, text=True, timeout=20).split()[0]
    if raw != committed(PLAN) or head != remote:
        raise ValueError('normal CLI plan is not frozen on pushed main')
    published = (PUBLIC_PROBE, PUBLIC_GATE, PUBLIC_PATCH, PUBLIC_REGRESSIONS,
                 PUBLIC_REDUCER, PUBLIC_BUNDLE, PUBLIC_RUNNER, PUBLIC_AUDIT,
                 PUBLIC_CANDIDATE, probe.SCHEDULE)
    if any(path.read_bytes() != committed(path) for path in published):
        raise ValueError('public fixture differs from committed HEAD')
    observed = {name: digest(pilot.HERE / name) for name in PINNED_FILES}
    if observed != plan['private_source_sha256']:
        raise ValueError('private normal CLI source differs from frozen plan')
    mirrors = {
        'cli_normal_bundle_probe_v3.py': PUBLIC_PROBE,
        'frozen_quiet_variance_bundle_normal_cli_v3.py': PUBLIC_GATE,
        'click_source_only_patch_v3.txt': PUBLIC_PATCH,
        'click_extended_regressions_v3.py': PUBLIC_REGRESSIONS,
        'bundled_cli_v3.py': PUBLIC_BUNDLE,
        'variance_calibration_bundle_v3.py': PUBLIC_RUNNER,
        'audit.py': PUBLIC_AUDIT,
    }
    if (any(digest(path) != observed[name] for name, path in mirrors.items())
            or digest(PUBLIC_REDUCER) != plan['reducer_sha256']
            or LEDGER_SHA256 != plan['usage_ledger_sha256']
            or digest(LEDGER_SOURCE) != plan['usage_ledger_sha256']
            or LEDGER_SOURCE != pilot.PUBLIC / 'scripts/usage_attempt_ledger.py'
            or digest(PUBLIC_PATCH) != plan['click_patch_sha256']
            or candidate_core_digest(PUBLIC_CANDIDATE) != plan['calibration_core_sha256']
            or digest(bundled_cli.BUNDLE / 'codex') != plan['cli_binary_sha256']
            or digest(bundled_cli.BUNDLE / 'codex-code-mode-host')
                != plan['code_mode_host_sha256']
            or sys.version.split()[0] != plan['python_version']):
        raise ValueError('normal CLI public source or runtime differs')
    bundled_cli.validate_bundle()
    candidate = strict_json(PUBLIC_CANDIDATE.read_bytes())
    if (candidate['status'] != 'candidate'
            or candidate['model_run_authorized'] is not False
            or candidate['qualification']['evidence_sha256']['normal_cli_matrix'] is not None
            or candidate['cli']['sha256'] != plan['cli_binary_sha256']
            or candidate['cli']['code_mode_host_sha256'] != plan['code_mode_host_sha256']
            or candidate['cli']['version'] != plan['cli_version']
            or candidate['usage_ledger_sha256'] != plan['usage_ledger_sha256']
            or candidate['venv_tree_sha256'] != plan['runtime_tree_sha256']
            or candidate['source_tree_sha256'] != plan['source_tree_sha256']
            or candidate['quality']['assets_lock_sha256']
                != plan['quality_assets_lock_sha256']
            or candidate['quality']['evaluator_runtime_sha256']
                != plan['evaluator_runtime_tree_sha256']
            or set(candidate['code_sha256']) != calibration.SOURCE_FILES
            or any(candidate['code_sha256'][name] != observed[name]
                   for name in calibration.SOURCE_FILES)):
        raise ValueError('normal CLI candidate differs from frozen plan')
    rows = strict_json(probe.SCHEDULE.read_bytes())['schedule'][:4]
    if (rows != plan['schedule_rows']
            or len(rows) != 4
            or not isinstance(plan['expected_cases'], list)
            or len(plan['expected_cases']) != 4
            or any(not isinstance(case, dict) for case in plan['expected_cases'])
            or [case['id'] for case in plan['expected_cases']]
                != [row['id'] for row in rows]
            or runtime_manifest.digest(pilot.VENV) != plan['runtime_tree_sha256']
            or evaluate.runtime_digest(pilot.VENV)
                != plan['evaluator_runtime_tree_sha256']
            or digest(pilot.HERE / 'quality-assets/lock.json')
                != plan['quality_assets_lock_sha256']):
        raise ValueError('normal CLI schedule, runtime or evaluator differs')
    for row, expected in zip(rows, plan['expected_cases']):
        task = row['task']
        behavior = 9 if task == 'click' else 14
        contract = {
            'id': row['id'], 'task': task, 'arm': row['arm'],
            'provider_requests': 3, 'provider_errors': 0,
            'request_items': REQUEST_ITEMS, 'trace_event_shapes': TRACE_SHAPES,
            'first_command': FIRST_COMMAND, 'exit_code': 0, 'timed_out': False,
            'cleanup_verified': True, 'checkpoint_status': 'captured',
            'checkpoint_tree_sha256': probe.GOLD_TREES[task],
            'expected_gold_tree_sha256': probe.GOLD_TREES[task],
            'quality_status': 'pass', 'quality': True, 'technical_ok': True,
            'reconciliation_complete': True,
            'usage': {'input_tokens': 390, 'output_tokens': 90,
                      'cached_input_tokens': 195},
            'admissible': True, 'broker_stopped': True, 'bridge_stopped': True,
            'ledgers_verified': True, 'upstream_passed': True,
            'diagnostic_output_verified': True,
            'behavior_passed': behavior, 'behavior_total': behavior,
            'extended_click_regression_groups': 7 if task == 'click' else None,
        }
        if expected != contract:
            raise ValueError('normal CLI expected case differs from control contract')
    evaluate.load_assets()
    for task in ('click', 'packaging'):
        fixture = pilot.WORK / f'prehook-{task}-dev/fixture'
        if manifest(fixture)['tree_sha256'] != plan['source_tree_sha256'][task]:
            raise ValueError('normal CLI source fixture differs: ' + task)
    return raw, plan, observed, head


def verify_ledgers(host, final):
    requests = final['broker']['requests']
    ids = [item['attempt_id'] for item in requests]
    if len(ids) != 3 or len(set(ids)) != 3:
        raise ValueError('normal CLI attempt identifiers differ')
    expected = list(probe.USAGE)
    for name in ('delivery.sqlite3', 'upstream.sqlite3'):
        ledger = host / name
        with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro&immutable=1',
                                     uri=True)) as connection:
            if connection.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise ValueError('normal CLI ledger integrity failed: ' + name)
            rows = connection.execute(
                'SELECT attempt_id,state,input_tokens,output_tokens,'
                'cached_input_tokens,reason FROM attempts ORDER BY rowid').fetchall()
        if (len(rows) != 3 or [row[0] for row in rows] != ids
                or any(row[1] != 'completed' or row[2:5] != usage or row[5] is not None
                       for row, usage in zip(rows, expected))):
            raise ValueError('normal CLI ledger rows differ: ' + name)


def verify_case(case, directory, expected):
    host = directory / case['id'] / 'host-artifacts'
    final = strict_json((host / 'final.json').read_bytes())
    if (final['id'] != expected['id'] or final['task'] != expected['task']
            or final['arm'] != expected['arm']
            or case['sealed'] != calibration.evidence_hashes(directory, case['id'], final)
            or case['trace_sha256'] != digest(host / 'trace.jsonl')):
        raise ValueError('normal CLI sealed evidence differs')
    verify_ledgers(host, final)
    trace_events = [strict_json(line) for line in (host / 'trace.jsonl').read_bytes().splitlines()]
    trace_shapes = [[event['type'], (event.get('item') or {}).get('type'),
                     (event.get('item') or {}).get('status')] for event in trace_events]
    diagnostic_output = trace_events[3]['item'].get('aggregated_output', '')
    signature = ('FAILED tests/test_context.py::test_resource_exit_receives_original_exception'
                 if case['task'] == 'click' else
                 'FAILED tests/test_metadata.py::test_nested_spdx_regression_probe')
    summary = ('1 failed, 1282 passed, 22 skipped, 1 xfailed'
               if case['task'] == 'click' else '1 failed, 290 passed')
    if (trace_shapes != TRACE_SHAPES or case['trace_event_shapes'] != TRACE_SHAPES
            or trace_events[3]['item'].get('exit_code') != 1
            or signature not in diagnostic_output or summary not in diagnostic_output):
        raise ValueError('normal CLI diagnostic output differs')
    report_path = Path(final['quality_report_root']) / 'report.json'
    report = strict_json(report_path.read_bytes())
    behavior_total = 9 if case['task'] == 'click' else 14
    if (report.get('status') != 'pass' or report.get('quality') is not True
            or report.get('task') != case['task']
            or report.get('evidence_sha256') != digest(host / 'result.json')
            or case['quality_report_sha256'] != digest(report_path)
            or report.get('behavior_total') != behavior_total
            or report.get('behavior_passed') != behavior_total
            or report.get('upstream', {}).get('passed') is not True
            or report.get('candidate_tree_sha256') != probe.GOLD_TREES[case['task']]
            or report.get('assets_lock_sha256') != evaluate.ASSETS_LOCK_SHA256):
        raise ValueError('normal CLI external evaluator differs')
    regression_count = None
    if case['task'] == 'click':
        snapshot = host / 'snapshot'
        before = manifest(snapshot)
        result = subprocess.run([str(pilot.VENV / 'bin/python'), '-B',
                                 str(pilot.HERE / 'click_extended_regressions_v3.py'),
                                 str(snapshot)], cwd=pilot.HERE, capture_output=True,
                                text=True, timeout=20,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                                         PYTHONOPTIMIZE='0'))
        expected_lines = [name + ': PASS' for name in REGRESSION_NAMES]
        if (result.returncode != 0 or result.stdout.splitlines() != expected_lines
                or result.stderr or manifest(snapshot) != before):
            raise ValueError('normal CLI extended Click regressions failed')
        regression_count = len(expected_lines)
    copied = dict(case)
    sealed = copied.pop('sealed')
    copied.update(sealed_inventory_sha256=sha(canonical(sealed)),
                  ledgers_verified=True, upstream_passed=True,
                  diagnostic_output_verified=True,
                  behavior_passed=behavior_total, behavior_total=behavior_total,
                  extended_click_regression_groups=regression_count)
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    with (pilot.HERE / 'execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw, plan, observed, head = preflight()
        if not args.execute:
            print(json.dumps({'preflight': 'pass', 'public_head': head}, sort_keys=True))
            return 0
        result = probe.run(4)
        after_raw, after_plan, after_observed, after_head = preflight()
        if (after_raw != raw or after_plan != plan
                or after_observed != observed or after_head != head):
            raise ValueError('normal CLI pins drifted during execution')
        if len(result['observations']) != 4 or result['unstarted_ids']:
            raise ValueError('normal CLI matrix incomplete')
        directory = Path(result['private_root'])
        cases = [verify_case(case, directory, expected) for case, expected in
                 zip(result['observations'], plan['expected_cases'])]
        mismatches = []
        for case, expected in zip(cases, plan['expected_cases']):
            mismatches += [case['id'] + ':' + key for key, item in expected.items()
                           if canonical(case.get(key)) != canonical(item)]
            if set(case) != set(expected) | DYNAMIC_CASE_FIELDS:
                mismatches.append(case['id'] + ':key_set')
            if any(not calibration.is_sha(case.get(key)) for key in DYNAMIC_CASE_FIELDS):
                mismatches.append(case['id'] + ':dynamic_hash')
        for case in cases:
            trace = directory / case['id'] / 'host-artifacts/trace.jsonl'
            PUBLIC_TRACES[case['id']].write_bytes(trace.read_bytes())
        output = {'schema': 'solcodex.quiet-variance-bundle-normal-cli-observations.v1',
                  'scope': 'pinned_bundle_no_model_normal_cli_matrix',
                  'expectations_sha256': sha(raw),
                  'private_source_sha256': observed, 'public_head': head,
                  'cli_bundle_sha256': bundled_cli.PINNED,
                  'predeclared_match': not mismatches,
                  'mismatched_fields': sorted(mismatches),
                  'private_artifacts_verified': True,
                  'cases': cases}
        RESULT.write_text(json.dumps(output, sort_keys=True, indent=2) + '\n')
        print(json.dumps({'result_path': str(RESULT), 'predeclared_match': not mismatches,
                          'mismatched_fields': sorted(mismatches)}, sort_keys=True))
        return 0 if not mismatches else 1


if __name__ == '__main__':
    sys.exit(main())
