"""Frozen no-model OFF/SHAM/ON decomposition of explicit package overhead."""

import hashlib
import json

import frozen_explicit_package_control as prior
import pilot
import sham_package_probe


BASE = pilot.PUBLIC / 'experiments/explicit_receipt_profile'
PLAN = BASE / 'sham_expectations.json'
RESULT = BASE / 'sham_result.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preflight():
    old_raw, _ = prior.preflight()
    raw = PLAN.read_bytes()
    plan = json.loads(raw)
    if (plan['schema'] != 'solcodex.explicit-sham-plan.v1'
            or plan['model_run_authorized'] is not False
            or plan['arm_order'] != ['off', 'sham', 'on']
            or plan['prior_expectations_sha256'] != hashlib.sha256(old_raw).hexdigest()):
        raise ValueError('invalid frozen sham plan')
    for name, expected in plan['private_source_sha256'].items():
        if (digest(pilot.HERE / name) != expected
                or digest(BASE / 'fixtures' / name) != expected):
            raise ValueError('private or public sham source changed: ' + name)
    return raw, plan


def validate(mode, row):
    expected = {
        'scope': 'no_model_explicit_package_triarm',
        'arm': mode,
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
        'first_request_skill_flag': mode != 'off',
        'installed_cache_present': mode != 'off',
        'tool_command_uses_installed_script': mode == 'on',
        'request_marker_flags': [False, mode != 'on'],
        'request_receipt_flags': [False, mode == 'on'],
        'tool_output_marker': mode != 'on',
    }
    if mode != 'off':
        expected['installed_script_matches_package'] = True
    else:
        expected['installed_script_matches_package'] = None
    if mode == 'on':
        expected.update(receipt_status='completed', receipt_complete=True,
                        receipt_exit_code=0)
    if any(row.get(key) != value for key, value in expected.items()):
        raise ValueError(f'{mode} failed frozen observations')
    sizes = row['request_body_bytes']
    if len(sizes) != 2 or min(sizes) <= 0 or row['tool_output_bytes'] <= 0:
        raise ValueError(f'{mode} missing request or output sizes')
    if mode == 'on':
        artifact = row['artifact']
        if (artifact is None or artifact['sha256_matches'] is not True
                or artifact['marker_present'] is not True
                or artifact['bytes'] != row['receipt_bytes']
                or artifact['bytes'] <= 5000
                or row['tool_output_bytes'] > 3072):
            raise ValueError('ON receipt or artifact invalid')
    return row


def main():
    raw, plan = preflight()
    rows = []
    for mode in plan['arm_order']:
        row = validate(mode, sham_package_probe.run_arm(mode))
        row.pop('private_root')
        rows.append(row)
    totals = {row['arm']: sum(row['request_body_bytes']) for row in rows}
    second = {row['arm']: row['request_body_bytes'][1] for row in rows}
    result = {
        'schema': 'solcodex.explicit-sham-result.v1',
        'scope': 'synthetic_provider_real_cli_no_model',
        'expectations_sha256': hashlib.sha256(raw).hexdigest(),
        'model_usage_is_synthetic': True,
        'arms': rows,
        'request_body_bytes_total': totals,
        'request_2_bytes': second,
        'contrasts_total_bytes': {
            'sham_minus_off': totals['sham'] - totals['off'],
            'on_minus_sham': totals['on'] - totals['sham'],
            'on_minus_off': totals['on'] - totals['off']},
        'contrasts_request_2_bytes': {
            'sham_minus_off': second['sham'] - second['off'],
            'on_minus_sham': second['on'] - second['sham'],
            'on_minus_off': second['on'] - second['off']},
    }
    RESULT.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'result_path': str(RESULT),
                      'expectations_sha256': result['expectations_sha256'],
                      'contrasts_total_bytes': result['contrasts_total_bytes']},
                     sort_keys=True))


if __name__ == '__main__':
    main()
