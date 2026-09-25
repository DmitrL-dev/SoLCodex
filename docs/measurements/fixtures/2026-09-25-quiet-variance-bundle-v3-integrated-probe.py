"""Exploratory no-model integration matrix using the pinned real CLI bundle."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

import bundled_cli_v3 as bundled_cli
import cli_normal_bundle_probe_v3 as normal
import cli_toolcall_probe as wire
import live_pilot
import pilot
from process import run_managed
from protocol_gate import strict_json
from snapshot import manifest
from test_host_bridge import provider, send
import variance_calibration_bundle_v3 as calibration


CASES = (
    ('wrong_complete', False),
    ('wrong_cleanup_uncertain', True),
)
WRONG_TREE = '607c7f277ab7b7ff9bbe94a77feb5e6aee3f3e131cc3abc63d06d71d14b509b5'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def wrong_patch(target):
    return ('*** Begin Patch\n*** Update File: ' + str(target) + '\n'
            '@@\n'
            '-    if not raw_license_expression:\n'
            '+    raw_license_expression = raw_license_expression.replace("(", "").replace(")", "")\n'
            '+    if not raw_license_expression:\n'
            '@@\n'
            '-        elif token == "(" and python_tokens and python_tokens[-1] not in {"or", "and"}:\n'
            '+        elif (\n'
            '+            token == "("\n'
            '+            and python_tokens\n'
            '+            and python_tokens[-1] not in {"or", "and", "("}\n'
            '+        ):\n'
            '             message = f"Invalid license expression: {raw_license_expression!r}"\n'
            '*** End Patch')


def one(spec, directory, protocol):
    name, cleanup_failure = spec
    row = strict_json(normal.SCHEDULE.read_bytes())['schedule'][1]
    if row['task'] != 'packaging' or row['arm'] != 'quiet':
        raise ValueError('integrated row differs')
    case_root = directory / name
    case_root.mkdir(mode=0o700)
    run_root = case_root / row['id']
    work = run_root / 'workspace'
    command = pilot.diagnostic('packaging', 'quiet', run_root / 'tools/venv/bin/python')
    first_js = ('const r=await tools.exec_command({cmd:' + json.dumps(command) +
                ',workdir:' + json.dumps(str(work)) +
                ',yield_time_ms:30000,max_output_tokens:700}); text(r.output);')
    target = work / 'src/packaging/licenses/__init__.py'
    patch_text = wrong_patch(target)
    second_js = 'const r=await tools.apply_patch(' + json.dumps(patch_text) + '); text(r);'
    replies = (
        wire.payload('resp_synthetic_1', normal.custom(1, first_js), normal.USAGE[0]),
        wire.payload('resp_synthetic_2', normal.custom(2, second_js), normal.USAGE[1]),
        wire.payload('resp_synthetic_3', None, normal.USAGE[2]),
    )
    calls = []
    calls_lock = threading.Lock()
    real_cleanup = {}
    evaluator_calls = []

    def action(handler):
        with calls_lock:
            calls.append(1)
            index = len(calls)
        if index > len(replies):
            raise ValueError('unexpected provider request')
        send(handler, replies[index - 1], declared=len(replies[index - 1]))

    def bundled_plan(profile, message):
        return bundled_cli.model_plan(run_root, profile, message)

    def runner(argv, cwd, env, remaining):
        process, timed_out, cleanup = run_managed(argv, cwd, env, remaining)
        real_cleanup.update(cleanup)
        actual_tree = manifest(cwd)['tree_sha256'] if cleanup.get('verified') else None
        if cleanup_failure:
            cleanup = dict(cleanup, verified=False,
                           watch_errors=['injected_cleanup_uncertainty'])
        (case_root / 'cleanup-observation.json').write_text(json.dumps({
            'actual_receipt': real_cleanup,
            'returned_receipt': cleanup,
            'actual_tree_sha256': actual_tree,
        }, sort_keys=True, indent=2) + '\n')
        return process, timed_out, cleanup

    original_evaluate = live_pilot.evaluate

    def counted_evaluate(*args, **kwargs):
        evaluator_calls.append(1)
        return original_evaluate(*args, **kwargs)

    with provider(action) as (factory, requests, errors, _):
        with patch.object(live_pilot, 'model_plan', side_effect=bundled_plan):
            with patch.object(live_pilot, 'evaluate', side_effect=counted_evaluate):
                final = live_pilot.one_live(
                    row, case_root, protocol,
                    credential_supplier=lambda: 'synthetic-token-never-real',
                    provider_connection_factory=factory, runner=runner)
    host = run_root / 'host-artifacts'
    recorded = strict_json((host / 'final.json').read_bytes())
    if recorded != final:
        raise ValueError('integrated final artifact differs')
    trace = (host / 'trace.jsonl').read_bytes()
    trace_events = [strict_json(line) for line in trace.splitlines()]
    report_path = (Path(final['quality_report_root']) / 'report.json'
                   if final.get('quality_report_root') else None)
    report = strict_json(report_path.read_bytes()) if report_path else None
    cleanup_observation = strict_json((case_root / 'cleanup-observation.json').read_bytes())
    broker_rows = final['broker']['requests']
    bridge_rows = final['bridge']['attempts']
    failed_calls = (sorted({item['nodeid'] for item in report['upstream']['reports']
                            if item['outcome'] == 'failed' and item['when'] == 'call'})
                    if report else None)
    request_items = []
    for _, _, raw in requests:
        body = strict_json(raw)
        request_items.append([{'type': item.get('type'), 'call_id': item.get('call_id')}
            for item in body.get('input', []) if isinstance(item, dict) and
            item.get('type') in ('custom_tool_call', 'custom_tool_call_output')])
    shapes = []
    for item in trace_events:
        body = item.get('item') or {}
        shapes.append([item['type'], body.get('type'), body.get('status')])
    return {
        'case': name, 'id': row['id'], 'task': row['task'], 'arm': row['arm'],
        'patch_kind': 'wrong', 'usage_kind': 'complete',
        'injected_cleanup_failure': cleanup_failure,
        'real_cleanup_verified': real_cleanup.get('verified'),
        'real_supervisor_tree_sha256': cleanup_observation['actual_tree_sha256'],
        'provider_requests': len(requests), 'provider_errors': len(errors),
        'request_items': request_items, 'trace_event_shapes': shapes,
        'trace_sha256': sha(trace), 'patch_sha256': sha(patch_text.encode()),
        'first_command': final.get('first_command'),
        'exit_code': final.get('exit_code'), 'timed_out': final.get('timed_out'),
        'cleanup_verified': final.get('cleanup', {}).get('verified'),
        'checkpoint_status': final.get('checkpoint', {}).get('status'),
        'checkpoint_tree_sha256': final.get('checkpoint', {}).get('manifest', {}).get('tree_sha256'),
        'expected_tree_sha256': WRONG_TREE,
        'quality_status': final.get('quality_status'), 'quality': final.get('quality'),
        'quality_report_sha256': final.get('quality_report_sha256'),
        'quality_report_present': report is not None,
        'snapshot_present': (host / 'snapshot').exists(),
        'evaluator_calls': len(evaluator_calls),
        'behavior_passed': report.get('behavior_passed') if report else None,
        'behavior_total': report.get('behavior_total') if report else None,
        'behavior_case_pass_vector': ([item.get('passed') for item in report['cases']]
                                      if report else None),
        'upstream_passed': report.get('upstream', {}).get('passed') if report else None,
        'evaluator_upstream_exit': (report['upstream_process']['exit_code']
                                    if report else None),
        'evaluator_upstream_failed_call_count': (len(failed_calls)
                                                  if failed_calls is not None else None),
        'evaluator_upstream_failed_call_ids_sha256': (
            sha(json.dumps(failed_calls, separators=(',', ':')).encode())
            if failed_calls is not None else None),
        'evaluator_upstream_inventory_sha256': (report['upstream']['inventory_sha256']
                                                 if report else None),
        'evaluator_upstream_unexpected_skips': (report['upstream']['unexpected_skips']
                                                if report else None),
        'technical_ok': final.get('technical_ok'),
        'admissible': calibration.admissible(final),
        'reconciliation_complete': final.get('reconciliation', {}).get('complete'),
        'usage': final.get('usage'),
        'broker_usage_status': final.get('broker', {}).get('usage_status'),
        'broker_attempts': len(broker_rows),
        'distinct_attempt_ids': len({item['attempt_id'] for item in broker_rows}) == 3,
        'broker_bridge_attempt_ids_match': (
            {item['attempt_id'] for item in broker_rows} == set(bridge_rows)),
        'ledger_errors': {'broker': final['broker']['ledger_error'],
                          'bridge': final['bridge']['ledger_error']},
        'upstream_states': final.get('bridge', {}).get('accounting', {}).get('states'),
        'upstream_observed_completed_usage': (
            final['bridge']['accounting']['observed_completed_usage']),
        'provider_billing_complete': final.get('provider_billing_complete'),
        'broker_stopped': final.get('broker_stopped'),
        'bridge_stopped': final.get('bridge_stopped'),
        'private_host_files_sha256': {path.name: sha(path.read_bytes())
                                      for path in host.iterdir() if path.is_file()},
        'copied_cli_sha256': {name: bundled_cli.sha(run_root / 'tools' / name)
                              for name in bundled_cli.PINNED},
    }


def run(limit=4):
    os.umask(0o077)
    directory = Path(tempfile.mkdtemp(prefix='cli-integrated-bundle-v3-', dir=pilot.HERE))
    protocol = strict_json(normal.CANDIDATE.read_bytes())
    protocol['timeout_seconds'] = 90
    observations = []
    for spec in CASES[:limit]:
        item = one(spec, directory, protocol)
        observations.append(item)
        (directory / 'probe-summary.json').write_text(json.dumps(
            {'private_root': str(directory), 'observations': observations,
             'unstarted_cases': [case[0] for case in CASES[len(observations):]]},
            sort_keys=True, indent=2) + '\n')
        if item['real_cleanup_verified'] is not True:
            break
    return {'private_root': str(directory), 'observations': observations,
            'unstarted_cases': [case[0] for case in CASES[len(observations):]]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, choices=range(1, 3), default=2)
    args = parser.parse_args()
    print(json.dumps(run(args.limit), sort_keys=True))
