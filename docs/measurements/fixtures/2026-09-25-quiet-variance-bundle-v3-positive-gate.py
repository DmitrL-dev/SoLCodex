"""Frozen no-model positive path for the pinned CLI bundle calibration."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import cli_late_checkpoint_bundle_probe_v3 as cli_late_checkpoint_bundle_probe
import bundled_cli_v3 as bundled_cli
import broker_route
import evaluate
import pilot
import runtime_manifest
from protocol_gate import CODE_FILES, is_sha, strict_json
from snapshot import manifest


PLAN = pilot.PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-bundle-v3-positive-expectations.json'
RESULT = pilot.PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-positive-result.json'
PUBLIC_TRACE = pilot.PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-positive-trace.jsonl'
PUBLIC_PROBE = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-real-cli-late-checkpoint-bundle-v3-probe.py'
PUBLIC_GATE = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-positive-gate.py'
PUBLIC_BUNDLE = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundled-cli-v3.py'
PUBLIC_REDUCER = pilot.PUBLIC / 'scripts/reduce_quiet_variance_bundle_v3_positive.py'
PUBLIC_AUDIT = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-real-cli-first-diagnostic-audit.py'
PUBLIC_CALIBRATION_RUNNER = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-calibration-bundle-v3-runner.py'
PUBLIC_CANDIDATE = pilot.PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-calibration-bundle-v3-candidate.json'
PINNED_FILES = frozenset({
    'audit.py', 'broker_route.py', 'bundled_cli_v3.py',
    'cli_late_checkpoint_bundle_probe_v3.py',
    'cli_toolcall_probe.py', 'evaluate.py',
    'frozen_quiet_variance_bundle_positive_path_v3.py', 'host_bridge.py', 'isolation.py',
    'live_pilot.py', 'pilot.py', 'process.py', 'protocol_gate.py',
    'runtime_manifest.py', 'safe_tree.py', 'selftest-protocol.json',
    'snapshot.py', 'test_harness.py', 'test_host_bridge.py', 'variance_calibration_bundle_v3.py',
})


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode()


def candidate_core_digest(path):
    candidate = strict_json(path.read_bytes())
    core = {key: value for key, value in candidate.items()
            if key not in ('status', 'model_run_authorized', 'qualification')}
    return hashlib.sha256(canonical(core)).hexdigest()


def preflight():
    bundled_cli.validate_source_snapshot(
        'frozen_quiet_variance_bundle_positive_path_v3.py', PINNED_FILES)
    raw = PLAN.read_bytes()
    expected = strict_json(raw)
    if (expected.get('schema') != 'solcodex.quiet-variance-positive-path-expectations.v3'
            or expected.get('status') != 'prospective_no_model_development_control'
            or expected.get('model_run_authorized') is not False
            or expected.get('case') != 'packaging_patch_cancel_late_usage'
            or set(expected.get('private_source_sha256', {})) != PINNED_FILES):
        raise ValueError('invalid frozen plan')
    relative = PLAN.relative_to(pilot.PUBLIC).as_posix()
    committed = subprocess.check_output(['git', 'show', 'HEAD:' + relative],
                                        cwd=pilot.PUBLIC, timeout=10)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=pilot.PUBLIC,
                                   text=True, timeout=10).strip()
    remote = subprocess.check_output(['git', 'ls-remote', 'origin', 'refs/heads/main'],
                                     cwd=pilot.PUBLIC, text=True, timeout=20).split()[0]
    if raw != committed or head != remote:
        raise ValueError('plan is not frozen on pushed main')
    for path in (PUBLIC_PROBE, PUBLIC_GATE, PUBLIC_BUNDLE, PUBLIC_REDUCER, PUBLIC_AUDIT,
                 PUBLIC_CALIBRATION_RUNNER, PUBLIC_CANDIDATE):
        relative_fixture = path.relative_to(pilot.PUBLIC).as_posix()
        if path.read_bytes() != subprocess.check_output(
                ['git', 'show', 'HEAD:' + relative_fixture],
                cwd=pilot.PUBLIC, timeout=10):
            raise ValueError('public fixture differs from committed HEAD')
    observed = {name: digest(pilot.HERE / name) for name in PINNED_FILES}
    if observed != expected['private_source_sha256']:
        raise ValueError('private source differs from frozen plan')
    if (digest(PUBLIC_PROBE) != observed['cli_late_checkpoint_bundle_probe_v3.py']
            or digest(PUBLIC_GATE) != observed['frozen_quiet_variance_bundle_positive_path_v3.py']
            or digest(PUBLIC_BUNDLE) != observed['bundled_cli_v3.py']
            or digest(PUBLIC_AUDIT) != observed['audit.py']
            or digest(PUBLIC_CALIBRATION_RUNNER) != observed['variance_calibration_bundle_v3.py']
            or candidate_core_digest(PUBLIC_CANDIDATE) != expected['calibration_core_sha256']
            or digest(PUBLIC_REDUCER) != expected['reducer_sha256']
            or digest(bundled_cli.BUNDLE / 'codex') != expected['cli_binary_sha256']
            or digest(bundled_cli.BUNDLE / 'codex-code-mode-host')
                != expected['code_mode_host_sha256']
            or subprocess.check_output([str(bundled_cli.BUNDLE / 'codex'), '--version'], text=True,
                                       timeout=10).strip() != expected['cli_version']
            or sys.version.split()[0] != expected['python_version']):
        raise ValueError('published source or CLI differs from frozen plan')
    bundled_cli.validate_bundle()
    protocol = strict_json((pilot.HERE / 'selftest-protocol.json').read_bytes())
    candidate = strict_json(PUBLIC_CANDIDATE.read_bytes())
    candidate_code = candidate.get('code_sha256', {})
    if (candidate.get('status') != 'candidate'
            or candidate.get('model_run_authorized') is not False
            or not isinstance(candidate_code, dict)
            or set(candidate_code) != CODE_FILES | {'bundled_cli_v3.py', 'variance_calibration_bundle_v3.py'}
            or any(candidate_code[name] != observed[name] for name in candidate_code)
            or candidate.get('cli', {}).get('sha256') != expected['cli_binary_sha256']
            or candidate.get('cli', {}).get('code_mode_host_sha256')
                != expected['code_mode_host_sha256']
            or candidate.get('venv_tree_sha256') != expected['runtime_tree_sha256']
            or candidate.get('usage_ledger_sha256') != expected['usage_ledger_sha256']
            or candidate.get('source_tree_sha256', {}).get('packaging')
                != expected['source_tree_sha256']):
        raise ValueError('calibration candidate differs from positive control')
    if (protocol['schedule'][2] != expected['schedule_row']
            or protocol['usage_ledger_sha256'] != expected['usage_ledger_sha256']
            or broker_route.LEDGER_SHA256 != expected['usage_ledger_sha256']
            or digest(broker_route.LEDGER_SOURCE) != expected['usage_ledger_sha256']
            or protocol['source_tree_sha256']['packaging'] != expected['source_tree_sha256']
            or protocol['venv_tree_sha256'] != expected['runtime_tree_sha256']
            or manifest(pilot.WORK / 'prehook-packaging-dev/fixture')['tree_sha256']
                != expected['source_tree_sha256']
            or runtime_manifest.digest(pilot.VENV) != expected['runtime_tree_sha256']
            or evaluate.runtime_digest(pilot.VENV)
                != expected['evaluator_runtime_tree_sha256']
            or digest(pilot.HERE / 'quality-assets/lock.json')
                != expected['quality_assets_lock_sha256']):
        raise ValueError('baseline, runtime or evaluator differs from frozen plan')
    evaluate.load_assets()
    return raw, expected, observed, head


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    with (pilot.HERE / 'execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw, expected, observed, head = preflight()
        if not args.execute:
            print(json.dumps({'preflight': 'pass', 'public_head': head}, sort_keys=True))
            return 0
        result = cli_late_checkpoint_bundle_probe.run()
        after_raw, after_expected, after_observed, after_head = preflight()
        if (after_raw != raw or canonical(after_expected) != canonical(expected)
                or after_observed != observed or after_head != head):
            raise ValueError('measurement pins drifted during the run')
        private_root = Path(result.pop('private_root'))
        host = private_root / expected['schedule_row']['id'] / 'host-artifacts'
        trace = (host / 'trace.jsonl').read_bytes()
        final = strict_json((host / 'final.json').read_bytes())
        report_path = Path(final['quality_report_root']) / 'report.json'
        report = strict_json(report_path.read_bytes())
        if (hashlib.sha256(trace).hexdigest() != result['trace_sha256']
                or hashlib.sha256(report_path.read_bytes()).hexdigest()
                    != result['quality_report_sha256']
                or final.get('quality_report_sha256') != result['quality_report_sha256']
                or final.get('trace_sha256') != result['trace_sha256']
                or final.get('quality_status') != result['quality_status']
                or final.get('checkpoint', {}).get('manifest', {}).get('tree_sha256')
                    != result['checkpoint_tree_sha256']
                or report.get('status') != 'pass' or report.get('quality') is not True
                or report.get('candidate_tree_sha256') != expected['gold_tree_sha256']
                or report.get('assets_lock_sha256')
                    != expected['quality_assets_lock_sha256']):
            raise ValueError('private trace or evaluator report differs')
        result = json.loads(json.dumps(result, sort_keys=True, allow_nan=False))
        mismatch = sorted(k for k, value in expected['expected_observations'].items()
                          if canonical(result.get(k)) != canonical(value))
        if set(result) != set(expected['expected_observations']) | {
                'trace_sha256', 'quality_report_sha256'}:
            mismatch.append('observation_key_set')
        if not is_sha(result.get('trace_sha256')) or not is_sha(result.get('quality_report_sha256')):
            mismatch.append('artifact_hash_shape')
        result.update(schema='solcodex.quiet-variance-positive-path-observations.v2',
                      expectations_sha256=hashlib.sha256(raw).hexdigest(),
                      private_source_sha256=observed, public_head=head,
                      cli_bundle_sha256=bundled_cli.PINNED,
                      predeclared_match=not mismatch, mismatched_fields=mismatch,
                      private_artifacts_verified=True)
        PUBLIC_TRACE.write_bytes(trace)
        RESULT.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
        print(json.dumps({'result_path': str(RESULT), 'predeclared_match': not mismatch,
                          'mismatched_fields': mismatch}, sort_keys=True))
        return 0 if not mismatch else 1


if __name__ == '__main__':
    sys.exit(main())
