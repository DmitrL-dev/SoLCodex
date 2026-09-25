"""Frozen no-model wrong-repair and cleanup integration for the pinned CLI."""

import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile

import bundled_cli_v3 as bundled_cli
from broker_route import LEDGER_SHA256, LEDGER_SOURCE
import cli_integrated_bundle_probe_v3 as probe
import cli_normal_bundle_probe_v3 as normal
import evaluate
import pilot
from protocol_gate import strict_json
import runtime_manifest
from snapshot import manifest
import variance_calibration_bundle_v3 as calibration


PLAN = pilot.PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-bundle-v3-integrated-expectations.json'
RESULT = pilot.PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-integrated-result.json'
CANDIDATE = pilot.PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-calibration-bundle-v3-candidate.json'
PUBLIC_PROBE = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-integrated-probe.py'
PUBLIC_GATE = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-integrated-gate.py'
PUBLIC_NORMAL = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-normal-cli-exploratory-probe.py'
PUBLIC_BUNDLE = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundled-cli-v3.py'
PUBLIC_RUNNER = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-calibration-bundle-v3-runner.py'
PUBLIC_AUDIT = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-real-cli-first-diagnostic-audit.py'
PUBLIC_REDUCER = pilot.PUBLIC / 'scripts/reduce_quiet_variance_bundle_v3_integrated.py'
TRACES = {name: pilot.PUBLIC / ('docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-integrated-'
                                + name + '-trace.jsonl') for name, _ in probe.CASES}
LEDGERS = {name: pilot.PUBLIC / ('docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-integrated-'
                                 + name + '-ledger-projection.json') for name, _ in probe.CASES}
PINNED_FILES = calibration.SOURCE_FILES | {
    'cli_integrated_bundle_probe_v3.py', 'frozen_quiet_variance_bundle_integrated_v3.py',
    'cli_normal_bundle_probe_v3.py', 'cli_toolcall_probe.py', 'test_host_bridge.py',
}
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
USAGE = {'input_tokens': 390, 'output_tokens': 90, 'cached_input_tokens': 195}
LEDGER_USAGES = ((120, 30, 40), (200, 50, 150), (70, 10, 5))
BEHAVIOR_VECTOR = [False, False, False, False, False, False, True,
                   False, True, True, False, True, True, True]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(path):
    return sha(Path(path).read_bytes())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode()


def candidate_core_digest():
    candidate = strict_json(CANDIDATE.read_bytes())
    core = {key: item for key, item in candidate.items()
            if key not in ('status', 'model_run_authorized', 'qualification')}
    return sha(canonical(core))


def host_python_runtime():
    base = Path(sys.base_prefix).resolve(strict=True)
    names = ('json', 'hashlib', 'sqlite3', 'subprocess', 'shutil',
             'pathlib', 'fcntl', 'os')
    origins = {}
    for name in names:
        module = sys.modules[name]
        source = getattr(module, '__file__', None)
        if source is None:
            origin = module.__spec__.origin
            if origin not in ('built-in', 'frozen'):
                raise ValueError('host Python import has unknown origin: ' + name)
            origins[name] = origin
        else:
            path = Path(source).resolve(strict=True)
            if not path.is_relative_to(base):
                raise ValueError('host Python import escaped pinned base: ' + name)
            origins[name] = str(path)
    return {'executable': sys.executable,
            'resolved_executable': str(Path(sys.executable).resolve(strict=True)),
            'base_prefix': str(base), 'module_origins': origins,
            'tree_sha256': runtime_manifest.digest(base)}


def committed(path):
    return subprocess.check_output(
        ['git', 'show', 'HEAD:' + path.relative_to(pilot.PUBLIC).as_posix()],
        cwd=pilot.PUBLIC, timeout=10)


def expected_contract(name):
    uncertain = name == 'wrong_cleanup_uncertain'
    if name not in TRACES:
        raise ValueError('unknown integrated case')
    return {
        'case': name, 'id': '02-b1-packaging-quiet', 'task': 'packaging',
        'arm': 'quiet', 'patch_kind': 'wrong', 'usage_kind': 'complete',
        'injected_cleanup_failure': uncertain,
        'real_cleanup_verified': True,
        'real_supervisor_tree_sha256': probe.WRONG_TREE,
        'provider_requests': 3, 'provider_errors': 0,
        'request_items': REQUEST_ITEMS, 'trace_event_shapes': SHAPES,
        'first_command': FIRST_COMMAND, 'exit_code': 0, 'timed_out': False,
        'cleanup_verified': not uncertain,
        'checkpoint_status': ('blocked_unverified_cleanup' if uncertain else 'captured'),
        'checkpoint_tree_sha256': None if uncertain else probe.WRONG_TREE,
        'expected_tree_sha256': probe.WRONG_TREE,
        'quality_status': None if uncertain else 'fail',
        'quality': None if uncertain else False,
        'quality_report_present': not uncertain,
        'snapshot_present': not uncertain,
        'evaluator_calls': 0 if uncertain else 1,
        'behavior_passed': None if uncertain else 6,
        'behavior_total': None if uncertain else 14,
        'behavior_case_pass_vector': None if uncertain else BEHAVIOR_VECTOR,
        'upstream_passed': None if uncertain else False,
        'evaluator_upstream_exit': None if uncertain else 1,
        'evaluator_upstream_failed_call_count': None if uncertain else 13,
        'evaluator_upstream_failed_call_ids_sha256': (
            None if uncertain else
            'e02c245d1f086f0e5ce5ea29c9742d119a2f35b80306a75c46d2866c9aed81d3'),
        'evaluator_upstream_inventory_sha256': (
            None if uncertain else
            '6da92136adbe79c030efa193028fb9190341e8e7203ce1c40efe24b9470111da'),
        'evaluator_upstream_unexpected_skips': None if uncertain else [],
        'technical_ok': not uncertain, 'admissible': not uncertain,
        'reconciliation_complete': True, 'usage': USAGE,
        'broker_usage_status': 'complete_observed', 'broker_attempts': 3,
        'distinct_attempt_ids': True, 'broker_bridge_attempt_ids_match': True,
        'ledger_errors': {'broker': False, 'bridge': False},
        'upstream_states': {'completed': 3, 'pending': 0, 'unknown': 0},
        'upstream_observed_completed_usage': USAGE,
        'provider_billing_complete': False,
        'broker_stopped': True, 'bridge_stopped': True,
        'copied_cli_sha256': bundled_cli.PINNED,
    }


def preflight():
    bundled_cli.validate_source_snapshot(
        'frozen_quiet_variance_bundle_integrated_v3.py', PINNED_FILES)
    raw = PLAN.read_bytes()
    plan = strict_json(raw)
    required = {'schema', 'status', 'model_run_authorized', 'case_order',
                'private_source_sha256', 'calibration_core_sha256',
                'cli_binary_sha256', 'code_mode_host_sha256', 'cli_version',
                'python_version', 'runtime_tree_sha256', 'evaluator_runtime_tree_sha256',
                'quality_assets_lock_sha256', 'usage_ledger_sha256',
                'source_tree_sha256', 'schedule_row', 'host_python_runtime',
                'control_timeout_seconds',
                'wrong_tree_sha256', 'expected_cases', 'reducer_sha256'}
    if (set(plan) != required
            or plan['schema'] != 'solcodex.quiet-variance-bundle-integrated-expectations.v1'
            or plan['status'] != 'prospective_no_model_development_control'
            or plan['model_run_authorized'] is not False
            or plan['case_order'] != [name for name, _ in probe.CASES]
            or plan['control_timeout_seconds'] != 90
            or plan['wrong_tree_sha256'] != probe.WRONG_TREE
            or plan['expected_cases'] != [expected_contract(name) for name, _ in probe.CASES]
            or set(plan['private_source_sha256']) != PINNED_FILES
            or sys.flags.optimize != 0):
        raise ValueError('invalid integrated control plan')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=pilot.PUBLIC,
                                   text=True, timeout=10).strip()
    remote = subprocess.check_output(['git', 'ls-remote', 'origin', 'refs/heads/main'],
                                     cwd=pilot.PUBLIC, text=True, timeout=20).split()[0]
    if raw != committed(PLAN) or head != remote:
        raise ValueError('integrated plan is not frozen on pushed main')
    published = (PLAN, PUBLIC_PROBE, PUBLIC_GATE, PUBLIC_NORMAL, PUBLIC_BUNDLE,
                 PUBLIC_RUNNER, PUBLIC_AUDIT, PUBLIC_REDUCER, CANDIDATE,
                 normal.SCHEDULE, LEDGER_SOURCE)
    if any(path.read_bytes() != committed(path) for path in published):
        raise ValueError('integrated public source differs from HEAD')
    observed = {name: digest(pilot.HERE / name) for name in PINNED_FILES}
    mirrors = {
        'cli_integrated_bundle_probe_v3.py': PUBLIC_PROBE,
        'frozen_quiet_variance_bundle_integrated_v3.py': PUBLIC_GATE,
        'cli_normal_bundle_probe_v3.py': PUBLIC_NORMAL,
        'bundled_cli_v3.py': PUBLIC_BUNDLE,
        'variance_calibration_bundle_v3.py': PUBLIC_RUNNER,
        'audit.py': PUBLIC_AUDIT,
    }
    if (observed != plan['private_source_sha256']
            or any(digest(path) != observed[name] for name, path in mirrors.items())
            or digest(PUBLIC_REDUCER) != plan['reducer_sha256']
            or LEDGER_SHA256 != plan['usage_ledger_sha256']
            or digest(LEDGER_SOURCE) != plan['usage_ledger_sha256']
            or LEDGER_SOURCE != pilot.PUBLIC / 'scripts/usage_attempt_ledger.py'
            or candidate_core_digest() != plan['calibration_core_sha256']
            or digest(bundled_cli.BUNDLE / 'codex') != plan['cli_binary_sha256']
            or digest(bundled_cli.BUNDLE / 'codex-code-mode-host')
                != plan['code_mode_host_sha256']
            or sys.version.split()[0] != plan['python_version']
            or host_python_runtime() != plan['host_python_runtime']):
        raise ValueError('integrated source or binary pins differ')
    bundled_cli.validate_bundle()
    candidate = strict_json(CANDIDATE.read_bytes())
    row = strict_json(normal.SCHEDULE.read_bytes())['schedule'][1]
    if (candidate['status'] != 'candidate'
            or candidate['model_run_authorized'] is not False
            or candidate['qualification']['evidence_sha256']['integrated_path'] is not None
            or candidate['cli']['sha256'] != plan['cli_binary_sha256']
            or candidate['cli']['code_mode_host_sha256'] != plan['code_mode_host_sha256']
            or candidate['cli']['version'] != plan['cli_version']
            or candidate['usage_ledger_sha256'] != plan['usage_ledger_sha256']
            or candidate['venv_tree_sha256'] != plan['runtime_tree_sha256']
            or candidate['quality']['evaluator_runtime_sha256']
                != plan['evaluator_runtime_tree_sha256']
            or candidate['quality']['assets_lock_sha256']
                != plan['quality_assets_lock_sha256']
            or candidate['source_tree_sha256']['packaging']
                != plan['source_tree_sha256']
            or row != plan['schedule_row']
            or row['id'] != '02-b1-packaging-quiet'
            or any(candidate['code_sha256'][name] != observed[name]
                   for name in calibration.SOURCE_FILES)
            or runtime_manifest.digest(pilot.VENV) != plan['runtime_tree_sha256']
            or evaluate.runtime_digest(pilot.VENV)
                != plan['evaluator_runtime_tree_sha256']
            or digest(pilot.HERE / 'quality-assets/lock.json')
                != plan['quality_assets_lock_sha256']
            or manifest(pilot.WORK / 'prehook-packaging-dev/fixture')['tree_sha256']
                != plan['source_tree_sha256']):
        raise ValueError('integrated candidate, runtime or baseline differs')
    evaluate.load_assets()
    return raw, plan, observed, head


def ledger_projection(case_root, name, final):
    host = case_root / '02-b1-packaging-quiet/host-artifacts'
    attempts = final['broker']['requests']
    ids = [item['attempt_id'] for item in attempts]
    if len(ids) != 3 or len(set(ids)) != 3:
        raise ValueError('integrated attempt identifiers differ')
    projection = {'schema': 'solcodex.quiet-variance-bundle-ledger-projection.v1',
                  'case': name, 'attempt_ids_match': True,
                  'raw_sha256': {}}
    for kind, filename in (('delivery', 'delivery.sqlite3'),
                           ('upstream', 'upstream.sqlite3')):
        path = host / filename
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1',
                                     uri=True)) as connection:
            if connection.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise ValueError('integrated ledger integrity failed')
            rows = connection.execute(
                'SELECT attempt_id,state,input_tokens,output_tokens,'
                'cached_input_tokens,reason FROM attempts ORDER BY rowid').fetchall()
        if (len(rows) != 3 or [row[0] for row in rows] != ids
                or any(row[1] != 'completed' or row[2:5] != usage or row[5] is not None
                       for row, usage in zip(rows, LEDGER_USAGES))):
            raise ValueError('integrated ledger rows differ')
        projection[kind] = [{'ordinal': index, 'state': row[1],
                             'input_tokens': row[2], 'output_tokens': row[3],
                             'cached_input_tokens': row[4], 'reason': row[5]}
                            for index, row in enumerate(rows, 1)]
        projection['raw_sha256'][kind] = digest(path)
    return projection


def verify_case(case, root):
    name = case['case']
    case_root = root / name
    host = case_root / '02-b1-packaging-quiet/host-artifacts'
    final = strict_json((host / 'final.json').read_bytes())
    cleanup_path = case_root / 'cleanup-observation.json'
    cleanup = strict_json(cleanup_path.read_bytes())
    expected = expected_contract(name)
    if (final['task'] != 'packaging' or final['arm'] != 'quiet'
            or final['id'] != expected['id']
            or case['private_host_files_sha256'] != {
                path.name: digest(path) for path in host.iterdir() if path.is_file()}
            or case['trace_sha256'] != digest(host / 'trace.jsonl')
            or case['patch_sha256'] != sha(probe.wrong_patch(
                case_root / '02-b1-packaging-quiet/workspace/src/packaging/licenses/__init__.py'
            ).encode())
            or case['copied_cli_sha256'] != bundled_cli.PINNED
            or cleanup['actual_receipt'].get('verified') is not True
            or cleanup['actual_tree_sha256'] != probe.WRONG_TREE
            or cleanup['returned_receipt'] != final['cleanup']
            or case['real_supervisor_tree_sha256'] != probe.WRONG_TREE
            or case['real_cleanup_verified'] is not True
            or manifest(case_root / '02-b1-packaging-quiet/workspace')['tree_sha256']
                != probe.WRONG_TREE):
        raise ValueError('integrated private artifact or cleanup differs')
    events = [strict_json(line) for line in (host / 'trace.jsonl').read_bytes().splitlines()]
    shapes = [[event['type'], (event.get('item') or {}).get('type'),
               (event.get('item') or {}).get('status')] for event in events]
    command = events[2]['item']
    completed = events[3]['item']
    outer = shlex.split(command['command'])
    diagnostic = pilot.diagnostic('packaging', 'quiet',
        case_root / '02-b1-packaging-quiet/tools/venv/bin/python')
    changes = events[4]['item'].get('changes')
    if (shapes != SHAPES or case['trace_event_shapes'] != SHAPES
            or len(outer) != 3 or outer[:2] != ['/bin/zsh', '-lc']
            or outer[2] != diagnostic
            or command['id'] != 'item_0' or completed['id'] != 'item_0'
            or events[4]['item']['id'] != 'item_1'
            or events[5]['item']['id'] != 'item_1'
            or completed['exit_code'] != 1
            or 'FAILED tests/test_metadata.py::test_nested_spdx_regression_probe'
                not in completed.get('aggregated_output', '')
            or '1 failed, 290 passed' not in completed.get('aggregated_output', '')
            or changes != [{'path': str(case_root /
                '02-b1-packaging-quiet/workspace/src/packaging/licenses/__init__.py'),
                'kind': 'update'}]
            or events[5]['item'].get('changes') != changes
            or {key: events[6]['usage'].get(key) for key in USAGE} != USAGE
            or final['first_command'] != FIRST_COMMAND):
        raise ValueError('integrated CLI trace differs')
    ledger = ledger_projection(case_root, name, final)
    if (case['broker_attempts'] != 3
            or case['ledger_errors'] != {'broker': False, 'bridge': False}
            or case['upstream_observed_completed_usage'] != USAGE
            or final['bridge']['accounting']['states']
                != {'completed': 3, 'pending': 0, 'unknown': 0}
            or final['usage'] != USAGE
            or final['reconciliation']['complete'] is not True):
        raise ValueError('integrated accounting differs')
    uncertain = name == 'wrong_cleanup_uncertain'
    if uncertain:
        if (cleanup['returned_receipt'].get('verified') is not False
                or cleanup['returned_receipt'].get('watch_errors')
                    != ['injected_cleanup_uncertainty']
                or final['checkpoint']['status'] != 'blocked_unverified_cleanup'
                or 'quality_status' in final or 'quality_report_root' in final
                or (host / 'snapshot').exists()
                or case['evaluator_calls'] != 0
                or calibration.admissible(final) is not False):
            raise ValueError('integrated cleanup-negative gate differs')
        try:
            calibration.evidence_hashes(case_root, final['id'], final)
        except ValueError as error:
            if str(error) != 'checkpoint manifest differs':
                raise
        else:
            raise ValueError('cleanup-negative was accepted by evidence gate')
    else:
        report_path = Path(final['quality_report_root']) / 'report.json'
        report = strict_json(report_path.read_bytes())
        failed = sorted({item['nodeid'] for item in report['upstream']['reports']
                         if item['outcome'] == 'failed' and item['when'] == 'call'})
        if (cleanup['returned_receipt'] != cleanup['actual_receipt']
                or final['checkpoint']['status'] != 'captured'
                or final['checkpoint']['manifest']['tree_sha256'] != probe.WRONG_TREE
                or case['quality_report_sha256'] != digest(report_path)
                or report.get('evidence_sha256') != digest(host / 'result.json')
                or report.get('assets_lock_sha256') != evaluate.ASSETS_LOCK_SHA256
                or report.get('candidate_tree_sha256') != probe.WRONG_TREE
                or report.get('status') != 'fail' or report.get('quality') is not False
                or report.get('changed_paths')
                    != ['src/packaging/licenses/__init__.py']
                or report.get('canaries') != {'host_read': True, 'host_write': True,
                                             'network': True, 'source_write': True}
                or report.get('restored_source_unchanged') is not True
                or report.get('behavior_passed') != 6
                or report.get('behavior_total') != 14
                or [item.get('passed') for item in report['cases']] != BEHAVIOR_VECTOR
                or report['upstream_process']['exit_code'] != 1
                or len(failed) != 13
                or sha(json.dumps(failed, separators=(',', ':')).encode())
                    != expected['evaluator_upstream_failed_call_ids_sha256']
                or report['upstream']['inventory_sha256']
                    != expected['evaluator_upstream_inventory_sha256']
                or report['upstream']['finished'] is not True
                or report['upstream']['collection_errors'] != []
                or report['upstream']['unexpected_skips'] != []
                or case['evaluator_calls'] != 1
                or calibration.admissible(final) is not True
                or calibration.evidence_hashes(case_root, final['id'], final)
                    ['host_files'] != case['private_host_files_sha256']):
            raise ValueError('integrated wrong-repair evaluator differs')
    copied = dict(case)
    private_files = copied.pop('private_host_files_sha256')
    copied.update(sealed_inventory_sha256=sha(canonical(private_files)),
                  cleanup_observation_sha256=digest(cleanup_path),
                  ledger_projection_sha256=sha(canonical(ledger)))
    return copied, ledger


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
        if (RESULT.exists() or any(path.exists() for path in (*TRACES.values(), *LEDGERS.values()))
                or shutil.disk_usage(pilot.HERE).free < 1_200_000_000):
            raise ValueError('integrated output exists or free space is insufficient')
        marker = pilot.HERE / 'integrated-v3-launch-marker.json'
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump({'expectations_sha256': sha(raw), 'public_head': head}, stream)
        root = Path(tempfile.mkdtemp(prefix='cli-integrated-scored-v3-', dir=pilot.HERE))
        protocol = strict_json(CANDIDATE.read_bytes())
        protocol['timeout_seconds'] = 90
        cases = []
        ledgers = {}
        for spec in probe.CASES:
            before = preflight()
            if before != (raw, plan, observed, head):
                raise ValueError('integrated pins drifted before case')
            observation = probe.one(spec, root, protocol)
            after = preflight()
            if after != (raw, plan, observed, head):
                raise ValueError('integrated pins drifted after case')
            if observation['real_cleanup_verified'] is not True:
                raise ValueError('actual supervisor cleanup uncertain; stop')
            case, ledger = verify_case(observation, root)
            cases.append(case)
            ledgers[case['case']] = ledger
            (root / 'partial-results.json').write_text(json.dumps(cases, sort_keys=True,
                                                       indent=2) + '\n')
        mismatches = []
        for case, expected in zip(cases, plan['expected_cases']):
            mismatches += [case['case'] + ':' + key for key, item in expected.items()
                           if canonical(case.get(key)) != canonical(item)]
            if set(case) != set(expected) | DYNAMIC:
                mismatches.append(case['case'] + ':key_set')
        for case in cases:
            name = case['case']
            host = root / name / '02-b1-packaging-quiet/host-artifacts'
            TRACES[name].write_bytes((host / 'trace.jsonl').read_bytes())
            LEDGERS[name].write_text(json.dumps(ledgers[name], sort_keys=True,
                                                indent=2) + '\n')
            case['ledger_projection_sha256'] = digest(LEDGERS[name])
        output = {'schema': 'solcodex.quiet-variance-bundle-integrated-observations.v1',
                  'scope': 'pinned_bundle_no_model_real_cli_integration',
                  'expectations_sha256': sha(raw),
                  'private_source_sha256': observed, 'public_head': head,
                  'cli_bundle_sha256': bundled_cli.PINNED,
                  'predeclared_match': not mismatches,
                  'mismatched_fields': sorted(mismatches),
                  'private_artifacts_verified': True, 'cases': cases}
        RESULT.write_text(json.dumps(output, sort_keys=True, indent=2) + '\n')
        print(json.dumps({'result_path': str(RESULT),
                          'predeclared_match': not mismatches,
                          'mismatched_fields': sorted(mismatches)}, sort_keys=True))
        return 0 if not mismatches else 1


if __name__ == '__main__':
    sys.exit(main())
