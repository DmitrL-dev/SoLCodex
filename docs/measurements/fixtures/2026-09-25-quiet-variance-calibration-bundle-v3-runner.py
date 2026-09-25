"""Prospective 16-run calibration with a pinned local CLI bundle.

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
from unittest.mock import patch

import bundled_cli_v3 as bundled_cli
import live_pilot
from broker_route import LEDGER_SHA256, LEDGER_SOURCE
from evaluate import load_assets
from live_pilot import one_live
from pilot import HERE, PUBLIC, VENV, WORK
from protocol_gate import (CODE_FILES, PATCHES, is_sha, strict_json,
                           validate_public_freeze, validate_quality_pins)
from runtime_manifest import digest as runtime_digest
from snapshot import manifest


SCHEDULE = PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-calibration-schedule.json'
PUBLIC_RUNNER = PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-calibration-bundle-v3-runner.py'
PUBLIC_BUNDLE = PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundled-cli-v3.py'
SOURCE_FILES = CODE_FILES | {'variance_calibration_bundle_v3.py', 'bundled_cli_v3.py'}
QUALIFICATION_FILES = {
    'unit': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-current-unit-probe.json',
    'late_missing': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-negative-result.json',
    'positive_path': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-positive-result.json',
    'normal_cli_matrix': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-normal-cli-result.json',
    'integrated_path': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-integrated-result.json',
    'independent_review': PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-bundle-v3-qualification-review.json',
}

DECISION_SCRIPTS = {
    'unit': PUBLIC / 'scripts/reduce_quiet_measurement_qualification.py',
    'late_missing': PUBLIC / 'scripts/reduce_quiet_variance_bundle_v3_negative.py',
    'positive_path': PUBLIC / 'scripts/reduce_quiet_variance_bundle_v3_positive.py',
    'normal_cli_matrix': PUBLIC / 'scripts/reduce_quiet_variance_bundle_v3_normal_cli.py',
    'integrated_path': PUBLIC / 'scripts/reduce_quiet_variance_bundle_v3_integrated.py',
}

CONTROL_PLANS = {
    'late_missing': PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-bundle-v3-negative-expectations.json',
    'positive_path': PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-bundle-v3-positive-expectations.json',
    'normal_cli_matrix': PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-bundle-v3-normal-cli-expectations.json',
    'integrated_path': PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-bundle-v3-integrated-expectations.json',
}

CONTROL_SCHEMAS = {
    'late_missing': 'solcodex.quiet-variance-bundle-negative-observations.v1',
    'positive_path': 'solcodex.quiet-variance-positive-path-observations.v2',
    'normal_cli_matrix': 'solcodex.quiet-variance-bundle-normal-cli-observations.v1',
    'integrated_path': 'solcodex.quiet-variance-bundle-integrated-observations.v1',
}

CONTROL_DECISIONS = {
    'late_missing': 'control_pass',
    'positive_path': 'control_pass',
    'normal_cli_matrix': 'control_pass',
    'integrated_path': 'control_pass',
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


def published_decision(script, args, key):
    relative = script.relative_to(PUBLIC).as_posix()
    if script.read_bytes() != subprocess.check_output(
            ['git', 'show', 'HEAD:' + relative], cwd=PUBLIC, timeout=10):
        raise ValueError('qualification reducer differs: ' + relative)
    process = subprocess.run([sys.executable, str(script), *args],
                             cwd=PUBLIC, capture_output=True, text=True, timeout=20)
    if process.returncode != 0:
        raise ValueError('qualification reducer failed: ' + relative)
    decision = strict_json(process.stdout.encode())
    if decision.get(key) is not True:
        raise ValueError('qualification reducer rejected: ' + relative)


def validate_qualification(value):
    """Validate every filled control, including while the candidate is pending."""
    hashes = value['qualification']['evidence_sha256']
    if hashes['unit'] is not None:
        unit = strict_json(QUALIFICATION_FILES['unit'].read_bytes())
        if (unit.get('schema') != 'solcodex.quiet-measurement-unit-probe.v1'
                or any(unit.get('source_sha256', {}).get(name) != value['code_sha256'][name]
                       for name in ('audit.py', 'evaluate.py', 'live_pilot.py'))):
            raise ValueError('unit qualification source differs')
        published_decision(DECISION_SCRIPTS['unit'],
                           ['--result', str(QUALIFICATION_FILES['unit'])],
                           'direct_probe_pass')
    required_bundle = {'codex': value['cli']['sha256'],
                       'codex-code-mode-host': value['cli']['code_mode_host_sha256']}
    for key, schema in CONTROL_SCHEMAS.items():
        if hashes[key] is None:
            continue
        result = strict_json(QUALIFICATION_FILES[key].read_bytes())
        plan_path = CONTROL_PLANS[key]
        plan_raw = plan_path.read_bytes()
        plan = strict_json(plan_raw)
        relative_plan = plan_path.relative_to(PUBLIC).as_posix()
        commit = result.get('public_head')
        if (not isinstance(commit, str) or len(commit) != 40
                or any(char not in '0123456789abcdef' for char in commit)
                or subprocess.check_output(['git', 'show', commit + ':' + relative_plan],
                                           cwd=PUBLIC, timeout=10) != plan_raw
                or subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'HEAD'],
                                  cwd=PUBLIC, capture_output=True, timeout=10).returncode != 0
                or plan_raw != subprocess.check_output(
                    ['git', 'show', 'HEAD:' + relative_plan], cwd=PUBLIC, timeout=10)
                or plan.get('reducer_sha256') != sha(DECISION_SCRIPTS[key].read_bytes())):
            raise ValueError('qualification plan was not frozen: ' + key)
        pins = result.get('private_source_sha256')
        if (result.get('schema') != schema
                or result.get('predeclared_match') is not True
                or result.get('mismatched_fields') != []
                or result.get('private_artifacts_verified') is not True
                or result.get('cli_bundle_sha256') != required_bundle
                or result.get('expectations_sha256') != sha(plan_raw)
                or not isinstance(pins, dict)
                or any(pins.get(name) != value['code_sha256'][name]
                       for name in SOURCE_FILES)):
            raise ValueError('qualification control differs or failed: ' + key)
        published_decision(DECISION_SCRIPTS[key], [], CONTROL_DECISIONS[key])
    if hashes['independent_review'] is None:
        return
    review = strict_json(QUALIFICATION_FILES['independent_review'].read_bytes())
    candidate_pins = {name: value[name] for name in (
        'schedule_sha256', 'source_tree_sha256', 'venv_tree_sha256',
        'usage_ledger_sha256', 'code_sha256', 'cli', 'quality',
        'visible_patch_sha256', 'source_commits', 'timeout_seconds',
        'request_cap', 'method')}
    control_names = ('unit', 'late_missing', 'positive_path',
                     'normal_cli_matrix', 'integrated_path')
    if (set(review) != {'schema', 'status', 'candidate_pins',
                        'evidence_sha256', 'control_decisions', 'analysis'}
            or review['schema'] != 'solcodex.quiet-variance-bundle-qualification-review.v3'
            or review['status'] != 'qualified'
            or review['candidate_pins'] != candidate_pins
            or review['evidence_sha256'] != {key: hashes[key] for key in control_names}
            or any(hashes[key] is None for key in control_names)
            or review['control_decisions'] != {key: True for key in control_names}
            or not isinstance(review['analysis'], str)
            or len(review['analysis'].strip()) < 50):
        raise ValueError('independent qualification review differs')


def preflight(path):
    bundled_cli.validate_source_snapshot('variance_calibration_bundle_v3.py', SOURCE_FILES)
    raw = path.read_bytes()
    value = strict_json(raw)
    required = {'schema', 'status', 'model_run_authorized', 'schedule_sha256',
                'timeout_seconds', 'request_cap', 'source_tree_sha256',
                'venv_tree_sha256', 'usage_ledger_sha256', 'code_sha256',
                'cli', 'quality', 'visible_patch_sha256', 'source_commits',
                'method', 'qualification'}
    if (set(value) != required
            or value['schema'] != 'solcodex.quiet-variance-calibration.v3'
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
    validate_qualification(value)
    code = value['code_sha256']
    if (not isinstance(code, dict) or set(code) != SOURCE_FILES
            or not all(is_sha(item) for item in code.values())):
        raise ValueError('incomplete code pin set')
    for name in SOURCE_FILES:
        if sha((HERE / name).read_bytes()) != code[name]:
            raise ValueError('runner code differs: ' + name)
    runner_relative = PUBLIC_RUNNER.relative_to(PUBLIC).as_posix()
    if (sha(PUBLIC_RUNNER.read_bytes()) != code['variance_calibration_bundle_v3.py']
            or PUBLIC_RUNNER.read_bytes() != subprocess.check_output(
                ['git', 'show', 'HEAD:' + runner_relative], cwd=PUBLIC, timeout=10)):
        raise ValueError('public calibration runner differs')
    bundle_relative = PUBLIC_BUNDLE.relative_to(PUBLIC).as_posix()
    if (sha(PUBLIC_BUNDLE.read_bytes()) != code['bundled_cli_v3.py']
            or PUBLIC_BUNDLE.read_bytes() != subprocess.check_output(
                ['git', 'show', 'HEAD:' + bundle_relative], cwd=PUBLIC, timeout=10)):
        raise ValueError('public CLI bundle adapter differs')
    cli = value['cli']
    if (set(cli) != {'version', 'sha256', 'code_mode_host_sha256',
                     'model', 'effort', 'code_mode'}
            or cli['model'] != 'gpt-6-luna' or cli['effort'] != 'low'
            or cli['code_mode'] is not True or not is_sha(cli['sha256'])
            or not is_sha(cli['code_mode_host_sha256'])
            or cli['sha256'] != bundled_cli.PINNED['codex']
            or cli['code_mode_host_sha256'] != bundled_cli.PINNED['codex-code-mode-host']
            or cli['version'] != bundled_cli.VERSION):
        raise ValueError('pinned CLI bundle metadata differs')
    bundled_cli.validate_bundle()
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
    copied_cli = {name: bundled_cli.sha(root / row_id / 'tools' / name)
                  for name in bundled_cli.PINNED}
    if copied_cli != bundled_cli.PINNED:
        raise ValueError('run CLI bundle differs after execution')
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
    return {'host_files': files, 'copied_cli_sha256': copied_cli,
            'quality_report_sha256': report_sha,
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
        def bundled_plan(profile, message):
            return bundled_cli.model_plan(root / row['id'], profile, message)
        with patch.object(live_pilot, 'model_plan', side_effect=bundled_plan):
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
