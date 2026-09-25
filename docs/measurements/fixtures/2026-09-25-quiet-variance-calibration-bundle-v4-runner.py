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
import shutil
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch

import bundled_cli_v3 as bundled_cli
import campaign_state_v4 as campaign_state
import control_provider_v4
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
PUBLIC_RUNNER = PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-calibration-bundle-v4-runner.py'
PUBLIC_BUNDLE = PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-bundled-cli-v3.py'
PUBLIC_STATE = PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-campaign-state-v4.py'
PUBLIC_CONTROL = PUBLIC / 'docs/measurements/fixtures/2026-09-25-quiet-variance-control-provider-v4.py'
PUBLIC_REDUCER = PUBLIC / 'scripts/reduce_quiet_variance_calibration_v4.py'
CONTROL_SOURCE_FILES = CODE_FILES | {'variance_calibration_bundle_v3.py', 'bundled_cli_v3.py'}
SOURCE_FILES = CONTROL_SOURCE_FILES | {'variance_calibration_bundle_v4.py',
                                       'campaign_state_v4.py', 'control_provider_v4.py'}
CONTROL_PLAN = PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-bundle-v4-campaign-expectations.json'
QUALIFICATION_FILES = {
    'unit': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-current-unit-probe.json',
    'late_missing': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-negative-result.json',
    'positive_path': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-positive-result.json',
    'normal_cli_matrix': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-normal-cli-result.json',
    'integrated_path': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v3-integrated-result.json',
    'campaign_path': PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-bundle-v4-campaign-result.json',
    'independent_review': PUBLIC / 'docs/research/data/2026-09-25-quiet-variance-bundle-v4-qualification-review.json',
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


def host_python_runtime():
    """Pin the base Python as well as the copied virtual environment."""
    base = Path(sys.base_prefix).resolve(strict=True)
    origins = {}
    for name in ('json', 'hashlib', 'sqlite3', 'subprocess', 'shutil',
                 'pathlib', 'fcntl', 'os'):
        module = sys.modules[name]
        source = getattr(module, '__file__', None)
        if source is None:
            origin = module.__spec__.origin
            if origin not in ('built-in', 'frozen'):
                raise ValueError('host Python import has unknown origin: ' + name)
            origins[name] = origin
        else:
            path = Path(source).resolve(strict=True)
            if not path.is_relative_to(base):
                raise ValueError('host Python import escaped pinned base: ' + name)
            origins[name] = str(path)
    return {'executable': sys.executable,
            'resolved_executable': str(Path(sys.executable).resolve(strict=True)),
            'base_prefix': str(base), 'module_origins': origins,
            'tree_sha256': runtime_digest(base)}


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
                       for name in CONTROL_SOURCE_FILES)):
            raise ValueError('qualification control differs or failed: ' + key)
        published_decision(DECISION_SCRIPTS[key], [], CONTROL_DECISIONS[key])
    if hashes['campaign_path'] is not None:
        raise ValueError('campaign-path evidence lacks a frozen machine verifier')
    if hashes['independent_review'] is None:
        return
    review = strict_json(QUALIFICATION_FILES['independent_review'].read_bytes())
    candidate_pins = {name: value[name] for name in (
        'schedule_sha256', 'source_tree_sha256', 'venv_tree_sha256',
        'usage_ledger_sha256', 'code_sha256', 'cli', 'quality',
        'visible_patch_sha256', 'source_commits', 'timeout_seconds',
        'request_cap', 'method', 'host_python_runtime',
        'analysis_reducer_sha256', 'control_plan_sha256')}
    control_names = ('unit', 'late_missing', 'positive_path',
                     'normal_cli_matrix', 'integrated_path', 'campaign_path')
    if (set(review) != {'schema', 'status', 'candidate_pins',
                        'evidence_sha256', 'control_decisions', 'analysis'}
            or review['schema'] != 'solcodex.quiet-variance-bundle-qualification-review.v4'
            or review['status'] != 'qualified'
            or review['candidate_pins'] != candidate_pins
            or review['evidence_sha256'] != {key: hashes[key] for key in control_names}
            or any(hashes[key] is None for key in control_names)
            or review['control_decisions'] != {key: True for key in control_names}
            or not isinstance(review['analysis'], str)
            or len(review['analysis'].strip()) < 50):
        raise ValueError('independent qualification review differs')


def preflight(path):
    bundled_cli.validate_source_snapshot('variance_calibration_bundle_v4.py', SOURCE_FILES)
    raw = path.read_bytes()
    value = strict_json(raw)
    required = {'schema', 'status', 'model_run_authorized', 'schedule_sha256',
                'timeout_seconds', 'request_cap', 'source_tree_sha256',
                'venv_tree_sha256', 'usage_ledger_sha256', 'code_sha256',
                'cli', 'quality', 'visible_patch_sha256', 'source_commits',
                'method', 'qualification', 'host_python_runtime',
                'analysis_reducer_sha256', 'control_plan_sha256'}
    if (set(value) != required
            or value['schema'] != 'solcodex.quiet-variance-calibration.v4'
            or value['status'] not in ('candidate', 'frozen')
            or type(value['model_run_authorized']) is not bool
            or value['model_run_authorized'] != (value['status'] == 'frozen')
            or value['method'] != 'exposed-development-balanced-four-blocks'
            or value['timeout_seconds'] != 600 or value['request_cap'] != 32):
        raise ValueError('invalid calibration protocol')
    public_head = validate_public_freeze(path, raw)
    control_raw = CONTROL_PLAN.read_bytes()
    control_relative = CONTROL_PLAN.relative_to(PUBLIC).as_posix()
    if (not is_sha(value['control_plan_sha256'])
            or sha(control_raw) != value['control_plan_sha256']
            or control_raw != subprocess.check_output(
                ['git', 'show', 'HEAD:' + control_relative], cwd=PUBLIC, timeout=10)):
        raise ValueError('campaign control plan differs')
    control_document = strict_json(control_raw)
    if (control_document.get('schema') != 'solcodex.quiet-variance-campaign-path-expectations.v4'
            or control_document.get('status') != 'prospective_unexecuted'
            or control_document.get('model_run_authorized') is not False
            or control_document.get('schedule_sha256') != value['schedule_sha256']):
        raise ValueError('campaign control plan invalid')
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
    for source_name, public_path in (
            ('variance_calibration_bundle_v4.py', PUBLIC_RUNNER),
            ('campaign_state_v4.py', PUBLIC_STATE),
            ('control_provider_v4.py', PUBLIC_CONTROL)):
        relative_public = public_path.relative_to(PUBLIC).as_posix()
        public_raw = public_path.read_bytes()
        if (sha(public_raw) != code[source_name]
                or public_raw != subprocess.check_output(
                    ['git', 'show', 'HEAD:' + relative_public],
                    cwd=PUBLIC, timeout=10)):
            raise ValueError('public campaign source differs: ' + source_name)
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
            or runtime_digest(VENV) != value['venv_tree_sha256']
            or host_python_runtime() != value['host_python_runtime']):
        raise ValueError('ledger or Python runtime differs')
    reducer_relative = PUBLIC_REDUCER.relative_to(PUBLIC).as_posix()
    if (not is_sha(value['analysis_reducer_sha256'])
            or sha(PUBLIC_REDUCER.read_bytes()) != value['analysis_reducer_sha256']
            or PUBLIC_REDUCER.read_bytes() != subprocess.check_output(
                ['git', 'show', 'HEAD:' + reducer_relative], cwd=PUBLIC, timeout=10)):
        raise ValueError('calibration analysis reducer differs')
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


def validate_control_case(value, case):
    """A candidate can run only an implemented synthetic-provider control."""
    if (case not in control_provider_v4.SUPPORTED_CASES
            or value.get('status') != 'candidate'
            or value.get('model_run_authorized') is not False
            or value.get('qualification', {}).get('status') != 'pending'):
        raise ValueError('control-only permit unavailable')
    plan = strict_json(CONTROL_PLAN.read_bytes())
    if (sha(CONTROL_PLAN.read_bytes()) != value.get('control_plan_sha256')
            or case not in {item.get('id') for item in plan.get('cases', [])}):
        raise ValueError('control case differs from pinned plan')


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


def admissible(result):
    if not isinstance(result, dict):
        return False
    first = result.get('first_command')
    usage = result.get('usage')
    reconciliation = result.get('reconciliation')
    if (not isinstance(first, dict) or not isinstance(usage, dict)
            or not isinstance(reconciliation, dict)):
        return False
    counts = (usage.get('input_tokens'), usage.get('output_tokens'),
              usage.get('cached_input_tokens'))
    return (result.get('technical_ok') is True
            and reconciliation.get('complete') is True
            and first.get('status') == 'observed'
            and first.get('first_command_exact') is True
            and first.get('first_exit_code') == 1
            and result.get('quality_status') in ('pass', 'fail')
            and result.get('quality') is (result['quality_status'] == 'pass')
            and all(type(count) is int and count >= 0 for count in counts)
            and counts[2] <= counts[0])


def campaign_root(protocol_sha, control_case=None):
    if control_case is not None:
        if control_case not in control_provider_v4.SUPPORTED_CASES:
            raise ValueError('unsupported control campaign')
        return HERE / ('variance-control-' + control_case + '-' + protocol_sha[:16])
    return HERE / ('variance-campaign-' + protocol_sha[:16])


def execute(value, rows, protocol_raw, protocol_sha, public_head, block, protocol_path,
            control_case=None):
    if control_case is None:
        if value['model_run_authorized'] is not True:
            raise ValueError('candidate protocol forbids model execution')
    else:
        validate_control_case(value, control_case)
    root = campaign_root(protocol_sha, control_case)
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
        with (root / 'schedule.json').open('xb') as stream:
            stream.write(SCHEDULE.read_bytes())
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
    if ((root / 'schedule.json').read_bytes() != SCHEDULE.read_bytes()
            or sha((root / 'schedule.json').read_bytes()) != value['schedule_sha256']):
        raise ValueError('campaign schedule differs')
    state = campaign_state.replay(root, rows, admissible)
    block_start = 4 * (block - 1)
    block_end = 4 * block
    if (state['phase'] != 'ready'
            or not block_start <= state['next_index'] < block_end
            or state['closed_count'] != state['next_index']):
        raise ValueError('block is not the next unstarted block')
    for row in rows[state['next_index']:block_end]:
        try:
            _, _, current_sha, current_head = preflight(protocol_path)
            if current_sha != protocol_sha:
                raise ValueError('protocol changed before slot')
        except Exception as error:
            append_event(root / 'journal.jsonl',
                         {'seq': state['seq'], 'kind': 'stopped',
                          'next_id': row['id'], 'reason': 'preflight_failed:' +
                          type(error).__name__, 'utc': time.time()})
            return {**campaign_state.replay(root, rows, admissible),
                    'campaign': str(root), 'completed_block': None}
        append_event(root / 'journal.jsonl',
                     {'seq': state['seq'], 'kind': 'start', 'id': row['id'],
                      'public_head': current_head, 'utc': time.time()})
        state['seq'] += 1
        def bundled_plan(profile, message):
            return bundled_cli.model_plan(root / row['id'], profile, message)
        run_error = None
        try:
            with patch.object(live_pilot, 'model_plan', side_effect=bundled_plan):
                assignment = {key: row[key] for key in ('id', 'task', 'arm')}
                if control_case is None:
                    one_live(assignment, root, value)
                else:
                    with control_provider_v4.LocalProvider(control_case, assignment,
                                                           root) as provider:
                        one_live(assignment, root, value,
                                 credential_supplier=provider.credential,
                                 provider_connection_factory=provider.connection)
        except Exception as error:
            run_error = type(error).__name__
        sealed_sha, sealed, saved = campaign_state.seal(root, row['id'])
        try:
            _, _, after_sha, _ = preflight(protocol_path)
            pins_valid = after_sha == protocol_sha
        except Exception:
            pins_valid = False
        identity_ok = (saved is not None and saved.get('task') == row['task']
                       and saved.get('arm') == row['arm'])
        okay = (run_error is None and sealed['complete_evidence'] and identity_ok
                and admissible(saved) and pins_valid)
        disposition = ('complete' if row['id'] == rows[-1]['id'] else 'continue') if okay else 'stop'
        reason = None if okay else (('run_exception:' + run_error) if run_error else
                                    'incomplete_evidence' if not sealed['complete_evidence'] else
                                    'run_identity_differs' if not identity_ok else
                                    'measurement_or_adherence_failed' if not admissible(saved)
                                    else 'postflight_failed')
        append_event(root / 'journal.jsonl',
                     {'seq': state['seq'], 'kind': 'finish', 'id': row['id'],
                      'final_sha256': sealed['host_files'].get('final.json'),
                      'seal_sha256': sealed_sha, 'disposition': disposition,
                      'reason': reason, 'run_error': run_error,
                      'admissible': okay, 'pins_valid': pins_valid,
                      'quality_status': saved.get('quality_status') if saved else None,
                      'utc': time.time()})
        state = campaign_state.replay(root, rows, admissible)
        if not okay:
            return {**state, 'campaign': str(root), 'completed_block': None}
    return {**state, 'campaign': str(root), 'completed_block': block}


def stop_existing_on_entry_failure(path, error, control_case=None):
    """Record pre-start drift in an already-created campaign without launching."""
    raw = path.read_bytes()
    root = campaign_root(sha(raw), control_case)
    if not root.is_dir() or (root / 'protocol.json').read_bytes() != raw:
        return None
    schedule_raw = (root / 'schedule.json').read_bytes()
    rows = schedule(schedule_raw)
    state = campaign_state.replay(root, rows, admissible)
    if state['phase'] != 'ready' or state['next_index'] >= len(rows):
        return {**state, 'campaign': str(root), 'completed_block': None}
    append_event(root / 'journal.jsonl',
                 {'seq': state['seq'], 'kind': 'stopped',
                  'next_id': rows[state['next_index']]['id'],
                  'reason': 'entry_preflight_failed:' + type(error).__name__,
                  'utc': time.time()})
    return {**campaign_state.replay(root, rows, admissible),
            'campaign': str(root), 'completed_block': None}


def export_analysis(path, control_case=None):
    protocol_raw = path.read_bytes()
    root = campaign_root(sha(protocol_raw), control_case)
    if (root / 'protocol.json').read_bytes() != protocol_raw:
        raise ValueError('campaign protocol differs')
    protocol = strict_json(protocol_raw)
    schedule_raw = (root / 'schedule.json').read_bytes()
    if (not isinstance(protocol, dict)
            or protocol.get('schedule_sha256') != sha(schedule_raw)):
        raise ValueError('campaign schedule differs from protocol pin')
    return campaign_state.analysis_input(
        root, schedule(schedule_raw), admissible, sha(schedule_raw))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--block', type=int, choices=range(1, 5))
    parser.add_argument('--export-analysis', action='store_true')
    parser.add_argument('--control-case')
    args = parser.parse_args()
    if (args.execute != (args.block is not None)
            or args.export_analysis and args.execute):
        parser.error('--execute and --block must be paired and exclude --export-analysis')
    os.umask(0o077)
    with (HERE / 'execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = args.protocol.resolve(strict=True)
        if args.export_analysis:
            projection = export_analysis(path, args.control_case)
            print(json.dumps(projection, sort_keys=True, separators=(',', ':'),
                             allow_nan=False))
            return 0
        try:
            value, rows, protocol_sha, public_head = preflight(path)
        except Exception as error:
            if not args.execute:
                raise
            stopped = stop_existing_on_entry_failure(path, error, args.control_case)
            if stopped is None:
                raise
            print(json.dumps(stopped, sort_keys=True))
            return 2
        if not args.execute:
            print(json.dumps({'preflight': 'pass', 'model_run_authorized':
                              value['model_run_authorized'], 'slots': len(rows),
                              'protocol_sha256': protocol_sha, 'public_head': public_head},
                             sort_keys=True))
            return 0
        if args.control_case is not None:
            validate_control_case(value, args.control_case)
        result = execute(value, rows, path.read_bytes(), protocol_sha,
                         public_head, args.block, path, args.control_case)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['phase'] in ('ready', 'complete') else 2


if __name__ == '__main__':
    sys.exit(main())
