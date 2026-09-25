"""Exploratory no-model, normal-completion matrix for the pinned CLI bundle."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

import bundled_cli_v3 as bundled_cli
import cli_toolcall_probe as wire
import live_pilot
import pilot
from protocol_gate import strict_json
from test_host_bridge import provider, send
import variance_calibration_bundle_v3 as calibration


SCHEDULE = pilot.PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-calibration-schedule.json'
CANDIDATE = pilot.PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-calibration-bundle-v3-candidate.json'
CLICK_PATCH = pilot.HERE / 'click_source_only_patch_v3.txt'
GOLD_TREES = {
    'click': '715e83baae94b5b3271ff19de66e96e13fa85cafaeebb28a123df11f3b4f7227',
    'packaging': 'ad9f932ad4671fa1c3d080fc96bc042722c88d6bdfd06bf15ed7590b7841d62d',
}
USAGE = ((120, 30, 40), (200, 50, 150), (70, 10, 5))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def custom(identifier, javascript):
    return {'id': 'ctc_synthetic_' + str(identifier),
            'type': 'custom_tool_call', 'status': 'completed',
            'call_id': 'call_synthetic_' + str(identifier),
            'namespace': 'functions', 'name': 'exec', 'input': javascript}


def packaging_patch(target):
    return ('*** Begin Patch\n*** Update File: ' + str(target) + '\n'
        '@@\n'
        '-        elif token == "(" and python_tokens and python_tokens[-1] not in {"or", "and"}:\n'
        '+        elif (\n'
        '+            token == "("\n'
        '+            and python_tokens\n'
        '+            and python_tokens[-1] not in {"or", "and", "("}\n'
        '+        ):\n'
        '             message = f"Invalid license expression: {raw_license_expression!r}"\n'
        '*** End Patch')


def source_patch(task, work):
    if task == 'packaging':
        return packaging_patch(work / 'src/packaging/licenses/__init__.py')
    if task == 'click':
        patch_text = CLICK_PATCH.read_text()
        expected = '*** Update File: src/click/core.py'
        if patch_text.count(expected) != 1:
            raise ValueError('Click patch fixture differs')
        return patch_text.replace(expected,
                                  '*** Update File: ' + str(work / 'src/click/core.py'))
    raise ValueError('unknown task')


def one(row, directory, protocol):
    run_root = directory / row['id']
    work = run_root / 'workspace'
    diagnostic = pilot.diagnostic(row['task'], row['arm'], run_root / 'tools/venv/bin/python')
    first_js = ('const r=await tools.exec_command({cmd:' + json.dumps(diagnostic) +
                ',workdir:' + json.dumps(str(work)) +
                ',yield_time_ms:30000,max_output_tokens:700}); text(r.output);')
    patch_text = source_patch(row['task'], work)
    second_js = 'const r=await tools.apply_patch(' + json.dumps(patch_text) + '); text(r);'
    replies = (
        wire.payload('resp_synthetic_1', custom(1, first_js), USAGE[0]),
        wire.payload('resp_synthetic_2', custom(2, second_js), USAGE[1]),
        wire.payload('resp_synthetic_3', None, USAGE[2]),
    )
    calls = []
    calls_lock = threading.Lock()

    def action(handler):
        with calls_lock:
            calls.append(1)
            index = len(calls)
        if index > len(replies):
            raise ValueError('unexpected provider request')
        reply = replies[index - 1]
        send(handler, reply, declared=len(reply))

    def bundled_plan(profile, message):
        return bundled_cli.model_plan(run_root, profile, message)

    with provider(action) as (factory, requests, errors, _):
        with patch.object(live_pilot, 'model_plan', side_effect=bundled_plan):
            final = live_pilot.one_live(row, directory, protocol,
                credential_supplier=lambda: 'synthetic-token-never-real',
                provider_connection_factory=factory)

    host = run_root / 'host-artifacts'
    trace = (host / 'trace.jsonl').read_bytes()
    recorded = strict_json((host / 'final.json').read_bytes())
    if recorded != final or sha(trace) != final['trace_sha256']:
        raise ValueError('final or trace differs after run')
    sealed = calibration.evidence_hashes(directory, row['id'], final)
    request_items = []
    for _, _, raw in requests:
        body = strict_json(raw)
        request_items.append([{'type': item.get('type'), 'call_id': item.get('call_id')}
            for item in body.get('input', []) if isinstance(item, dict) and
            item.get('type') in ('custom_tool_call', 'custom_tool_call_output')])
    shapes = []
    for line in trace.splitlines():
        event = strict_json(line)
        item = event.get('item') or {}
        shapes.append([event['type'], item.get('type'), item.get('status')])
    return {
        'id': row['id'], 'task': row['task'], 'arm': row['arm'],
        'provider_requests': len(requests), 'provider_errors': len(errors),
        'request_items': request_items, 'trace_event_shapes': shapes,
        'trace_sha256': sha(trace), 'patch_sha256': sha(patch_text.encode()),
        'first_command': final.get('first_command'),
        'exit_code': final.get('exit_code'), 'timed_out': final.get('timed_out'),
        'cleanup_verified': final.get('cleanup', {}).get('verified'),
        'checkpoint_status': final.get('checkpoint', {}).get('status'),
        'checkpoint_tree_sha256': final.get('checkpoint', {}).get('manifest', {}).get('tree_sha256'),
        'expected_gold_tree_sha256': GOLD_TREES[row['task']],
        'quality_status': final.get('quality_status'), 'quality': final.get('quality'),
        'quality_report_sha256': final.get('quality_report_sha256'),
        'technical_ok': final.get('technical_ok'),
        'reconciliation_complete': final.get('reconciliation', {}).get('complete'),
        'usage': final.get('usage'), 'admissible': calibration.admissible(final),
        'broker_stopped': final.get('broker_stopped'),
        'bridge_stopped': final.get('bridge_stopped'),
        'sealed': sealed,
    }


def run(limit=4):
    os.umask(0o077)
    directory = Path(tempfile.mkdtemp(prefix='cli-normal-bundle-v3-', dir=pilot.HERE))
    protocol = strict_json(CANDIDATE.read_bytes())
    protocol['timeout_seconds'] = 90
    rows = strict_json(SCHEDULE.read_bytes())['schedule'][:4]
    if [(row['task'], row['arm']) for row in rows] != [
            ('packaging', 'verbose'), ('packaging', 'quiet'),
            ('click', 'quiet'), ('click', 'verbose')]:
        raise ValueError('first block changed')
    observations = []
    for row in rows[:limit]:
        observation = one({key: row[key] for key in ('id', 'task', 'arm')},
                          directory, protocol)
        observations.append(observation)
        if observation['cleanup_verified'] is not True:
            break
    result = {'scope': 'pinned_bundle_no_model_normal_cli_matrix',
              'private_root': str(directory), 'observations': observations,
              'unstarted_ids': [row['id'] for row in rows[len(observations):]]}
    (directory / 'probe-summary.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, choices=range(1, 5), default=4)
    args = parser.parse_args()
    print(json.dumps(run(args.limit), sort_keys=True))
