"""No-model real-CLI patch, cancellation, and late-usage bundle control."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

import cli_toolcall_probe as wire
import bundled_cli_v3 as bundled_cli
import live_pilot
import pilot
from process import run_managed
from test_host_bridge import provider, send


GOLD_TREE = 'ad9f932ad4671fa1c3d080fc96bc042722c88d6bdfd06bf15ed7590b7841d62d'


def custom(identifier, javascript):
    return {'id':'ctc_synthetic_' + str(identifier),
            'type':'custom_tool_call','status':'completed',
            'call_id':'call_synthetic_' + str(identifier),
            'namespace':'functions','name':'exec','input':javascript}


def run():
    os.umask(0o077)
    directory = Path(tempfile.mkdtemp(prefix='cli-late-checkpoint-bundle-', dir=pilot.HERE))
    protocol = json.loads((pilot.HERE / 'selftest-protocol.json').read_text())
    protocol['timeout_seconds'] = 45
    row = protocol['schedule'][2]
    assert row['task'] == 'packaging' and row['arm'] == 'quiet'
    root = directory / row['id']
    work = root / 'workspace'
    diagnostic = pilot.diagnostic('packaging', 'quiet', root / 'tools/venv/bin/python')
    first_js = ('const r=await tools.exec_command({cmd:' + json.dumps(diagnostic) +
                ',workdir:' + json.dumps(str(work)) +
                ',yield_time_ms:30000,max_output_tokens:700}); text(r.output);')
    target = work / 'src/packaging/licenses/__init__.py'
    source_patch = ('*** Begin Patch\n*** Update File: ' + str(target) + '\n'
        '@@\n'
        '-        elif token == "(" and python_tokens and python_tokens[-1] not in {"or", "and"}:\n'
        '+        elif (\n'
        '+            token == "("\n'
        '+            and python_tokens\n'
        '+            and python_tokens[-1] not in {"or", "and", "("}\n'
        '+        ):\n'
        '             message = f"Invalid license expression: {raw_license_expression!r}"\n'
        '*** End Patch')
    second_js = 'const r=await tools.apply_patch(' + json.dumps(source_patch) + '); text(r);'
    responses = (
        wire.payload('resp_synthetic_1', custom(1, first_js), (120,30,40)),
        wire.payload('resp_synthetic_2', custom(2, second_js), (200,50,150)),
        wire.payload('resp_synthetic_3', None, (70,10,5)),
    )
    runner_done = threading.Event()
    third_seen = threading.Event()
    late_completion = threading.Event()
    calls = []
    calls_lock = threading.Lock()

    def action(handler):
        with calls_lock:
            calls.append(1)
            index = len(calls)
        if index == 3:
            third_seen.set()
            if not runner_done.wait(30):
                raise TimeoutError('runner did not finish before late provider response')
            late_completion.set()
        if 1 <= index <= 3:
            send(handler, responses[index-1], declared=len(responses[index-1]))
        else:
            raise ValueError('unexpected provider request')

    def short_runner(argv, cwd, env, remaining):
        try:
            return run_managed(argv, cwd, env, min(remaining, 10))
        finally:
            runner_done.set()

    def bundled_plan(profile, message):
        return bundled_cli.model_plan(root, profile, message)

    with provider(action) as (factory, requests, errors, _):
        with patch.object(live_pilot, 'model_plan', side_effect=bundled_plan):
            result = live_pilot.one_live(row, directory, protocol,
                credential_supplier=lambda:'synthetic-token-never-real',
                provider_connection_factory=factory, runner=short_runner)
    request_items = []
    for _,_,raw in requests:
        body = json.loads(raw)
        request_items.append([{'type':item.get('type'),'call_id':item.get('call_id')}
            for item in body.get('input',[]) if isinstance(item,dict) and
            item.get('type') in ('custom_tool_call','custom_tool_call_output')])
    host = root / 'host-artifacts'
    trace = (host / 'trace.jsonl').read_text()
    shapes = []
    diagnostic_signature = False
    for line in trace.splitlines():
        value = json.loads(line)
        item = value.get('item') or {}
        shapes.append((value['type'],item.get('type'),item.get('status')))
        if value['type'] == 'item.completed' and item.get('type') == 'command_execution':
            output = item.get('aggregated_output', '')
            diagnostic_signature = ('1 failed, 290 passed' in output and
                'FAILED tests/test_metadata.py::test_nested_spdx_regression_probe' in output)
    digest = lambda data:hashlib.sha256(data).hexdigest()
    summary = {'scope':'pinned_bundle_no_model_real_cli_late_checkpoint',
        'private_root':str(directory),'provider_requests':len(requests),
        'provider_errors':len(errors),'third_request_seen':third_seen.is_set(),
        'late_completion_after_runner':late_completion.is_set(),
        'request_items':request_items,'trace_event_shapes':shapes,
        'diagnostic_signature':diagnostic_signature,
        'trace_sha256':digest(trace.encode()),
        'exit_code':result.get('exit_code'),'timed_out':result.get('timed_out'),
        'cleanup_verified':result.get('cleanup',{}).get('verified'),
        'checkpoint_status':result.get('checkpoint',{}).get('status'),
        'checkpoint_tree_sha256':result.get('checkpoint',{}).get('manifest',{}).get('tree_sha256'),
        'gold_tree_sha256':GOLD_TREE,'source_unchanged':result.get('source_unchanged'),
        'first_command':result.get('first_command'),
        'reconciliation_complete':result.get('reconciliation',{}).get('complete'),
        'broker_attempts':len(result.get('broker',{}).get('requests',[])),
        'upstream_states':result.get('bridge',{}).get('accounting',{}).get('states'),
        'usage':result.get('usage'),'quality_status':result.get('quality_status'),
        'quality_report_sha256':result.get('quality_report_sha256'),
        'technical_ok':result.get('technical_ok'),
        'broker_stopped':result.get('broker_stopped'),
        'bridge_stopped':result.get('bridge_stopped')}
    (directory / 'probe-summary.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n')
    return summary


if __name__ == '__main__':
    print(json.dumps(run(), sort_keys=True))
