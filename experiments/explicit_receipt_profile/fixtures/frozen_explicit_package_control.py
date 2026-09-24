"""Run the frozen synthetic-provider ON/OFF control for explicit receipts."""

import hashlib
import json
from pathlib import Path
import sys

import evaluate
import explicit_package_probe
import pilot
import runtime_manifest
from snapshot import manifest


BASE = pilot.PUBLIC / 'experiments/explicit_receipt_profile'
PLAN = BASE / 'expectations.json'
RESULT = BASE / 'result.json'
CLI = Path('/Applications/ChatGPT.app/Contents/Resources/codex')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def preflight():
    raw = PLAN.read_bytes()
    plan = json.loads(raw)
    if (plan['schema'] != 'solcodex.explicit-package-control-plan.v1'
            or plan['model_run_authorized'] is not False
            or plan['arm_order'] != ['off', 'on']):
        raise ValueError('invalid frozen control plan')
    if ({name: digest(pilot.HERE / name) for name in plan['private_source_sha256']}
            != plan['private_source_sha256']):
        raise ValueError('private controller source changed')
    for name in ('explicit_package_probe.py', 'frozen_explicit_package_control.py'):
        if digest(BASE / 'fixtures' / name) != plan['private_source_sha256'][name]:
            raise ValueError('published controller differs from private controller')
    if ({name: digest(BASE / name) for name in plan['package_sha256']}
            != plan['package_sha256']):
        raise ValueError('experimental package changed')
    protocol = json.loads((pilot.HERE / 'selftest-protocol.json').read_text())
    if (digest(CLI) != plan['cli_binary_sha256']
            or sys.version.split()[0] != plan['python_version']
            or protocol['schedule'][2] != plan['schedule_row']
            or protocol['source_tree_sha256']['packaging'] != plan['source_tree_sha256']
            or protocol['venv_tree_sha256'] != plan['runtime_tree_sha256']
            or manifest(pilot.WORK / 'prehook-packaging-dev/fixture')['tree_sha256']
            != plan['source_tree_sha256']
            or runtime_manifest.digest(pilot.VENV) != plan['runtime_tree_sha256']
            or evaluate.runtime_digest(pilot.VENV) != plan['evaluator_runtime_tree_sha256']):
        raise ValueError('CLI, fixture, protocol or runtime changed')
    return raw, plan


def validate(arm, result):
    expected = {
        'scope': 'no_model_installed_explicit_package',
        'arm': arm,
        'provider_requests': 2,
        'provider_errors': 0,
        'cli_exit_code': 0,
        'tool_calls': 1,
        'tool_exit_codes': [0],
        'child_executions': 1,
        'reconciliation_complete': True,
        'cleanup_verified': True,
        'broker_stopped': True,
        'bridge_stopped': True,
    }
    if arm == 'off':
        expected.update(request_marker_flags=[False, True],
            request_receipt_flags=[False, False], first_request_skill_flag=False,
            tool_output_marker=True, tool_command_uses_installed_script=False,
            installed_cache_present=False)
    else:
        expected.update(request_marker_flags=[False, False],
            request_receipt_flags=[False, True], first_request_skill_flag=True,
            tool_output_marker=False, tool_command_uses_installed_script=True,
            installed_cache_present=True, installed_script_matches_package=True,
            receipt_status='completed', receipt_complete=True, receipt_exit_code=0)
    bad = {key: [result.get(key), value] for key, value in expected.items()
           if result.get(key) != value}
    if bad:
        raise ValueError(f'{arm} failed frozen observations: {bad}')
    sizes = result['request_body_bytes']
    if len(sizes) != 2 or min(sizes) <= 0 or result['tool_output_bytes'] <= 0:
        raise ValueError(f'{arm} missing request or output sizes')
    if arm == 'on':
        artifact = result['artifact']
        if (artifact is None or artifact['sha256_matches'] is not True
                or artifact['marker_present'] is not True
                or artifact['bytes'] != result['receipt_bytes']
                or artifact['bytes'] <= 5000
                or result['tool_output_bytes'] > 3072):
            raise ValueError('ON receipt or full artifact invalid')
    return result


def main():
    raw, _ = preflight()
    rows = []
    for on in (False, True):
        row = validate('on' if on else 'off', explicit_package_probe.run_arm(on))
        row.pop('private_root')
        rows.append(row)
    off, on = rows
    result = {
        'schema': 'solcodex.explicit-package-control-result.v1',
        'expectations_sha256': hashlib.sha256(raw).hexdigest(),
        'model_usage_is_synthetic': True,
        'arms': rows,
        'request_body_bytes_total': {'off': sum(off['request_body_bytes']),
                                     'on': sum(on['request_body_bytes'])},
        'request_body_bytes_delta_on_minus_off':
            sum(on['request_body_bytes']) - sum(off['request_body_bytes']),
        'request_2_bytes_delta_on_minus_off':
            on['request_body_bytes'][1] - off['request_body_bytes'][1],
    }
    RESULT.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'result_path': str(RESULT),
                      'expectations_sha256': result['expectations_sha256']},
                     sort_keys=True))


if __name__ == '__main__':
    main()
