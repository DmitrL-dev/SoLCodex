"""Pinned-bundle no-model control: late third response lacks usage."""

import json
from pathlib import Path
from unittest.mock import patch

import cli_late_checkpoint_bundle_probe_v3 as late
import cli_toolcall_probe as wire
from test_host_bridge import event


ORIGINAL_PAYLOAD = wire.payload


def omit_late_usage(identifier, item, usage):
    if identifier != 'resp_synthetic_3':
        return ORIGINAL_PAYLOAD(identifier, item, usage)
    if item is not None:
        raise ValueError('late control must have no tool call')
    completed = wire.response(identifier, [], usage)
    completed.pop('usage')
    return event({'type': 'response.completed', 'response': completed}) + b'data: [DONE]\n\n'


def run():
    with patch.object(wire, 'payload', side_effect=omit_late_usage):
        summary = late.run()
    root = Path(summary['private_root']) / '03-packaging-on/host-artifacts'
    final = json.loads((root / 'final.json').read_text())
    requests = final['broker']['requests']
    third_id = requests[2]['attempt_id'] if len(requests) >= 3 else None
    third = final['bridge']['attempts'].get(third_id, {})
    reconciliation = final['reconciliation']
    return {
        'schema': 'solcodex.quiet-variance-bundle-negative-observations.v1',
        'scope': 'pinned_bundle_no_model_late_missing_usage',
        'private_root': summary['private_root'],
        'provider_requests': summary['provider_requests'],
        'provider_errors': summary['provider_errors'],
        'third_request_seen': summary['third_request_seen'],
        'late_completion_after_runner': summary['late_completion_after_runner'],
        'diagnostic_signature': summary['diagnostic_signature'],
        'trace_sha256': summary['trace_sha256'],
        'quality_report_sha256': summary['quality_report_sha256'],
        'exit_code': summary['exit_code'],
        'timed_out': summary['timed_out'],
        'request_items': summary['request_items'],
        'trace_event_shapes': summary['trace_event_shapes'],
        'first_command': summary['first_command'],
        'checkpoint_status': summary['checkpoint_status'],
        'checkpoint_tree_sha256': summary['checkpoint_tree_sha256'],
        'quality_status': summary['quality_status'],
        'cleanup_verified': summary['cleanup_verified'],
        'broker_stopped': summary['broker_stopped'],
        'bridge_stopped': summary['bridge_stopped'],
        'reconciliation_complete': summary['reconciliation_complete'],
        'usage': summary['usage'],
        'technical_ok': summary['technical_ok'],
        'broker_attempts': summary['broker_attempts'],
        'upstream_states': summary['upstream_states'],
        'broker_usage_status': final['broker']['usage_status'],
        'upstream_observed_completed_usage':
            final['bridge']['accounting']['observed_completed_usage'],
        'third_upstream': {
            'state': third.get('state'),
            'error': third.get('error'),
            'upstream_status': third.get('upstream_status'),
            'response_bytes_positive': third.get('response_bytes', 0) > 0,
            'send_attempted': third.get('send_attempted'),
        },
        'ledger_errors': {
            'broker': final['broker']['ledger_error'],
            'bridge': final['bridge']['ledger_error'],
        },
        'reconciliation_ids_match': (
            reconciliation['missing_upstream_attempt_ids'] == [] and
            reconciliation['unexpected_upstream_attempt_ids'] == []),
        'provider_billing_complete': final['reconciliation']['provider_billing_complete'],
    }


if __name__ == '__main__':
    print(json.dumps(run(), sort_keys=True))
