"""Frozen local negative controls for the installed explicit receipt package.

No model, network, or Codex CLI is used. Only bounded aggregate observations
are published; full child output and random evidence markers stay temporary.
"""

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile


BASE = Path(__file__).resolve().parents[1]
PACKAGE = BASE / 'plugins/sol-codex-explicit'
PLAN = BASE / 'negative_expectations.json'
RESULT = BASE / 'negative_result.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def run(command, timeout=10):
    return subprocess.run(command, capture_output=True, timeout=timeout,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))


def receipt_run(adapter, artifact_dir, code, count, seconds=5):
    process = run([sys.executable, str(adapter), '--artifact-dir', str(artifact_dir),
                   '--timeout-seconds', str(seconds), '--', sys.executable,
                   '-c', code, str(count)])
    check(not process.stderr, 'adapter wrote stderr')
    check(len(process.stdout) <= 3072, 'receipt exceeds bound')
    receipt = json.loads(process.stdout)
    check(receipt['schema'] == 'solcodex.command-receipt.v1', 'receipt schema')
    check(process.returncode == receipt['wrapper_exit_code'], 'wrapper exit mismatch')
    return process, receipt


def artifact_bytes(receipt, artifact_dir):
    path = Path(receipt['path'])
    check(path.is_relative_to(artifact_dir) and path.is_file(), 'artifact path')
    data = path.read_bytes()
    check(len(data) == receipt['bytes'], 'artifact byte count')
    check(digest(data) == receipt['sha256'], 'artifact hash')
    return path, data


def main():
    raw_plan = PLAN.read_bytes()
    plan = json.loads(raw_plan)
    check(plan['schema'] == 'solcodex.installed-negative-plan.v1', 'plan schema')
    check(plan['model_run_authorized'] is False, 'model run not authorized')
    check(plan['script_sha256'] == digest(Path(__file__).read_bytes()),
          'control script changed')
    for name, expected in plan['package_sha256'].items():
        check(digest((PACKAGE / name).read_bytes()) == expected,
              'package changed: ' + name)

    with tempfile.TemporaryDirectory(prefix='solcodex-negative-') as temporary:
        root = Path(temporary)
        installed = root / 'installed/sol-codex-explicit/0.1.0'
        installed.parent.mkdir(parents=True)
        shutil.copytree(PACKAGE, installed)
        for name, expected in plan['package_sha256'].items():
            check(digest((installed / name).read_bytes()) == expected,
                  'installed copy changed: ' + name)
        adapter = installed / 'scripts/receipt_command.py'
        search = installed / 'scripts/receipt_search.py'
        observations = {}

        failure_root = root / 'failure-artifacts'
        failure_count = root / 'failure-count'
        failure_code = ('import pathlib,sys; '
                        'pathlib.Path(sys.argv[1]).write_text("run\\n"); '
                        'print("FAIL_MARKER",flush=True); sys.exit(7)')
        failed, failure = receipt_run(adapter, failure_root, failure_code,
                                      failure_count)
        _, failure_data = artifact_bytes(failure, failure_root)
        check(failed.returncode == 7 and failure['exit_code'] == 7
              and failure['status'] == 'completed'
              and failure['capture_complete'] is True
              and failure_count.read_text() == 'run\n'
              and b'FAIL_MARKER' in failure_data, 'nonzero child not preserved')
        observations['nonzero_child'] = {
            'child_executions': 1, 'wrapper_exit_code': failed.returncode,
            'child_exit_code': failure['exit_code'],
            'capture_complete': failure['capture_complete'],
            'artifact_hash_verified': True}

        timeout_root = root / 'timeout-artifacts'
        timeout_count = root / 'timeout-count'
        timeout_code = ('import pathlib,sys,time; '
                        'pathlib.Path(sys.argv[1]).write_text("run\\n"); '
                        'print("TIMEOUT_MARKER",flush=True); time.sleep(10)')
        timed, timeout_receipt = receipt_run(adapter, timeout_root, timeout_code,
                                             timeout_count, seconds=0.3)
        _, timeout_data = artifact_bytes(timeout_receipt, timeout_root)
        check(timed.returncode == 124 and timeout_receipt['status'] == 'timeout'
              and timeout_receipt['capture_complete'] is False
              and timeout_count.read_text() == 'run\n'
              and b'TIMEOUT_MARKER' in timeout_data, 'timeout not preserved')
        observations['timeout'] = {
            'child_executions': 1, 'wrapper_exit_code': timed.returncode,
            'status': timeout_receipt['status'],
            'capture_complete': timeout_receipt['capture_complete'],
            'artifact_hash_verified': True}

        bad_root = root / 'shared-artifacts'
        bad_root.mkdir()
        bad_root.chmod(0o755)
        bad_count = root / 'bad-count'
        rejected, rejected_receipt = receipt_run(
            adapter, bad_root,
            'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("run\\n")',
            bad_count)
        check(rejected.returncode == 125
              and rejected_receipt['status'] == 'capture_error'
              and rejected_receipt['capture_complete'] is False
              and rejected_receipt['path'] is None
              and rejected_receipt['error']['phase'] == 'artifact_setup'
              and not bad_count.exists(), 'unsafe storage launched child')
        observations['unsafe_storage'] = {
            'child_executions': 0, 'wrapper_exit_code': rejected.returncode,
            'status': rejected_receipt['status'],
            'capture_complete': rejected_receipt['capture_complete']}

        retrieval_root = root / 'retrieval-artifacts'
        retrieval_count = root / 'retrieval-count'
        marker = 'NEEDED_' + secrets.token_hex(12)
        retrieval_code = ('import pathlib,sys; '
                          'pathlib.Path(sys.argv[1]).write_text("run\\n"); '
                          '[(print("line-%03d"%i + '
                          f'({marker!r} if i==64 else ""),flush=True)) '
                          'for i in range(128)]')
        completed, retrieval_receipt = receipt_run(
            adapter, retrieval_root, retrieval_code, retrieval_count)
        path, retrieval_data = artifact_bytes(retrieval_receipt, retrieval_root)
        check(completed.returncode == 0 and retrieval_receipt['status'] == 'completed'
              and retrieval_receipt['capture_complete'] is True
              and retrieval_count.read_text() == 'run\n'
              and marker.encode() in retrieval_data
              and marker not in json.dumps(retrieval_receipt),
              'retrieval fixture leaked into preview or failed')
        found = run([sys.executable, str(search), '--artifact', str(path),
                     '--sha256', retrieval_receipt['sha256'], '--literal', marker])
        found_result = json.loads(found.stdout)
        check(found.returncode == 0 and len(found.stdout) <= 2048
              and found_result['status'] == 'ok'
              and found_result['match_count'] == 1
              and found_result['matches'][0]['line'] == 65
              and marker in found_result['matches'][0]['text'],
              'bounded retrieval failed')
        wrong_hash = run([sys.executable, str(search), '--artifact', str(path),
                          '--sha256', '0' * 64, '--literal', marker])
        wrong_result = json.loads(wrong_hash.stdout)
        check(wrong_hash.returncode == 2 and
              wrong_result == {'schema': 'solcodex.receipt-search.v1',
                               'status': 'error', 'error': 'hash_mismatch'},
              'wrong hash accepted')
        observations['retrieval'] = {
            'child_executions': 1, 'receipt_omitted_needed_line': True,
            'artifact_hash_verified': True, 'found_line': 65,
            'search_result_bytes': len(found.stdout),
            'wrong_hash_rejected': True}

    result = {
        'schema': 'solcodex.installed-negative-result.v1',
        'scope': 'local_no_model_installed_copy',
        'plan_sha256': digest(raw_plan),
        'observations': observations,
    }
    RESULT.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'result_path': str(RESULT),
                      'plan_sha256': result['plan_sha256']}, sort_keys=True))


if __name__ == '__main__':
    main()
