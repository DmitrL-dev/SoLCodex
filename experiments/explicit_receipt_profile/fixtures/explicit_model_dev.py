"""One-shot exposed packaging repair pair for the explicit receipt package."""

from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from unittest.mock import patch

import evaluate
import explicit_package_probe
import live_pilot
import pilot
import runtime_manifest
from process import run_managed
from snapshot import manifest


BASE = pilot.PUBLIC / 'experiments/explicit_receipt_profile'
PLAN = BASE / 'model_dev_plan.json'
PUBLIC_CONTROLLER = BASE / 'fixtures/explicit_model_dev.py'
RUN_ROOT = pilot.HERE / 'explicit-model-dev-2026-09-25'
CLI = Path('/Applications/ChatGPT.app/Contents/Resources/codex')
CHILD_ARGS = ['-m', 'pytest', 'tests/test_metadata.py', '-v',
              '-p', 'no:cacheprovider', '-o', 'addopts=']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def preflight():
    raw = PLAN.read_bytes()
    plan = json.loads(raw)
    if (plan['schema'] != 'solcodex.explicit-model-dev-plan.v1'
            or plan['model_run_authorized'] is not True
            or plan['arm_order'] != ['on', 'off']
            or plan['case'] != 'exposed_packaging_928'
            or plan['child_args'] != CHILD_ARGS):
        raise ValueError('invalid frozen plan')
    if digest(PUBLIC_CONTROLLER) != digest(__file__) or \
            digest(__file__) != plan['private_source_sha256']['explicit_model_dev.py']:
        raise ValueError('controller differs from published freeze')
    if ({name: digest(pilot.HERE / name) for name in plan['private_source_sha256']}
            != plan['private_source_sha256']):
        raise ValueError('private source changed')
    if ({name: digest(BASE / name) for name in plan['package_sha256']}
            != plan['package_sha256']):
        raise ValueError('package changed')
    frozen = subprocess.run(['git', 'show', 'origin/main:experiments/'
        'explicit_receipt_profile/model_dev_plan.json'], cwd=pilot.PUBLIC,
        capture_output=True, check=True, timeout=10).stdout
    if frozen != raw:
        raise ValueError('plan not frozen at origin/main')
    protocol = json.loads((pilot.HERE / 'selftest-protocol.json').read_text())
    if (digest(CLI) != plan['cli_binary_sha256']
            or sys.version.split()[0] != plan['python_version']
            or protocol['source_tree_sha256']['packaging'] != plan['source_tree_sha256']
            or protocol['venv_tree_sha256'] != plan['runtime_tree_sha256']
            or manifest(pilot.WORK / 'prehook-packaging-dev/fixture')['tree_sha256']
            != plan['source_tree_sha256']
            or runtime_manifest.digest(pilot.VENV) != plan['runtime_tree_sha256']
            or evaluate.runtime_digest(pilot.VENV) != plan['evaluator_runtime_tree_sha256']):
        raise ValueError('CLI, fixture, or runtime differs from freeze')
    protocol['timeout_seconds'] = plan['timeout_seconds']
    protocol['request_cap'] = plan['request_cap']
    return raw, plan, protocol


def run_arm(arm, protocol):
    on = arm == 'on'
    row = {'id': '01-packaging-on' if on else '02-packaging-off',
           'task': 'packaging', 'arm': arm}

    def diagnostic(task, assigned_arm, python):
        if task != 'packaging' or assigned_arm != arm:
            raise ValueError('unexpected task or arm')
        child = [str(python), *CHILD_ARGS]
        if not on:
            return shlex.join(child)
        root = Path(python).parents[3]
        adapter = (root / 'home/plugins/cache/sol-codex-explicit/'
                   'sol-codex-explicit/0.1.0/scripts/receipt_command.py')
        return shlex.join([str(python), str(adapter), '--artifact-dir',
                           str(root / 'agent-artifacts'), '--timeout-seconds',
                           '300', '--', *child])

    def prompt(task, command):
        return ('Your first tool action must run exactly this diagnostic command, '
                'before any inspection or edit:\n' + command + '\n\n'
                + pilot.ISSUES[task] + '\n'
                'Make a focused source repair and verify it. Do not edit tests, '
                'use the internet, subagents or hooks, read outside this workspace, '
                'or commit. After the first diagnostic choose subsequent commands '
                'freely. Stop within 600 seconds and 32 model requests.')

    original_model_plan = live_pilot.model_plan

    def model_plan(profile, message):
        argv = original_model_plan(profile, message)
        if on:
            index = argv.index('plugins')
            if argv[index - 1] != '--disable':
                raise ValueError('plugins were not disabled in baseline')
            argv[index - 1] = '--enable'
        return argv

    def runner(argv, work, env, remaining):
        if on:
            installed = explicit_package_probe.install(work.parent,
                                                        Path(env['CODEX_HOME']))
            if digest(installed / 'scripts/receipt_command.py') != \
                    digest(explicit_package_probe.PACKAGE /
                           'scripts/receipt_command.py'):
                raise ValueError('installed script differs from package')
        return run_managed(argv, work, env, remaining)

    with ExitStack() as stack:
        stack.enter_context(patch.object(live_pilot, 'diagnostic',
                                         side_effect=diagnostic))
        stack.enter_context(patch.object(live_pilot, 'prompt', side_effect=prompt))
        stack.enter_context(patch.object(live_pilot, 'model_plan',
                                         side_effect=model_plan))
        result = live_pilot.one_live(row, RUN_ROOT, protocol, runner=runner)

    root = RUN_ROOT / row['id']
    trace = root / 'host-artifacts/trace.jsonl'
    first_output = None
    if trace.is_file():
        for line in trace.read_text().splitlines():
            event = json.loads(line)
            item = event.get('item') or {}
            if (event.get('type') == 'item.completed'
                    and item.get('type') == 'command_execution'):
                first_output = item.get('aggregated_output', '')
                break
    receipt = None
    if on and first_output:
        try:
            receipt = json.loads(first_output)
        except ValueError:
            pass
    bridge = result.get('bridge') or {}
    broker = result.get('broker') or {}
    row_result = {key: result.get(key) for key in (
        'id', 'arm', 'outcome', 'exit_code', 'timed_out', 'elapsed_seconds',
        'cli_elapsed_seconds', 'first_command', 'quality', 'quality_status',
        'quality_report_sha256', 'usage', 'technical_ok', 'trace_sha256',
        'broker_stopped', 'bridge_stopped')}
    row_result['reconciliation_complete'] = \
        result.get('reconciliation', {}).get('complete')
    row_result['provider_attempts'] = bridge.get('accounting', {}).get('attempts')
    row_result['provider_states'] = bridge.get('accounting', {}).get('states')
    row_result['delivery_attempts'] = broker.get('accounting', {}).get('attempts')
    row_result['first_output_bytes'] = (len(first_output.encode())
                                        if first_output is not None else None)
    row_result['receipt'] = ({key: receipt.get(key) for key in
                             ('schema', 'status', 'exit_code', 'wrapper_exit_code',
                              'capture_complete', 'bytes', 'sha256')}
                             if receipt else None)
    row_result['first_command_verified'] = \
        result.get('first_command', {}).get('first_command_exact') is True
    row_result['package_installed'] = (root / 'home/plugins/cache/'
        'sol-codex-explicit/sol-codex-explicit/0.1.0').is_dir() if on else False
    return row_result


def main():
    os.umask(0o077)
    raw, plan, protocol = preflight()
    with (pilot.HERE / 'execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        RUN_ROOT.mkdir(mode=0o700, exist_ok=False)
        (RUN_ROOT / 'plan.json').write_bytes(raw)
        rows = []
        for arm in plan['arm_order']:
            row = run_arm(arm, protocol)
            rows.append(row)
            (RUN_ROOT / 'aggregate-progress.json').write_text(
                json.dumps(rows, sort_keys=True, indent=2) + '\n')
            if row['technical_ok'] is not True or \
                    row['reconciliation_complete'] is not True:
                break
    result = {'schema': 'solcodex.explicit-model-dev-result.v1',
              'scope': 'exposed_single_packaging_repair_pair',
              'plan_sha256': hashlib.sha256(raw).hexdigest(),
              'provider_billing_complete': False,
              'runs': rows,
              'unstarted_arms': plan['arm_order'][len(rows):]}
    if len(rows) == 2 and all(r['usage'] for r in rows):
        by_arm = {row['arm']: row for row in rows}
        tokens = {arm: by_arm[arm]['usage']['input_tokens']
                  + by_arm[arm]['usage']['output_tokens'] for arm in ('on', 'off')}
        result['total_provider_tokens'] = tokens
        result['token_delta_on_minus_off'] = tokens['on'] - tokens['off']
        result['both_quality_pass'] = all(row['quality_status'] == 'pass'
                                          for row in rows)
    path = BASE / 'model_dev_result.json'
    path.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'result_path': str(path), 'runs': len(rows),
                      'technical_ok': [row['technical_ok'] for row in rows],
                      'both_quality_pass': result.get('both_quality_pass'),
                      'token_delta_on_minus_off':
                          result.get('token_delta_on_minus_off')}, sort_keys=True))


if __name__ == '__main__':
    main()
