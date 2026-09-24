"""Recompute the frozen no-model OFF/SHAM/ON request-byte contrasts."""

import hashlib
import json
from pathlib import Path


BASE = Path(__file__).resolve().parent


def main():
    plan_bytes = (BASE / 'sham_expectations.json').read_bytes()
    plan = json.loads(plan_bytes)
    result = json.loads((BASE / 'sham_result.json').read_text())
    if (plan['schema'] != 'solcodex.explicit-sham-plan.v1'
            or plan['model_run_authorized'] is not False
            or result['schema'] != 'solcodex.explicit-sham-result.v1'
            or result['model_usage_is_synthetic'] is not True
            or result['expectations_sha256'] != hashlib.sha256(plan_bytes).hexdigest()
            or [row['arm'] for row in result['arms']] != plan['arm_order']):
        raise ValueError('sham result provenance mismatch')
    totals = {}
    second = {}
    for row in result['arms']:
        if (row['provider_requests'] != 2 or row['provider_errors'] != 0
                or row['child_executions'] != 1
                or row['reconciliation_complete'] is not True
                or row['cleanup_verified'] is not True
                or len(row['request_body_bytes']) != 2
                or any(type(value) is not int or value <= 0
                       for value in row['request_body_bytes'])):
            raise ValueError('incomplete sham arm')
        totals[row['arm']] = sum(row['request_body_bytes'])
        second[row['arm']] = row['request_body_bytes'][1]
    contrasts = lambda values: {
        'sham_minus_off': values['sham'] - values['off'],
        'on_minus_sham': values['on'] - values['sham'],
        'on_minus_off': values['on'] - values['off']}
    if (totals != result['request_body_bytes_total']
            or second != result['request_2_bytes']
            or contrasts(totals) != result['contrasts_total_bytes']
            or contrasts(second) != result['contrasts_request_2_bytes']):
        raise ValueError('published request-byte arithmetic mismatch')
    print(json.dumps({'request_body_bytes_total': totals,
                      'contrasts_total_bytes': contrasts(totals),
                      'contrasts_request_2_bytes': contrasts(second)},
                     sort_keys=True))


if __name__ == '__main__':
    main()
