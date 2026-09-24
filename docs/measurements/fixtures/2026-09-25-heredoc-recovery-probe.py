"""Reproduce the macOS sandbox heredoc failure and revised harness canary.

Run only with the retained private harness and affected model-run directory.
The provider in the final smoke is synthetic; no model request is made.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
PUBLIC = HERE.parents[2]
RESULT = PUBLIC / 'docs/measurements/data/2026-09-25-heredoc-harness-recovery.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness', required=True, type=Path)
    parser.add_argument('--affected-root', required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.harness.resolve()))
    import cli_toolcall_probe
    import live_pilot
    from process import run_managed

    model_result_path = PUBLIC / 'experiments/explicit_receipt_profile/model_dev_result.json'
    plan_path = PUBLIC / 'experiments/explicit_receipt_profile/model_dev_plan.json'
    model_result = json.loads(model_result_path.read_text())
    plan = json.loads(plan_path.read_text())
    old_hash = plan['private_source_sha256']['live_pilot.py']
    new_hash = digest(Path(live_pilot.__file__))
    if (model_result['schema'] != 'solcodex.explicit-model-dev-result.v1'
            or model_result['runs'][0]['arm'] != 'on'
            or new_hash == old_hash
            or new_hash != digest(HERE / '2026-09-25-live-pilot-tmpprefix.py')):
        raise ValueError('old or revised harness provenance mismatch')

    root = args.affected_root.resolve()
    host = root / 'host-artifacts'
    final = json.loads((host / 'final.json').read_text())
    trace_hash = digest(host / 'trace.jsonl')
    if (final['id'] != model_result['runs'][0]['id']
            or final['trace_sha256'] != trace_hash
            or trace_hash != model_result['runs'][0]['trace_sha256']):
        raise ValueError('affected run differs from published model result')
    profile = (host / 'seatbelt.sb').read_text()
    env = json.loads((host / 'environment.json').read_text())
    if 'TMPPREFIX' in env:
        raise ValueError('affected run already had TMPPREFIX')
    command = (str(root / 'tools/venv/bin/python') +
               " - <<'PYCODE'\nprint('HEREDOC_OK')\nPYCODE")
    observations = {}
    for label, prefix in (('before', None), ('after', str(root / 'tmp/zsh-'))):
        trial_env = dict(env)
        if prefix is not None:
            trial_env['TMPPREFIX'] = prefix
        process, timed_out, cleanup = run_managed(
            ['/usr/bin/sandbox-exec', '-p', profile, '/bin/zsh', '-lc', command],
            root / 'workspace', trial_env, 20)
        observations[label] = {'exit_code': process.returncode,
            'stdout_match': process.stdout == 'HEREDOC_OK\n',
            'expected_error': "can't create temp file for here document: operation not permitted"
                              in process.stderr,
            'stderr_sha256': hashlib.sha256(process.stderr.encode()).hexdigest(),
            'timed_out': timed_out,
            'cleanup_verified': cleanup['verified']}
    if (observations['before']['exit_code'] != 1
            or not observations['before']['expected_error']
            or observations['before']['stdout_match']
            or observations['after']['exit_code'] != 0
            or not observations['after']['stdout_match']
            or observations['after']['expected_error']
            or any(row['timed_out'] or not row['cleanup_verified']
                   for row in observations.values())):
        raise ValueError('sandbox heredoc reproduction did not match')

    smoke = cli_toolcall_probe.run()
    smoke_host = Path(smoke['private_root']) / '03-packaging-on/host-artifacts'
    canaries = json.loads((smoke_host / 'canaries.json').read_text())
    heredoc = json.loads((smoke_host / 'heredoc-canary.json').read_text())
    if (canaries['heredoc_ok'] is not True or heredoc['ok'] is not True
            or smoke['provider_requests'] != 2 or smoke['provider_errors'] != 0
            or smoke['reconciliation_complete'] is not True
            or smoke['cleanup_verified'] is not True
            or smoke['broker_stopped'] is not True
            or smoke['bridge_stopped'] is not True):
        raise ValueError('revised no-model harness smoke failed')
    result = {'schema': 'solcodex.heredoc-harness-recovery.v1',
              'scope': 'posthoc_exposed_model_run_and_no_model_smoke',
              'affected_model_result_sha256': digest(model_result_path),
              'affected_trace_sha256': trace_hash,
              'old_live_pilot_sha256': old_hash,
              'revised_live_pilot_sha256': new_hash,
              'sandbox_reproduction': observations,
              'synthetic_smoke': {
                  'provider_requests': smoke['provider_requests'],
                  'provider_errors': smoke['provider_errors'],
                  'reconciliation_complete': smoke['reconciliation_complete'],
                  'cleanup_verified': smoke['cleanup_verified'],
                  'heredoc_canary_ok': heredoc['ok'],
                  'trace_sha256': smoke['trace_sha256'],
                  'canaries_sha256': digest(smoke_host / 'canaries.json')},
              'old_model_result_reclassified': False}
    RESULT.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'before_exit': observations['before']['exit_code'],
                      'after_exit': observations['after']['exit_code'],
                      'smoke_canary_ok': heredoc['ok']}, sort_keys=True))


if __name__ == '__main__':
    main()
