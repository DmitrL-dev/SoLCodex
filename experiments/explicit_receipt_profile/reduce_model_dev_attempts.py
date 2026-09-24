"""Check the published retrospective per-attempt usage arithmetic."""

import hashlib
import json
from pathlib import Path


BASE = Path(__file__).resolve().parent


def main():
    model_bytes = (BASE / 'model_dev_result.json').read_bytes()
    model = json.loads(model_bytes)
    report = json.loads((BASE / 'model_dev_attempts.json').read_text())
    if (report['schema'] != 'solcodex.explicit-model-dev-attempts.v1'
            or report['status'] != 'retrospective_diagnostic'
            or report['model_result_sha256'] != hashlib.sha256(model_bytes).hexdigest()
            or [row['arm'] for row in report['arms']] != ['on', 'off']):
        raise ValueError('attempt report provenance mismatch')
    for group, run in zip(report['arms'], model['runs']):
        if (group['arm'] != run['arm']
                or group['trace_sha256'] != run['trace_sha256']
                or len(group['attempts']) != run['provider_attempts']):
            raise ValueError('attempt group differs from model run')
        cumulative = 0
        for sequence, row in enumerate(group['attempts'], 1):
            usage = [row[key] for key in
                     ('input_tokens', 'cached_input_tokens', 'output_tokens')]
            if (row['sequence'] != sequence
                    or any(type(value) is not int or value < 0
                           for value in usage)
                    or usage[1] > usage[0]
                    or row['total_tokens'] != usage[0] + usage[2]):
                raise ValueError('invalid attempt row')
            cumulative += row['total_tokens']
            if row['cumulative_tokens'] != cumulative:
                raise ValueError('invalid cumulative usage')
        if (cumulative != model['total_provider_tokens'][group['arm']]
                or {key: sum(row[key] for row in group['attempts']) for key in
                    ('input_tokens', 'cached_input_tokens', 'output_tokens')}
                != run['usage']):
            raise ValueError('attempts do not sum to published model usage')
    on, off = (group['attempts'] for group in report['arms'])
    expected = {
        'second_attempt_input_delta_on_minus_off':
            on[1]['input_tokens'] - off[1]['input_tokens'],
        'first_two_total_delta_on_minus_off':
            on[1]['cumulative_tokens'] - off[1]['cumulative_tokens'],
        'full_total_delta_on_minus_off':
            on[-1]['cumulative_tokens'] - off[-1]['cumulative_tokens']}
    if any(report[key] != value for key, value in expected.items()):
        raise ValueError('published attempt deltas differ from rows')
    print(json.dumps(expected, sort_keys=True))


if __name__ == '__main__':
    main()
