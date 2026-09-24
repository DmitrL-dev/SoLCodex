"""Run the no-model two-attempt transport fixture against a private harness.

The published fixture source must match the private test module byte for byte.
The stub provider and credential are synthetic; this never launches a model.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / 'docs/measurements/fixtures/2026-09-25-test-live-pilot.py'
PRIVATE_SOURCES = ('audit.py', 'broker_route.py', 'evaluate.py', 'host_bridge.py',
                   'isolation.py', 'live_pilot.py', 'pilot.py', 'process.py',
                   'runtime_manifest.py', 'snapshot.py', 'test_host_bridge.py',
                   'test_live_pilot.py', 'selftest-protocol.json')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identities(row):
    records = row['broker']['requests']
    ids = [record['attempt_id'] for record in records]
    return len(ids) == 2 and len(set(ids)) == 2 and set(ids) == set(row['bridge']['attempts'])


def positive(row, receipts):
    records = row['broker']['requests']
    states = row['bridge']['accounting']['states']
    return {
        'provider_receipts': len(receipts),
        'distinct_attempt_ids': identities(row),
        'broker_first_delivery_failed': bool(records[0]['error']) and
                                        records[0]['completed'] is False,
        'broker_second_delivery_complete': records[1]['completed'] is True,
        'upstream_completed': states['completed'],
        'upstream_unknown': states['unknown'],
        'usage': row['usage'],
        'reconciliation_complete': row['reconciliation']['complete'],
        'late_completion_after_cli':
            row['elapsed_seconds'] - row['cli_elapsed_seconds'] > .15,
        'first_command_exact': row['first_command']['first_command_exact'],
        'checkpoint_captured': row['checkpoint']['status'] == 'captured',
        'source_unchanged': row['source_unchanged'],
        'quality_qualified': False,  # The evaluator is intentionally synthetic.
        'provider_billing_complete': row['provider_billing_complete'],
    }


def missing_usage(row, receipts):
    states = row['bridge']['accounting']['states']
    return {
        'provider_receipts': len(receipts),
        'distinct_attempt_ids': identities(row),
        'upstream_completed': states['completed'],
        'upstream_unknown': states['unknown'],
        'usage': row['usage'],
        'observed_subtotal': row['bridge']['accounting']['observed_completed_usage'],
        'reconciliation_complete': row['reconciliation']['complete'],
        'first_command_exact': row['first_command']['first_command_exact'],
        'checkpoint_captured': row['checkpoint']['status'] == 'captured',
        'source_unchanged': row['source_unchanged'],
        'quality_qualified': False,
        'provider_billing_complete': row['provider_billing_complete'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness', type=Path, required=True)
    args = parser.parse_args()
    harness = args.harness.resolve(strict=True)
    if sha(FIXTURE) != sha(harness / 'test_live_pilot.py'):
        parser.error('private fixture differs from published source')
    sys.path.insert(0, str(harness))
    from test_live_pilot import LivePilotTests
    fixture = LivePilotTests('test_two_attempts_count_late_completion_after_client_disconnect')
    positive_row, positive_receipts, positive_errors, _ = fixture.run_two_attempts(
        first_usage=True)
    negative_row, negative_receipts, negative_errors, _ = fixture.run_two_attempts(
        first_usage=False)
    if positive_errors or negative_errors:
        raise RuntimeError('synthetic provider reported an error')
    sources = {name: sha(harness / name) for name in PRIVATE_SOURCES}
    sources['usage_attempt_ledger.py'] = sha(ROOT / 'scripts/usage_attempt_ledger.py')
    sources['qualify_two_attempt_transport.py'] = sha(Path(__file__))
    result = {
        'schema': 'solcodex.two-attempt-transport-probe.v1',
        'scope': 'synthetic_no_model_transport',
        'wall_time_boundary': 'cli_launch_to_upstream_closure',
        'model_run_authorized': False,
        'source_sha256': sources,
        'positive': positive(positive_row, positive_receipts),
        'missing_first_usage': missing_usage(negative_row, negative_receipts),
    }
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
