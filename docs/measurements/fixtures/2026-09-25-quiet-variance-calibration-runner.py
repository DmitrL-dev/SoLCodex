"""Prospective 16-run exposed-task calibration; one frozen block per invocation.

Preflight is read-only and makes no model calls. Execution requires an explicitly
authorized, committed and pushed protocol. An interrupted slot is never retried.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

from broker_route import LEDGER_SHA256, LEDGER_SOURCE
from evaluate import load_assets
from live_pilot import one_live
from pilot import HERE, PUBLIC, VENV, WORK
from protocol_gate import (CLI, CODE_FILES, PATCHES, is_sha, strict_json,
                           validate_public_freeze, validate_quality_pins)
from runtime_manifest import digest as runtime_digest
from snapshot import manifest


SCHEDULE = PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-calibration-schedule.json'
PUBLIC_RUNNER = PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-calibration-runner.py'
SOURCE_FILES = CODE_FILES | {'variance_calibration.py'}
QUALIFICATION_FILES = {
    'unit': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-current-unit-probe.json',
    'late_missing': PUBLIC / 'docs/measurements/data/2026-09-25-real-cli-late-missing-usage-v2-result.json',
    'positive_path': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-positive-path-result.json',
    'integrated_path': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-integrated-path-result.json',
    'independent_review': PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-qualification-review.json',
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def schedule(raw):
    value = strict_json(raw)
    if (value.get('schema') != 'solcodex.quiet-variance-calibration-schedule.v1'
            or value.get('model_run_authorized') is not False
            or value.get('status') != 'schedule_frozen_measurement_pending'
            or value.get('timeout_seconds_per_run') != 600
            or value.get('request_cap_per_run') != 32):
        raise ValueError('invalid frozen schedule')
    rows = value.get('schedule')
    if not isinstance(rows, list) or len(rows) != 16:
        raise ValueError('expected exactly 16 schedule slots')
    rng = random.Random(int(value['seed'], 16))
    click = ['verbose', 'verbose', 'quiet', 'quiet']
    first = ['click', 'click', 'packaging', 'packaging']
    rng.shuffle(click)
    rng.shuffle(first)
    predicted = []
    for block in range(1, 5):
        click_first = click[block - 1]
        package_first = 'quiet' if click_first == 'verbose' else 'verbose'
        order = {
            'click': [click_first, 'quiet' if click_first == 'verbose' else 'verbose'],
            'packaging': [package_first, 'quiet' if package_first == 'verbose' else 'verbose'],
        }
        tasks = [first[block - 1], 'packaging' if first[block - 1] == 'click' else 'click']
        for task in tasks:
            for arm in order[task]:
                predicted.append({'id': f'{len(predicted)+1:02d}-b{block}-{task}-{arm}',
                                  'block': block, 'task': task, 'arm': arm})
    if rows != predicted:
        raise ValueError('schedule differs from seed and balanced assignment')
    return rows


def preflight(path):
    raw = path.read_bytes()
    value = strict_json(raw)
    required = {'schema', 'status', 'model_run_authorized', 'schedule_sha256',
                'timeout_seconds', 'request_cap', 'source_tree_sha256',
                'venv_tree_sha256', 'usage_ledger_sha256', 'code_sha256',
                'cli', 'quality', 'visible_patch_sha256', 'source_commits',
                'method', 'qualification'}
    if (set(value) != required
            or value['schema'] != 'solcodex.quiet-variance-calibration.v1'
            or value['status'] not in ('candidate', 'frozen')
            or type(value['model_run_authorized']) is not bool
            or value['model_run_authorized'] != (value['status'] == 'frozen')
            or value['method'] != 'exposed-development-balanced-four-blocks'
            or value['timeout_seconds'] != 600 or value['request_cap'] != 32):
        raise ValueError('invalid calibration protocol')
    public_head = validate_public_freeze(path, raw)
    schedule_raw = SCHEDULE.read_bytes()
    relative = SCHEDULE.relative_to(PUBLIC).as_posix()
    committed = subprocess.check_output(['git', 'show', 'HEAD:' + relative],
                                        cwd=PUBLIC, timeout=10)
    if schedule_raw != committed or sha(schedule_raw) != value['schedule_sha256']:
        raise ValueError('frozen schedule differs')
    rows = schedule(schedule_raw)
    qualification = value['qualification']
    if (not isinstance(qualification, dict)
            or set(qualification) != {'status', 'evidence_sha256'}
            or qualification['status'] not in ('pending', 'qualified')
            or not isinstance(qualification['evidence_sha256'], dict)
            or set(qualification['evidence_sha256']) != set(QUALIFICATION_FILES)
            or value['model_run_authorized'] != (qualification['status'] == 'qualified')):
        raise ValueError('invalid qualification gate')
    for key, evidence_path in QUALIFICATION_FILES.items():
        expected = qualification['evidence_sha256'][key]
        if expected is None:
            if value['model_run_authorized']:
                raise ValueError('missing qualification evidence: ' + key)
            continue
        if not is_sha(expected):
            raise ValueError('invalid qualification evidence hash')
        relative_evidence = evidence_path.relative_to(PUBLIC).as_posix()
        if (sha(evidence_path.read_bytes()) != expected
                or evidence_path.read_bytes() != subprocess.check_output(
                    ['git', 'show', 'HEAD:' + relative_evidence],
                    cwd=PUBLIC, timeout=10)):
            raise ValueError('qualification evidence differs: ' + key)
    code = value['code_sha256']
    if (not isinstance(code, dict) or set(code) != SOURCE_FILES
            or not all(is_sha(item) for item in code.values())):
        raise ValueError('incomplete code pin set')
    for name in SOURCE_FILES:
        if sha((HERE / name).read_bytes()) != code[name]:
            raise ValueError('runner code differs: ' + name)
    runner_relative = PUBLIC_RUNNER.relative_to(PUBLIC).as_posix()
    if (sha(PUBLIC_RUNNER.read_bytes()) != code['variance_calibration.py']
            or PUBLIC_RUNNER.read_bytes() != subprocess.check_output(
                ['git', 'show', 'HEAD:' + runner_relative], cwd=PUBLIC, timeout=10)):
        raise ValueError('public calibration runner differs')
    cli = value['cli']
    if (set(cli) != {'version', 'sha256', 'model', 'effort', 'code_mode'}
            or cli['model'] != 'gpt-6-luna' or cli['effort'] != 'low'
            or cli['code_mode'] is not True or not is_sha(cli['sha256'])
            or sha(CLI.read_bytes()) != cli['sha256']
            or subprocess.check_output([str(CLI), '--version'], text=True,
                                       timeout=10).strip() != cli['version']):
        raise ValueError('model CLI differs')
    if (value['usage_ledger_sha256'] != LEDGER_SHA256
            or sha(LEDGER_SOURCE.read_bytes()) != LEDGER_SHA256
            or runtime_digest(VENV) != value['venv_tree_sha256']):
        raise ValueError('ledger or Python runtime differs')
    for directory, dirs, files in os.walk(VENV, followlinks=False):
        if any((Path(directory) / name).is_symlink() for name in dirs + files):
            raise ValueError('runtime contains a symlink with an unpinned target')
    validate_quality_pins(value['quality'])
    load_assets()
    source = value['source_tree_sha256']
    patches = value['visible_patch_sha256']
    commits = value['source_commits']
    if (set(source) != set(PATCHES) or set(patches) != set(PATCHES)
            or set(commits) != set(PATCHES)
            or not all(is_sha(source[task]) and is_sha(patches[task])
                       and isinstance(commits[task], str) and len(commits[task]) == 40
                       and all(char in '0123456789abcdef' for char in commits[task])
                       for task in PATCHES)):
        raise ValueError('incomplete source pins')
    for task, patch_path in PATCHES.items():
        fixture = WORK / f'prehook-{task}-dev/fixture'
        if (manifest(fixture)['tree_sha256'] != source[task]
                or sha(patch_path.read_bytes()) != patches[task]):
            raise ValueError('source fixture differs: ' + task)
    return value, rows, sha(raw), public_head


def append_event(path, event):
    payload = (json.dumps(event, sort_keys=True, separators=(',', ':'),
                          allow_nan=False) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        with os.fdopen(fd, 'ab', closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def evidence_hashes(root, row_id, saved):
    host = root / row_id / 'host-artifacts'
    files = {path.name: sha(path.read_bytes()) for path in host.iterdir() if path.is_file()}
    required = {'final.json', 'result.json', 'trace.jsonl', 'stderr.txt',
                'delivery.sqlite3', 'upstream.sqlite3', 'baseline.json'}
    if not required <= set(files):
        raise ValueError('missing mandatory run artifact')
    checkpoint = saved.get('checkpoint') or {}
    if (checkpoint.get('status') != 'captured'
            or manifest(host / 'snapshot') != checkpoint.get('manifest')):
        raise ValueError('checkpoint manifest differs')
    report_root = Path(saved.get('quality_report_root', '')).resolve(strict=True)
    if not report_root.is_relative_to(HERE.resolve()):
        raise ValueError('quality report escaped private harness')
    report_path = report_root / 'report.json'
    report_sha = sha(report_path.read_bytes())
    if report_sha != saved.get('quality_report_sha256'):
        raise ValueError('quality report differs')
    report = strict_json(report_path.read_bytes())
    if (report.get('task') != saved.get('task')
            or report.get('status') != saved.get('quality_status')
            or report.get('quality') is not saved.get('quality')
            or report.get('candidate_tree_sha256') != checkpoint['manifest']['tree_sha256']):
        raise ValueError('quality report and checkpoint disagree')
    report_host = report_root / 'host'
    if {str(path.relative_to(report_root)): sha(path.read_bytes())
            for path in report_host.iterdir() if path.is_file()} != report.get('artifact_hashes'):
        raise ValueError('quality report artifacts differ')
    return {'host_files': files, 'quality_report_sha256': report_sha,
            'checkpoint_tree_sha256': checkpoint['manifest']['tree_sha256']}


def journal_state(root, rows):
    raw = (root / 'journal.jsonl').read_bytes()
    if raw and not raw.endswith(b'\n'):
        raise ValueError('incomplete journal line; no automatic retry')
    events = [strict_json(line) for line in raw.splitlines()]
    finished = 0
    pending = None
    for seq, event in enumerate(events):
        if event.get('seq') != seq or event.get('kind') not in ('start', 'finish'):
            raise ValueError('invalid journal sequence')
        if event['kind'] == 'start':
            if pending is not None or finished >= len(rows) or event.get('id') != rows[finished]['id']:
                raise ValueError('out-of-order start')
            pending = event['id']
        else:
            if pending is None or event.get('id') != pending:
                raise ValueError('unpaired finish')
            evidence = root / pending / 'host-artifacts/final.json'
            if not evidence.is_file() or sha(evidence.read_bytes()) != event.get('final_sha256'):
                raise ValueError('run evidence differs from journal')
            saved = strict_json(evidence.read_bytes())
            if (saved.get('id') != pending
                    or saved.get('task') != rows[finished]['task']
                    or saved.get('arm') != rows[finished]['arm']):
                raise ValueError('run identity differs from schedule')
            if event.get('evidence_sha256') != evidence_hashes(root, pending, saved):
                raise ValueError('run evidence differs from sealed inventory')
            if event.get('admissible') != (admissible(saved) and
                                           event.get('pins_valid') is True):
                raise ValueError('journal admission differs from run evidence')
            if event.get('admissible') is not True:
                raise ValueError('technical/adherence failure; campaign stopped')
            pending = None
            finished += 1
    if pending is not None:
        raise ValueError('started run lacks finish; no automatic retry')
    if finished % 4:
        raise ValueError('block stopped before completion')
    return finished, len(events)


def admissible(result):
    first = result.get('first_command') or {}
    usage = result.get('usage') or {}
    counts = (usage.get('input_tokens'), usage.get('output_tokens'),
              usage.get('cached_input_tokens'))
    return (result.get('technical_ok') is True
            and result.get('reconciliation', {}).get('complete') is True
            and first.get('status') == 'observed'
            and first.get('first_command_exact') is True
            and first.get('first_exit_code') == 1
            and result.get('quality_status') in ('pass', 'fail')
            and result.get('quality') is (result['quality_status'] == 'pass')
            and all(type(count) is int and count >= 0 for count in counts)
            and counts[2] <= counts[0])


def campaign_root(protocol_sha):
    return HERE / ('variance-campaign-' + protocol_sha[:16])


def execute(value, rows, protocol_raw, protocol_sha, public_head, block, protocol_path):
    if value['model_run_authorized'] is not True:
        raise ValueError('candidate protocol forbids model execution')
    root = campaign_root(protocol_sha)
    if not root.exists():
        if block != 1:
            raise ValueError('campaign must begin with block one')
        root.mkdir(mode=0o700)
        parent_fd = os.open(HERE, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        with (root / 'protocol.json').open('xb') as stream:
            stream.write(protocol_raw)
            stream.flush()
            os.fsync(stream.fileno())
        (root / 'journal.jsonl').touch(mode=0o600, exist_ok=False)
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    if (root / 'protocol.json').read_bytes() != protocol_raw:
        raise ValueError('campaign protocol differs')
    finished, seq = journal_state(root, rows)
    if finished != 4 * (block - 1):
        raise ValueError('block is not the next unstarted block')
    for row in rows[finished:finished + 4]:
        _, _, current_sha, current_head = preflight(protocol_path)
        if current_sha != protocol_sha:
            raise ValueError('protocol changed before slot')
        append_event(root / 'journal.jsonl',
                     {'seq': seq, 'kind': 'start', 'id': row['id'],
                      'public_head': current_head, 'utc': time.time()})
        seq += 1
        item = one_live({key: row[key] for key in ('id', 'task', 'arm')}, root, value)
        final = root / row['id'] / 'host-artifacts/final.json'
        if not final.is_file():
            raise RuntimeError('missing run evidence; campaign stopped')
        sealed = evidence_hashes(root, row['id'], item)
        try:
            _, _, after_sha, _ = preflight(protocol_path)
            pins_valid = after_sha == protocol_sha
        except Exception:
            pins_valid = False
        okay = admissible(item) and pins_valid
        append_event(root / 'journal.jsonl',
                     {'seq': seq, 'kind': 'finish', 'id': row['id'],
                      'final_sha256': sha(final.read_bytes()),
                      'evidence_sha256': sealed,
                      'admissible': okay, 'pins_valid': pins_valid,
                      'quality_status': item.get('quality_status'),
                      'utc': time.time()})
        seq += 1
        if not okay:
            raise RuntimeError('technical, quality-evidence or adherence failure; campaign stopped')
    return {'campaign': str(root), 'completed_block': block, 'completed_runs': finished + 4,
            'unstarted_ids': [row['id'] for row in rows[finished + 4:]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--block', type=int, choices=range(1, 5))
    args = parser.parse_args()
    if args.execute != (args.block is not None):
        parser.error('--execute and --block must be supplied together')
    os.umask(0o077)
    with (HERE / 'execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = args.protocol.resolve(strict=True)
        value, rows, protocol_sha, public_head = preflight(path)
        if not args.execute:
            print(json.dumps({'preflight': 'pass', 'model_run_authorized':
                              value['model_run_authorized'], 'slots': len(rows),
                              'protocol_sha256': protocol_sha, 'public_head': public_head},
                             sort_keys=True))
            return 0
        result = execute(value, rows, path.read_bytes(), protocol_sha,
                         public_head, args.block, path)
        print(json.dumps(result, sort_keys=True))
        return 0


if __name__ == '__main__':
    sys.exit(main())
