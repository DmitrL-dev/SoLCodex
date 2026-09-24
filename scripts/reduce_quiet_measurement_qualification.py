"""Recompute the direct no-model qualification decision from public records.

The private harness is not reproduced by this reducer. A matching direct
probe does not authorize a model run or qualify the whole measurement path.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT / 'docs/research/data/2026-09-25-quiet-measurement-qualification-expectations.json'
RESULT = ROOT / 'docs/measurements/data/2026-09-25-quiet-measurement-unit-probe-result.json'
PROBE = ROOT / 'scripts/qualification_probe.py'
HASH = re.compile(r'[0-9a-f]{64}\Z')
SOURCES = {'audit.py', 'evaluate.py', 'live_pilot.py', 'qualification_probe.py'}


def strict_json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON key')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def reduce(expected, result):
    if (not isinstance(expected, dict) or
            expected.get('schema') != 'solcodex.quiet-measurement-qualification-expectations.v1' or
            expected.get('status') != 'development_matrix' or
            expected.get('model_run_authorized') is not False or
            not isinstance(expected.get('trace_status'), dict) or
            not isinstance(expected.get('ledger_complete'), dict)):
        raise ValueError('invalid expectation matrix')
    if (not isinstance(result, dict) or
            result.get('schema') != 'solcodex.quiet-measurement-unit-probe.v1' or
            result.get('scope') != 'direct_no_model_probes_only' or
            not isinstance(result.get('source_sha256'), dict) or
            set(result['source_sha256']) != SOURCES or
            any(not isinstance(value, str) or not HASH.fullmatch(value)
                for value in result['source_sha256'].values()) or
            result['source_sha256']['qualification_probe.py'] !=
            hashlib.sha256(PROBE.read_bytes()).hexdigest()):
        raise ValueError('invalid probe provenance')
    observed = result.get('observations')
    if not isinstance(observed, dict) or set(observed) != {'traces', 'ledgers', 'fake_success'}:
        raise ValueError('invalid probe observations')
    traces, ledgers = observed['traces'], observed['ledgers']
    if (not isinstance(traces, dict) or not isinstance(ledgers, dict) or
            set(traces) != set(expected['trace_status']) or
            set(ledgers) != set(expected['ledger_complete'])):
        raise ValueError('probe cases differ from frozen expectations')
    trace_matches = {}
    for name, expected_status in expected['trace_status'].items():
        row = traces[name]
        if (expected_status not in ('observed', 'violated', 'unknown') or
                not isinstance(row, dict) or
                type(row.get('first_command_exact')) is not bool):
            raise ValueError('invalid trace classification')
        trace_matches[name] = (row.get('status') == expected_status and
                               row['first_command_exact'] is (expected_status == 'observed'))
    ledger_matches = {}
    for name, expected_complete in expected['ledger_complete'].items():
        row = ledgers[name]
        if type(expected_complete) is not bool or not isinstance(row, dict):
            raise ValueError('invalid ledger classification')
        usage = row.get('usage')
        usage_valid = (isinstance(usage, dict) and
                       set(usage) == {'input_tokens', 'output_tokens', 'cached_input_tokens'} and
                       all(type(value) is int and value >= 0 for value in usage.values()) and
                       usage['cached_input_tokens'] <= usage['input_tokens'])
        ledger_matches[name] = (row.get('complete') is expected_complete and
                                (usage_valid if expected_complete else usage is None) and
                                row.get('provider_billing_complete') is False)
    fake = observed['fake_success']
    fake_matches = (isinstance(fake, dict) and
                    expected.get('fake_success_accepted') is False and
                    fake.get('accepted') is False)
    return {'schema': 'solcodex.quiet-measurement-unit-probe-decision.v1',
            'direct_probe_pass': all(trace_matches.values()) and
                                 all(ledger_matches.values()) and fake_matches,
            'trace_cases': len(trace_matches), 'trace_matches': sum(trace_matches.values()),
            'ledger_cases': len(ledger_matches), 'ledger_matches': sum(ledger_matches.values()),
            'fake_success_matches': fake_matches,
            'trace_mismatches': sorted(key for key, ok in trace_matches.items() if not ok),
            'ledger_mismatches': sorted(key for key, ok in ledger_matches.items() if not ok),
            'model_run_authorized': False,
            'scope': 'direct_no_model_probes_only'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected', type=Path, default=EXPECTED)
    parser.add_argument('--result', type=Path, default=RESULT)
    args = parser.parse_args()
    expected_bytes, result_bytes = args.expected.read_bytes(), args.result.read_bytes()
    decision = reduce(strict_json(expected_bytes), strict_json(result_bytes))
    decision.update(expected_sha256=hashlib.sha256(expected_bytes).hexdigest(),
                    result_sha256=hashlib.sha256(result_bytes).hexdigest())
    print(json.dumps(decision, sort_keys=True, indent=2))
    if not decision['direct_probe_pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
