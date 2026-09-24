"""Recompute the exposed model pair's aggregate from published run rows."""

import hashlib
import json
from pathlib import Path


BASE = Path(__file__).resolve().parent


def main():
    plan_bytes = (BASE / 'model_dev_plan.json').read_bytes()
    plan = json.loads(plan_bytes)
    result = json.loads((BASE / 'model_dev_result.json').read_text())
    if (plan['schema'] != 'solcodex.explicit-model-dev-plan.v1'
            or result['schema'] != 'solcodex.explicit-model-dev-result.v1'
            or result['plan_sha256'] != hashlib.sha256(plan_bytes).hexdigest()
            or result['provider_billing_complete'] is not False
            or result['unstarted_arms']):
        raise ValueError('plan or result provenance is incomplete')
    rows = result['runs']
    if [row['arm'] for row in rows] != plan['arm_order']:
        raise ValueError('assigned arms missing or reordered')
    tokens = {}
    for row in rows:
        if (row['technical_ok'] is not True
                or row['reconciliation_complete'] is not True
                or row['broker_stopped'] is not True
                or row['bridge_stopped'] is not True
                or row['provider_attempts'] != row['delivery_attempts']
                or row['provider_attempts'] <= 0
                or row['provider_states'] != {
                    'completed': row['provider_attempts'],
                    'pending': 0, 'unknown': 0}):
            raise ValueError('incomplete run accounting')
        usage = row['usage']
        if (set(usage) != {'input_tokens', 'output_tokens',
                           'cached_input_tokens'}
                or any(type(value) is not int or value < 0
                       for value in usage.values())
                or usage['cached_input_tokens'] > usage['input_tokens']):
            raise ValueError('invalid provider usage')
        tokens[row['arm']] = usage['input_tokens'] + usage['output_tokens']
    both_quality_pass = all(row['quality_status'] == 'pass'
                            and row['quality'] is True for row in rows)
    delta = tokens['on'] - tokens['off']
    if (tokens != result['total_provider_tokens']
            or delta != result['token_delta_on_minus_off']
            or both_quality_pass != result['both_quality_pass']):
        raise ValueError('published aggregate differs from run rows')
    print(json.dumps({'both_quality_pass': both_quality_pass,
                      'provider_tokens': tokens,
                      'token_delta_on_minus_off': delta,
                      'ratio_on_over_off': tokens['on'] / tokens['off'],
                      'first_action_audit': [row['first_command']['status']
                                             for row in rows]}, sort_keys=True))


if __name__ == '__main__':
    main()
