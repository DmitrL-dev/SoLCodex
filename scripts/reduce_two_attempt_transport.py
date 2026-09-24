"""Recompute the synthetic two-attempt transport decision from public data."""

import argparse
import hashlib
import json
from pathlib import Path
import re

try:
    from scripts.qualify_two_attempt_transport import PRIVATE_SOURCES
    from scripts.reduce_quiet_measurement_qualification import strict_json
except ModuleNotFoundError:
    from qualify_two_attempt_transport import PRIVATE_SOURCES
    from reduce_quiet_measurement_qualification import strict_json


ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT / 'docs/research/data/2026-09-25-two-attempt-transport-expectations.json'
RESULT = ROOT / 'docs/measurements/data/2026-09-25-two-attempt-transport-result.json'
PROBE = ROOT / 'scripts/qualify_two_attempt_transport.py'
FIXTURE = ROOT / 'docs/measurements/fixtures/2026-09-25-test-live-pilot.py'
LEDGER = ROOT / 'scripts/usage_attempt_ledger.py'
HASH = re.compile(r'[0-9a-f]{64}\Z')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def tokens(usage):
    if (not isinstance(usage, dict) or
            set(usage) != {'input_tokens', 'output_tokens', 'cached_input_tokens'} or
            any(type(value) is not int or value < 0 for value in usage.values()) or
            usage['cached_input_tokens'] > usage['input_tokens']):
        raise ValueError('invalid token usage')
    return {'input_plus_output': usage['input_tokens'] + usage['output_tokens'],
            'uncached_input': usage['input_tokens'] - usage['cached_input_tokens']}


def reduce(expected, result):
    if (not isinstance(expected, dict) or
            expected.get('schema') != 'solcodex.two-attempt-transport-expectations.v1' or
            expected.get('status') != 'development_matrix' or
            expected.get('model_run_authorized') is not False or
            expected.get('wall_time_boundary') != 'cli_launch_to_upstream_closure' or
            not isinstance(expected.get('positive'), dict) or
            not isinstance(expected.get('missing_first_usage'), dict)):
        raise ValueError('invalid frozen expectations')
    if (not isinstance(result, dict) or
            result.get('schema') != 'solcodex.two-attempt-transport-probe.v1' or
            result.get('scope') != 'synthetic_no_model_transport' or
            result.get('model_run_authorized') is not False or
            result.get('wall_time_boundary') != expected['wall_time_boundary'] or
            not isinstance(result.get('source_sha256'), dict)):
        raise ValueError('invalid synthetic result')
    sources = result['source_sha256']
    if (set(sources) != set(PRIVATE_SOURCES) | {
            'qualify_two_attempt_transport.py', 'usage_attempt_ledger.py'} or
            any(not isinstance(value, str) or not HASH.fullmatch(value)
                for value in sources.values()) or
            sources['qualify_two_attempt_transport.py'] != sha(PROBE.read_bytes()) or
            sources['test_live_pilot.py'] != sha(FIXTURE.read_bytes()) or
            sources['usage_attempt_ledger.py'] != sha(LEDGER.read_bytes())):
        raise ValueError('probe source does not match public pinned code')
    positive = result.get('positive')
    negative = result.get('missing_first_usage')
    if not isinstance(positive, dict) or not isinstance(negative, dict):
        raise ValueError('missing synthetic cases')
    positive_totals = tokens(positive.get('usage'))
    partial_totals = tokens(negative.get('observed_subtotal'))
    positive_match = positive == expected['positive']
    negative_match = negative == expected['missing_first_usage']
    return {'schema': 'solcodex.two-attempt-transport-decision.v1',
            'synthetic_transport_probe_pass': positive_match and negative_match,
            'positive_matches': positive_match,
            'missing_usage_matches': negative_match,
            'positive_input_plus_output': positive_totals['input_plus_output'],
            'positive_uncached_input': positive_totals['uncached_input'],
            'partial_subtotal_input_plus_output': partial_totals['input_plus_output'],
            'partial_subtotal_uncached_input': partial_totals['uncached_input'],
            'model_run_authorized': False,
            'scope': 'synthetic_no_model_transport'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected', type=Path, default=EXPECTED)
    parser.add_argument('--result', type=Path, default=RESULT)
    args = parser.parse_args()
    expected_bytes, result_bytes = args.expected.read_bytes(), args.result.read_bytes()
    decision = reduce(strict_json(expected_bytes), strict_json(result_bytes))
    decision.update(expected_sha256=sha(expected_bytes), result_sha256=sha(result_bytes))
    print(json.dumps(decision, sort_keys=True, indent=2))
    if not decision['synthetic_transport_probe_pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
