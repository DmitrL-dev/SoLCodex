"""Durable campaign state and partial evidence seals for the v4 pilot."""

import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from contextlib import closing

import bundled_cli_v3 as bundled_cli
from pilot import HERE
from protocol_gate import strict_json
from snapshot import manifest


REQUIRED_HOST_FILES = frozenset({
    'final.json', 'result.json', 'trace.jsonl', 'stderr.txt',
    'delivery.sqlite3', 'upstream.sqlite3', 'baseline.json',
})
USAGE_KEYS = ('input_tokens', 'cached_input_tokens', 'output_tokens')
HARNESS = Path(__file__).resolve().parent


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_usage(value):
    return (isinstance(value, dict) and set(value) == set(USAGE_KEYS)
            and all(type(value[key]) is int and value[key] >= 0 for key in USAGE_KEYS)
            and value['cached_input_tokens'] <= value['input_tokens'])


def _read_ledger(host, name, row_id, expected_arm):
    path = host / name
    if path.is_symlink() or not path.is_file():
        return None, None, ['ledger_missing_or_symlink:' + name]
    sidecar = any((host / (name + suffix)).exists() for suffix in ('-wal', '-shm'))
    errors = ['ledger_sidecar_present:' + name] if sidecar else []
    attempts = {}
    response_ids = set()
    known = {key: 0 for key in USAGE_KEYS}
    try:
        mode = '?mode=ro' if sidecar else '?mode=ro&immutable=1'
        with closing(sqlite3.connect(path.resolve(strict=True).as_uri() + mode,
                                     uri=True, timeout=5)) as database:
            if database.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise ValueError('integrity check failed')
            rows = database.execute(
                'SELECT attempt_id,run_id,arm,state,response_id,upstream_status,'
                'input_tokens,output_tokens,cached_input_tokens,reason '
                'FROM attempts ORDER BY attempt_id').fetchall()
        for (attempt_id, run_id, arm, state, response_id, upstream_status,
             inputs, outputs, cached, reason) in rows:
            if (not isinstance(attempt_id, str) or not attempt_id
                    or attempt_id in attempts or run_id != row_id or arm != expected_arm
                    or state not in ('pending', 'unknown', 'completed')):
                errors.append('ledger_identity_or_state:' + name)
                continue
            usage = {'input_tokens': inputs, 'cached_input_tokens': cached,
                     'output_tokens': outputs} if state == 'completed' else None
            if state == 'completed':
                if not isinstance(response_id, str) or not response_id or not valid_usage(usage):
                    errors.append('ledger_completed_usage_invalid:' + name)
                    continue
                if response_id in response_ids:
                    errors.append('ledger_duplicate_response:' + name)
                    continue
                response_ids.add(response_id)
                for key in USAGE_KEYS:
                    known[key] += usage[key]
            attempts[attempt_id] = {'state': state, 'response_id': response_id,
                                    'upstream_status': upstream_status,
                                    'usage': usage, 'reason': reason}
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None, None, ['ledger_unreadable:' + name]
    return attempts, known, errors


def ledger_observation(host, row_id, source_files=None):
    """Read a ledger snapshot and retain an upstream lower bound."""
    if source_files is not None:
        with tempfile.TemporaryDirectory(prefix='v4-ledger-snapshot-') as directory:
            snapshot = Path(directory)
            for name in ('delivery.sqlite3', 'delivery.sqlite3-wal',
                         'delivery.sqlite3-shm', 'upstream.sqlite3',
                         'upstream.sqlite3-wal', 'upstream.sqlite3-shm'):
                if name in source_files:
                    (snapshot / name).write_bytes(source_files[name])
            return ledger_observation(snapshot, row_id)
    delivery, _, delivery_errors = _read_ledger(
        host, 'delivery.sqlite3', row_id, 'private')
    upstream, known, upstream_errors = _read_ledger(
        host, 'upstream.sqlite3', row_id, 'host_upstream')
    errors = delivery_errors + upstream_errors
    complete = False
    if delivery is not None and upstream is not None:
        if not delivery or set(delivery) != set(upstream):
            errors.append('ledger_attempt_ids_differ_or_empty')
        else:
            for attempt_id in delivery:
                observed = delivery[attempt_id]
                source = upstream[attempt_id]
                if observed['state'] == 'completed' and observed != source:
                    errors.append('ledger_attempt_differs:' + attempt_id)
                elif (observed['state'] != 'completed'
                      and source['state'] != 'completed'):
                    errors.append('upstream_attempt_incomplete:' + attempt_id)
            complete = (not errors and all(row['state'] == 'completed'
                                            for row in upstream.values()))
            if not complete and not errors:
                errors.append('ledger_usage_incomplete')
    upstream_hard_errors = [error for error in upstream_errors
                            if not error.startswith('ledger_sidecar_present:')]
    return {'complete': complete,
            'verified_usage': known if complete else None,
            'known_upstream_usage': known if not upstream_hard_errors else None,
            'upstream_attempt_count': len(upstream) if upstream is not None else None,
            'delivery_attempts': delivery,
            'upstream_attempts': upstream,
            'errors': sorted(errors)}


def accounting_closed(saved, ledger, row_id):
    """Require closed handlers and one-to-one broker, bridge, and ledger attempts."""
    if not isinstance(saved, dict) or not ledger['complete']:
        return False
    cleanup, broker, bridge = (saved.get(key) for key in
                               ('cleanup', 'broker', 'bridge'))
    reconciliation = saved.get('reconciliation')
    if not all(isinstance(item, dict) for item in
               (cleanup, broker, bridge, reconciliation)):
        return False
    requests = broker.get('requests')
    bridge_attempts = bridge.get('attempts')
    delivery = ledger['delivery_attempts']
    upstream = ledger['upstream_attempts']
    count = ledger['upstream_attempt_count']
    verified = ledger['verified_usage']
    if (saved.get('id') != row_id
            or cleanup.get('verified') is not True
            or saved.get('broker_stopped') is not True
            or saved.get('bridge_stopped') is not True
            or broker.get('run_id') != row_id or bridge.get('run_id') != row_id
            or broker.get('ledger_error') is not False
            or bridge.get('ledger_error') is not False
            or type(bridge.get('active_handlers')) is not int
            or bridge['active_handlers'] != 0
            or not isinstance(requests, list)
            or not isinstance(bridge_attempts, dict)
            or not isinstance(delivery, dict) or not isinstance(upstream, dict)
            or not isinstance(broker.get('accounting'), dict)
            or not isinstance(bridge.get('accounting'), dict)
            or reconciliation.get('complete') is not True
            or reconciliation.get('usage') != verified
            or saved.get('usage') != verified
            or bridge.get('usage') != verified
            or broker['accounting'].get('attempts') != count
            or bridge['accounting'].get('attempts') != count
            or bridge['accounting'].get('all_attempts_have_observed_usage') is not True
            or bridge['accounting'].get('observed_completed_usage') != verified
            or broker.get('model_requests_forwarded') != count
            or len(requests) != count or len(bridge_attempts) != count
            or set(bridge_attempts) != set(upstream)):
        return False
    request_ids = [row.get('attempt_id') for row in requests
                   if isinstance(row, dict)]
    if len(request_ids) != count or set(request_ids) != set(delivery):
        return False
    for row in requests:
        attempt_id = row['attempt_id']
        observed = delivery[attempt_id]
        source = upstream[attempt_id]
        bridged = bridge_attempts[attempt_id]
        if (row.get('upstream_attempted') is not True
                or row.get('forwarded') is not True
                or row.get('completed') is not (observed['state'] == 'completed')
                or row.get('usage') != observed['usage']
                or row.get('status') != observed['upstream_status']
                or not isinstance(bridged, dict)
                or bridged.get('state') != source['state']
                or bridged.get('upstream_status') != source['upstream_status']
                or any(bridged.get(key) != source['usage'][key]
                       for key in USAGE_KEYS)):
            return False
    return True


def inventory(root, row_id):
    """Describe available evidence; uncertainty is data, not an exception."""
    run = root / row_id
    host = run / 'host-artifacts'
    files = {}
    host_bytes = {}
    issues = []
    campaign_pins = {}
    for name in ('protocol.json', 'schedule.json'):
        path = root / name
        if path.is_symlink() or not path.is_file():
            issues.append('campaign_pin_missing_or_symlink:' + name)
            campaign_pins[name] = None
        else:
            try:
                campaign_pins[name] = sha(path.read_bytes())
            except OSError:
                issues.append('campaign_pin_unreadable:' + name)
                campaign_pins[name] = None
    if not host.is_dir() or host.is_symlink():
        issues.append('host_artifacts_missing_or_symlink')
    else:
        for path in sorted(host.iterdir()):
            if path.name == 'snapshot' and path.is_dir() and not path.is_symlink():
                continue
            if path.is_symlink() or not path.is_file():
                issues.append('nonregular_host_entry:' + path.name)
                continue
            try:
                raw = path.read_bytes()
                host_bytes[path.name] = raw
                files[path.name] = sha(raw)
            except OSError:
                issues.append('unreadable_host_file:' + path.name)
    missing = sorted(REQUIRED_HOST_FILES - files.keys())
    if missing:
        issues.append('mandatory_host_files_missing')
    ledger = ledger_observation(host, row_id, host_bytes)
    if not ledger['complete']:
        issues.append('usage_ledger_incomplete')

    copied_cli = {}
    for name, expected in bundled_cli.PINNED.items():
        path = run / 'tools' / name
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError('missing or symlink')
            copied_cli[name] = bundled_cli.sha(path)
            if copied_cli[name] != expected:
                issues.append('cli_hash_differs:' + name)
        except (OSError, ValueError):
            issues.append('cli_unavailable:' + name)

    result = None
    if 'result.json' in files:
        try:
            if len(host_bytes['result.json']) > 16_000_000:
                raise ValueError('result too large')
            result = strict_json(host_bytes['result.json'])
            if not isinstance(result, dict):
                raise ValueError('result is not an object')
            if result.get('id') != row_id:
                issues.append('result_id_differs')
            recorded = result.get('artifacts')
            actual = {name: digest for name, digest in files.items()
                      if name not in ('result.json', 'final.json')}
            if recorded != actual:
                issues.append('result_artifacts_differ')
        except (OSError, ValueError, RecursionError):
            issues.append('result_invalid')
            result = None

    saved = None
    if 'final.json' in files:
        try:
            if len(host_bytes['final.json']) > 16_000_000:
                raise ValueError('final too large')
            saved = strict_json(host_bytes['final.json'])
            if not isinstance(saved, dict):
                raise ValueError('final is not an object')
            if saved.get('id') != row_id:
                issues.append('final_id_differs')
        except (OSError, ValueError, RecursionError):
            issues.append('final_invalid')
            saved = None
    if saved is not None and result is not None:
        if any(key not in saved or saved[key] != value
               for key, value in result.items() if key != 'quality'):
            issues.append('final_result_differs')
    if saved is not None and ledger['complete']:
        reconciliation = saved.get('reconciliation')
        verified = ledger['verified_usage']
        if (not isinstance(reconciliation, dict)
                or reconciliation.get('complete') is not True
                or saved.get('usage') != verified
                or reconciliation.get('usage') != verified):
            issues.append('final_usage_differs_from_ledgers')
    closed = accounting_closed(saved, ledger, row_id)
    if not closed:
        issues.append('accounting_not_closed_or_inconsistent')

    checkpoint_tree = None
    report_sha = None
    report_artifacts = None
    baseline_tree = None
    if 'baseline.json' in host_bytes:
        try:
            if len(host_bytes['baseline.json']) > 16_000_000:
                raise ValueError('baseline too large')
            baseline = strict_json(host_bytes['baseline.json'])
            if not isinstance(baseline, dict):
                raise ValueError('baseline is not an object')
            baseline_tree = baseline['tree_sha256']
        except (ValueError, KeyError, TypeError, RecursionError):
            issues.append('baseline_invalid')
    if saved is not None:
        checkpoint = saved.get('checkpoint')
        if not isinstance(checkpoint, dict):
            issues.append('checkpoint_invalid')
            checkpoint = {}
        if checkpoint.get('status') == 'captured':
            try:
                observed = manifest(host / 'snapshot')
                if observed != checkpoint.get('manifest'):
                    issues.append('checkpoint_manifest_differs')
                else:
                    checkpoint_tree = observed['tree_sha256']
            except (OSError, ValueError, KeyError):
                issues.append('checkpoint_unverifiable')
        else:
            issues.append('checkpoint_not_captured')
        try:
            report_root = Path(saved['quality_report_root']).resolve(strict=True)
            if not report_root.is_relative_to(HERE.resolve(strict=True)):
                raise ValueError('report root escaped private harness')
            report_path = report_root / 'report.json'
            if report_path.is_symlink():
                raise ValueError('quality report is a symlink')
            report_raw = report_path.read_bytes()
            report_sha = sha(report_raw)
            if len(report_raw) > 16_000_000:
                raise ValueError('quality report too large')
            report = strict_json(report_raw)
            if not isinstance(report, dict):
                raise ValueError('quality report is not an object')
            if (report_sha != saved.get('quality_report_sha256')
                    or report.get('root') != str(report_root)
                    or report.get('evidence_sha256') != files.get('result.json')
                    or report.get('evaluator_sha256') != sha((HARNESS / 'evaluate.py').read_bytes())
                    or report.get('assets_lock_sha256') != sha(
                        (HARNESS / 'quality-assets/lock.json').read_bytes())
                    or report.get('baseline_tree_sha256') != baseline_tree
                    or report.get('task') != saved.get('task')
                    or report.get('status') != saved.get('quality_status')
                    or report.get('quality') is not saved.get('quality')
                    or report.get('candidate_tree_sha256') != checkpoint_tree):
                raise ValueError('quality report differs')
            report_host = report_root / 'host'
            if report_host.is_symlink() or not report_host.is_dir():
                raise ValueError('quality artifacts unavailable')
            if any(path.is_symlink() or not path.is_file()
                   for path in report_host.iterdir()):
                raise ValueError('nonregular quality artifact')
            report_artifacts = {
                str(path.relative_to(report_root)): sha(path.read_bytes())
                for path in report_host.iterdir()
            }
            if report_artifacts != report.get('artifact_hashes'):
                raise ValueError('quality artifacts differ')
        except (OSError, ValueError, KeyError, TypeError, AttributeError,
                RecursionError):
            issues.append('quality_report_unverifiable')
    payload = {
        'schema': 'solcodex.quiet-campaign-partial-seal.v4',
        'id': row_id,
        'campaign_pins': campaign_pins,
        'host_files': files,
        'missing_host_files': missing,
        'copied_cli_sha256': copied_cli,
        'checkpoint_tree_sha256': checkpoint_tree,
        'quality_report_sha256': report_sha,
        'quality_report_artifacts': report_artifacts,
        'ledger': ledger,
        'accounting_closed': closed,
        'issues': sorted(set(issues)),
        'complete_evidence': not issues,
    }
    return payload, saved


def seal(root, row_id):
    """Write the inventory once before a terminal journal event."""
    run = root / row_id
    if run.is_symlink():
        raise ValueError('run root is a symlink')
    run.mkdir(mode=0o700, exist_ok=True)
    payload, saved = inventory(root, row_id)
    raw = (json.dumps(payload, sort_keys=True, separators=(',', ':'),
                      allow_nan=False) + '\n').encode()
    with (run / 'seal.json').open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(run, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return sha(raw), payload, saved


def replay(root, rows, admissible):
    """Validate the journal and sealed evidence; never infer a missing finish."""
    raw = (root / 'journal.jsonl').read_bytes()
    if raw and not raw.endswith(b'\n'):
        raise ValueError('incomplete journal line; no automatic retry')
    events = [strict_json(line) for line in raw.splitlines()]
    phase = 'ready'
    pending = None
    next_index = 0
    closed = 0
    for seq, event in enumerate(events):
        if event.get('seq') != seq:
            raise ValueError('invalid journal sequence')
        kind = event.get('kind')
        if kind == 'start':
            if (phase != 'ready' or pending is not None or next_index >= len(rows)
                    or event.get('id') != rows[next_index]['id']):
                raise ValueError('out-of-order start')
            pending = rows[next_index]
            next_index += 1
        elif kind == 'finish':
            if pending is None or event.get('id') != pending['id']:
                raise ValueError('unpaired finish')
            row_id = pending['id']
            seal_path = root / row_id / 'seal.json'
            if seal_path.is_symlink() or not seal_path.is_file():
                raise ValueError('run seal missing')
            seal_raw = seal_path.read_bytes()
            if sha(seal_raw) != event.get('seal_sha256'):
                raise ValueError('run seal hash differs')
            sealed = strict_json(seal_raw)
            observed, saved = inventory(root, row_id)
            if sealed != observed:
                raise ValueError('run artifacts differ from seal')
            final_sha = observed['host_files'].get('final.json')
            if event.get('final_sha256') != final_sha:
                raise ValueError('final hash differs')
            identity_ok = (saved is not None
                           and saved.get('task') == pending['task']
                           and saved.get('arm') == pending['arm'])
            run_error = event.get('run_error')
            if run_error is not None and (not isinstance(run_error, str)
                                          or not run_error.strip()):
                raise ValueError('invalid runner exception record')
            valid = (run_error is None and observed['complete_evidence'] and identity_ok
                     and admissible(saved) and event.get('pins_valid') is True)
            if event.get('admissible') is not valid:
                raise ValueError('journal admission differs from sealed evidence')
            disposition = event.get('disposition')
            if valid:
                expected = 'complete' if next_index == len(rows) else 'continue'
                if disposition != expected or event.get('reason') is not None:
                    raise ValueError('invalid successful disposition')
            elif disposition != 'stop' or not isinstance(event.get('reason'), str):
                raise ValueError('failure did not stop campaign')
            closed += 1
            pending = None
            if disposition in ('stop', 'complete'):
                phase = disposition
        elif kind == 'stopped':
            if (phase != 'ready' or pending is not None or next_index >= len(rows)
                    or event.get('next_id') != rows[next_index]['id']
                    or not isinstance(event.get('reason'), str)
                    or not event['reason'].strip()):
                raise ValueError('invalid pre-start stop')
            phase = 'stop'
        else:
            raise ValueError('unknown journal event')
    if pending is not None:
        phase = 'unresolved'
    return {
        'phase': phase, 'next_index': next_index,
        'closed_count': closed, 'seq': len(events),
        'unresolved_ids': [pending['id']] if pending else [],
        'unstarted_ids': [row['id'] for row in rows[next_index:]],
    }


def analysis_input(root, rows, admissible, schedule_sha256):
    """Project sealed campaign evidence to the public arithmetic schema."""
    protocol_path = root / 'protocol.json'
    schedule_path = root / 'schedule.json'
    if (protocol_path.is_symlink() or schedule_path.is_symlink()
            or not protocol_path.is_file() or not schedule_path.is_file()):
        raise ValueError('campaign pins missing or symlinked')
    protocol_sha256 = sha(protocol_path.read_bytes())
    if sha(schedule_path.read_bytes()) != schedule_sha256:
        raise ValueError('campaign schedule differs during export')
    state = replay(root, rows, admissible)
    events = [strict_json(line) for line in (root / 'journal.jsonl').read_bytes().splitlines()]
    finishes = {event['id']: event for event in events if event['kind'] == 'finish'}
    starts = {event['id'] for event in events if event['kind'] == 'start'}
    stop_event = next((event for event in events if event['kind'] == 'stopped'), None)
    campaign_state = {'ready': 'paused', 'stop': 'stopped',
                      'complete': 'complete', 'unresolved': 'unresolved'}[state['phase']]
    reason = (None if campaign_state == 'complete' else
              stop_event['reason'] if stop_event else
              next((event['reason'] for event in events[::-1]
                    if event['kind'] == 'finish' and event['disposition'] == 'stop'), None)
              if campaign_state == 'stopped' else
              'unclosed_start' if campaign_state == 'unresolved' else
              'schedule_not_finished')

    def closed_accounting(saved, observed, assignment):
        return (observed['accounting_closed']
                and not any(issue in observed['issues'] for issue in (
                    'final_id_differs', 'result_id_differs', 'result_invalid',
                    'final_result_differs', 'result_artifacts_differ'))
                and saved is not None
                and all(saved.get(key) == assignment[key]
                        for key in ('id', 'task', 'arm')))

    slots = []
    for assignment in rows:
        row_id = assignment['id']
        event = finishes.get(row_id)
        observed, saved = inventory(root, row_id) if row_id in starts else (None, None)
        slot = {**assignment, 'status': 'unstarted', 'quality': None,
                'usage_state': 'not_started', 'usage': None,
                'known_upstream_usage': None, 'accounting_evidence_sha256': None,
                'elapsed_seconds': None, 'timed_out': None, 'reason': None}
        if observed is not None:
            if observed['campaign_pins'] != {'protocol.json': protocol_sha256,
                                            'schedule.json': schedule_sha256}:
                raise ValueError('campaign pins changed during export')
            if event is not None:
                seal_raw = (root / row_id / 'seal.json').read_bytes()
                if (sha(seal_raw) != event['seal_sha256']
                        or strict_json(seal_raw) != observed):
                    raise ValueError('campaign evidence changed during export')
                slot['accounting_evidence_sha256'] = event['seal_sha256']
            else:
                snapshot_raw = json.dumps(observed, sort_keys=True,
                                          separators=(',', ':'), allow_nan=False).encode()
                slot['accounting_evidence_sha256'] = sha(snapshot_raw)
            known = observed['ledger']['known_upstream_usage']
            if known is not None:
                slot['known_upstream_usage'] = known
                slot['usage_state'] = 'lower_bound'
            else:
                slot['usage_state'] = 'unknown'
            if event is not None and closed_accounting(saved, observed, assignment):
                slot['usage_state'] = 'verified_complete'
                slot['usage'] = observed['ledger']['verified_usage']
        if event is not None:
            if event['disposition'] == 'stop':
                slot['status'] = 'stopped'
                slot['reason'] = event['reason']
            else:
                slot['status'] = 'completed'
                slot['quality'] = saved['quality']
                slot['timed_out'] = saved['timed_out']
            owned = (saved is not None and all(
                saved.get(key) == assignment[key] for key in ('id', 'task', 'arm')))
            elapsed = saved.get('elapsed_seconds') if owned else None
            if (type(elapsed) in (int, float) and math.isfinite(elapsed)
                    and elapsed >= 0):
                slot['elapsed_seconds'] = elapsed
        elif row_id in starts:
            slot['status'] = 'unresolved'
            slot['reason'] = 'unclosed_start'
        slots.append(slot)
    return {
        'schema': 'solcodex.quiet-variance-calibration-analysis-input.v4',
        'protocol_sha256': protocol_sha256,
        'schedule_sha256': schedule_sha256,
        'campaign_state': campaign_state,
        'terminal_reason': reason,
        'slots': slots,
    }
