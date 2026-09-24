"""Gate the frozen no-model real-CLI late-missing-usage control."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import cli_late_missing_usage_probe
import broker_route
import evaluate
import pilot
import runtime_manifest
from protocol_gate import strict_json
from snapshot import manifest


PLAN = pilot.PUBLIC / 'docs/research/data/2026-09-25-real-cli-late-missing-usage-expectations.json'
RESULT = pilot.PUBLIC / 'docs/measurements/data/2026-09-25-real-cli-late-missing-usage-result.json'
PUBLIC_PROBE = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-real-cli-late-missing-usage-probe.py'
PUBLIC_GATE = pilot.PUBLIC / 'docs/measurements/fixtures/2026-09-25-real-cli-late-missing-usage-gate.py'
PUBLIC_REDUCER = pilot.PUBLIC / 'scripts/reduce_real_cli_late_missing_usage_probe.py'
CLI = Path('/Applications/ChatGPT.app/Contents/Resources/codex')
PINNED_FILES = frozenset({
    'audit.py', 'broker_route.py', 'cli_late_checkpoint_probe.py',
    'cli_late_missing_usage_probe.py', 'cli_toolcall_probe.py', 'evaluate.py',
    'frozen_cli_late_missing_usage.py', 'host_bridge.py', 'isolation.py',
    'live_pilot.py', 'pilot.py', 'process.py', 'protocol_gate.py',
    'runtime_manifest.py', 'safe_tree.py', 'selftest-protocol.json',
    'snapshot.py', 'test_harness.py', 'test_host_bridge.py',
})


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def preflight():
    raw = PLAN.read_bytes()
    expected = strict_json(raw)
    if (expected.get('schema') != 'solcodex.real-cli-late-missing-usage-expectations.v1'
            or expected.get('model_run_authorized') is not False
            or expected.get('case') != 'packaging_patch_cancel_late_missing_usage'
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
    for path in (PUBLIC_PROBE, PUBLIC_GATE, PUBLIC_REDUCER):
        relative_fixture = path.relative_to(pilot.PUBLIC).as_posix()
        if path.read_bytes() != subprocess.check_output(
                ['git', 'show', 'HEAD:' + relative_fixture],
                cwd=pilot.PUBLIC, timeout=10):
            raise ValueError('public fixture differs from committed HEAD')
    observed = {name: digest(pilot.HERE / name) for name in PINNED_FILES}
    if observed != expected['private_source_sha256']:
        raise ValueError('private source differs from frozen plan')
    if (digest(PUBLIC_PROBE) != observed['cli_late_missing_usage_probe.py']
            or digest(PUBLIC_GATE) != observed['frozen_cli_late_missing_usage.py']
            or digest(PUBLIC_REDUCER) != expected['reducer_sha256']
            or digest(CLI) != expected['cli_binary_sha256']
            or subprocess.check_output([str(CLI), '--version'], text=True,
                                       timeout=10).strip() != expected['cli_version']
            or sys.version.split()[0] != expected['python_version']):
        raise ValueError('published source or CLI differs from frozen plan')
    protocol = strict_json((pilot.HERE / 'selftest-protocol.json').read_bytes())
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
        result = cli_late_missing_usage_probe.run()
        preflight()  # Reject source or public-plan drift during the run.
        result.pop('private_root')
        mismatch = sorted(k for k, value in expected['expected_observations'].items()
                          if result.get(k) != value)
        if set(result) != set(expected['expected_observations']):
            mismatch.append('observation_key_set')
        result.update(expectations_sha256=hashlib.sha256(raw).hexdigest(),
                      private_source_sha256=observed, public_head=head,
                      predeclared_match=not mismatch, mismatched_fields=mismatch)
        RESULT.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
        print(json.dumps({'result_path': str(RESULT), 'predeclared_match': not mismatch,
                          'mismatched_fields': mismatch}, sort_keys=True))
        return 0 if not mismatch else 1


if __name__ == '__main__':
    sys.exit(main())
