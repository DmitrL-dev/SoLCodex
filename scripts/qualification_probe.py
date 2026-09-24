"""No-model probes of the prospective measurement path.

This reports observations only. Expected outcomes live in a separately
reviewed qualification matrix and must be frozen before promotion.
"""

import hashlib
import json
from pathlib import Path

import audit
import evaluate
import live_pilot

from audit import audit_trace
from evaluate import case_result
from live_pilot import reconcile


def encoded(events):
    return '\n'.join(json.dumps(event, sort_keys=True) for event in events) + '\n'


def command_events(exit_code=1, status='failed'):
    return [
        {'type': 'thread.started'},
        {'type': 'turn.started'},
        {'type': 'item.started', 'item': {
            'type': 'command_execution', 'id': 'first', 'command': 'cmd'}},
        {'type': 'item.completed', 'item': {
            'type': 'command_execution', 'id': 'first', 'command': 'cmd',
            'status': status, 'exit_code': exit_code}},
        {'type': 'turn.completed'},
    ]


def probe():
    passing = command_events()
    command_cases = {
        'complete_exit_1': (encoded(passing), 1),
        'complete_exit_0': (encoded(command_events(0, 'completed')), 0),
        'baseline_exit_mismatch': (encoded(command_events(0, 'completed')), 1),
        'missing_turn_completion': (encoded(passing[:-1]), 1),
        'missing_thread_start': (encoded(passing[1:]), 1),
        'unfinished_command': (encoded(passing[:3]), 1),
        'signal_exit': (encoded(command_events(-9, 'failed')), 1),
        'prior_file_change': (encoded(passing[:2] + [
            {'type': 'item.completed', 'item': {'type': 'file_change', 'id': 'edit'}}
        ] + passing[2:]), 1),
        'malformed_json': (encoded(passing) + '{broken\n', 1),
        'duplicate_completion': (encoded(passing[:-1] + [passing[-2], passing[-1]]), 1),
        'error_item': (encoded(passing[:2] + [
            {'type': 'item.completed', 'item': {'type': 'error', 'id': 'failure'}}
        ] + passing[2:]), 1),
        'missing_command_id': (encoded([
            {**event, 'item': {key: value for key, value in event['item'].items()
                                if key != 'id'}}
            if event['type'] in ('item.started', 'item.completed') else event
            for event in passing]), 1),
        'wrong_command': (encoded([
            {**event, 'item': {**event['item'], 'command': 'other'}}
            if event['type'] in ('item.started', 'item.completed') else event
            for event in passing]), 1),
        'wrong_status': (encoded(command_events(1, 'completed')), 1),
    }
    traces = {}
    for name, (trace, expected_exit) in command_cases.items():
        result = audit_trace(trace, 'cmd', expected_exit=expected_exit)
        traces[name] = {key: result[key] for key in (
            'status', 'first_command_exact', 'trace_complete',
            'first_command_completed', 'first_exit_code')}

    delivery = {'requests': [{'attempt_id': 'a', 'state': 'disconnected'}],
                'ledger_error': False, 'accounting': {'attempts': 1}}
    upstream = {'attempts': {'a': {'state': 'completed'}},
                'ledger_error': False, 'accounting': {'attempts': 1},
                'usage': {'input_tokens': 10, 'output_tokens': 2,
                          'cached_input_tokens': 0}}
    ledgers = {
        'late_provider_completion': reconcile(delivery, upstream),
        'missing_usage': reconcile(delivery, {**upstream, 'usage': None}),
        'partial_usage': reconcile(delivery, {
            **upstream, 'usage': {'input_tokens': 10}}),
        'upstream_unknown': reconcile(delivery, {
            **upstream, 'attempts': {'a': {'state': 'unknown'}}}),
        'missing_upstream_id': reconcile(delivery, {
            **upstream, 'attempts': {'b': {'state': 'completed'}}}),
    }
    try:
        case_result(b'PASS\nTOTAL 1/1\n', 'synthetic_case', True)
    except (ValueError, TypeError) as error:
        fake_success = {'accepted': False, 'error': type(error).__name__}
    else:
        fake_success = {'accepted': True, 'error': None}
    sources = {'audit.py': audit, 'evaluate.py': evaluate,
               'live_pilot.py': live_pilot}
    source_sha256 = {name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                     for name, module in sources.items()}
    source_sha256['qualification_probe.py'] = hashlib.sha256(
        Path(__file__).read_bytes()).hexdigest()
    return {'schema': 'solcodex.quiet-measurement-unit-probe.v1',
            'scope': 'direct_no_model_probes_only',
            'source_sha256': source_sha256,
            'observations': {'traces': traces, 'ledgers': ledgers,
                             'fake_success': fake_success}}


if __name__ == '__main__':
    print(json.dumps(probe(), sort_keys=True, indent=2))
