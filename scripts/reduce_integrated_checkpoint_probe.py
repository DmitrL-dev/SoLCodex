"""Reduce four frozen no-model checkpoint/transport/evaluator observations.

The private host execution is not replayed by this reducer. A passing decision
qualifies only the named synthetic development cases.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT/'docs/research/data/2026-09-25-integrated-checkpoint-expectations.json'
RESULT = ROOT/'docs/measurements/data/2026-09-25-integrated-checkpoint-result.json'
FIXTURE = ROOT/'docs/measurements/fixtures/2026-09-25-integrated-checkpoint-probe.py'
HASH = re.compile(r'[0-9a-f]{64}\Z')


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def compare(want, got, prefix=''):
    """Exact types and values for declared fields; extra evidence is retained."""
    if type(got) is not type(want):
        return [prefix or 'root']
    if isinstance(want, dict):
        failures = []
        for key, value in want.items():
            path = prefix + '.' + key if prefix else key
            failures.extend([path] if key not in got else compare(value, got[key], path))
        return failures
    if isinstance(want, list):
        if len(want) != len(got):
            return [prefix]
        return [path for index, (a, b) in enumerate(zip(want, got))
                for path in compare(a, b, prefix + '[' + str(index) + ']')]
    return [] if got == want else [prefix]


def reduce(expected, result, expected_raw, *, require_declared=True):
    if (expected.get('schema') != 'solcodex.integrated-checkpoint-expectations.v1' or
            expected.get('status') != 'synthetic_development_no_model' or
            expected.get('model_run_authorized') is not False or
            result.get('schema') != 'solcodex.integrated-checkpoint-observations.v1' or
            result.get('scope') != expected['status'] or
            result.get('expected_sha256') != hashlib.sha256(expected_raw).hexdigest() or
            result.get('source_sha256') != expected['source_sha256'] or
            result.get('observed_runtime') != expected['runtime'] or
            hashlib.sha256(FIXTURE.read_bytes()).hexdigest() !=
            expected['source_sha256']['integrated_checkpoint_probe.py'] or
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest() !=
            expected['source_sha256']['reducer']):
        raise ValueError('invalid frozen probe provenance')
    rows = result.get('cases')
    if not isinstance(rows, list) or len(rows) != 4:
        raise ValueError('four ordered cases required')
    if [row.get('case') for row in rows] != expected['case_order']:
        raise ValueError('case order differs from frozen plan')
    mismatches = {}
    for row in rows:
        name = row['case']
        failures = compare(expected['common'], row)
        failures += compare(expected['cases'][name], row)
        for field in ('diagnostic_stdout_sha256', 'diagnostic_stderr_sha256',
                      'private_final_sha256'):
            value = row.get(field)
            if not isinstance(value, str) or not HASH.fullmatch(value):
                failures.append(field)
        quality_hash = row.get('private_quality_report_sha256')
        if name == 'gold_cleanup_failure':
            if quality_hash is not None:
                failures.append('private_quality_report_sha256')
        elif not isinstance(quality_hash, str) or not HASH.fullmatch(quality_hash):
            failures.append('private_quality_report_sha256')
        stdout_bytes, stderr_bytes = (row.get(field) for field in
                                      ('diagnostic_stdout_bytes','diagnostic_stderr_bytes'))
        if (type(stdout_bytes) is not int or not 0 < stdout_bytes < 1048576 or
                type(stderr_bytes) is not int or not 0 <= stderr_bytes < 1048576):
            failures.append('diagnostic_output_bound')
        if failures:
            mismatches[name] = sorted(set(failures))
    passed = not mismatches
    if require_declared and result.get('frozen_acceptance_pass') is not passed:
        raise ValueError('reported decision differs from recomputation')
    return {'schema':'solcodex.integrated-checkpoint-decision.v1',
            'synthetic_integration_pass':passed,
            'matching_cases':4-len(mismatches),'cases':4,'mismatches':mismatches,
            'model_run_authorized':False,'plugin_savings_established':False,
            'full_measurement_path_qualified':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected',type=Path,default=EXPECTED)
    parser.add_argument('--result',type=Path,default=RESULT)
    args=parser.parse_args()
    raw=args.expected.read_bytes()
    decision=reduce(strict_json(raw),strict_json(args.result.read_bytes()),raw)
    print(json.dumps(decision,sort_keys=True,indent=2))
    if not decision['synthetic_integration_pass']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
