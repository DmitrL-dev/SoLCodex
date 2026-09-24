"""Export usage per provider attempt from retained private development ledgers.

This is a retrospective diagnostic. It does not change the frozen model-pair
result or turn one exposed pair into confirmatory evidence.
"""

import argparse
import hashlib
import json
from pathlib import Path


BASE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export(root):
    published_path = BASE / 'model_dev_result.json'
    published = json.loads(published_path.read_text())
    if (published['schema'] != 'solcodex.explicit-model-dev-result.v1'
            or [row['arm'] for row in published['runs']] != ['on', 'off']):
        raise ValueError('unexpected published pair')
    arms = []
    for public_row in published['runs']:
        arm = public_row['arm']
        host = root / public_row['id'] / 'host-artifacts'
        final_path = host / 'final.json'
        final = json.loads(final_path.read_text())
        if (final['id'] != public_row['id'] or final['arm'] != arm
                or final['trace_sha256'] != public_row['trace_sha256']
                or digest(host / 'trace.jsonl') != public_row['trace_sha256']
                or final['usage'] != public_row['usage']
                or final['reconciliation']['complete'] is not True):
            raise ValueError('private final differs from published run')
        delivery = final['broker']['requests']
        upstream = final['bridge']['attempts']
        identifiers = [row['attempt_id'] for row in delivery]
        if (len(set(identifiers)) != len(identifiers)
                or set(identifiers) != set(upstream)
                or len(identifiers) != public_row['provider_attempts']):
            raise ValueError('attempt IDs do not reconcile')
        cumulative = 0
        rows = []
        for sequence, (delivered, identifier) in enumerate(
                zip(delivery, identifiers), 1):
            observed = upstream[identifier]
            usage = {key: observed[key] for key in
                     ('input_tokens', 'cached_input_tokens', 'output_tokens')}
            if (delivered['attempt'] != sequence
                    or delivered['usage'] != usage
                    or delivered['completed'] is not True
                    or delivered['forwarded'] is not True
                    or delivered['status'] != 200
                    or observed['state'] != 'completed'
                    or observed['send_attempted'] is not True
                    or observed['upstream_status'] != 200
                    or observed['delivery']['state'] != 'stream_finished'
                    or observed['delivery']['error'] is not None
                    or any(type(n) is not int or n < 0
                           for n in usage.values())
                    or usage['cached_input_tokens'] > usage['input_tokens']):
                raise ValueError('incomplete or invalid provider attempt')
            total = usage['input_tokens'] + usage['output_tokens']
            cumulative += total
            rows.append({'sequence': sequence, **usage,
                         'total_tokens': total,
                         'cumulative_tokens': cumulative})
        if (cumulative != published['total_provider_tokens'][arm]
                or {key: sum(row[key] for row in rows) for key in
                    ('input_tokens', 'cached_input_tokens', 'output_tokens')}
                != public_row['usage']):
            raise ValueError('attempt usage does not sum to published total')
        arms.append({'arm': arm, 'private_final_sha256': digest(final_path),
                     'trace_sha256': public_row['trace_sha256'],
                     'attempts': rows})
    on, off = (row['attempts'] for row in arms)
    return {'schema': 'solcodex.explicit-model-dev-attempts.v1',
            'status': 'retrospective_diagnostic',
            'model_result_sha256': digest(published_path),
            'arms': arms,
            'second_attempt_input_delta_on_minus_off':
                on[1]['input_tokens'] - off[1]['input_tokens'],
            'first_two_total_delta_on_minus_off':
                on[1]['cumulative_tokens'] - off[1]['cumulative_tokens'],
            'full_total_delta_on_minus_off':
                on[-1]['cumulative_tokens'] - off[-1]['cumulative_tokens']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-root', required=True, type=Path)
    args = parser.parse_args()
    result = export(args.private_root)
    output = BASE / 'model_dev_attempts.json'
    output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'attempts': [len(row['attempts']) for row in result['arms']],
                      'second_attempt_input_delta_on_minus_off':
                          result['second_attempt_input_delta_on_minus_off'],
                      'full_total_delta_on_minus_off':
                          result['full_total_delta_on_minus_off']}, sort_keys=True))


if __name__ == '__main__':
    main()
