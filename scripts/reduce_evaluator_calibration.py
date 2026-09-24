"""Compare a no-model evaluator calibration with its published frozen expectations.

The private evaluator cannot be replayed from this repository. This checks only
the sanitized observations and never upgrades a failed development calibration.
"""

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXPECTED = ROOT / 'docs/research/data/2026-09-25-evaluator-calibration-expectations.json'
DEFAULT_RESULT = ROOT / 'docs/measurements/data/2026-09-25-evaluator-calibration-first-result.json'


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


def reduce(expected, observed, expected_raw):
    version = expected.get('schema', '').rsplit('.', 1)[-1]
    if (version not in ('v1', 'v2') or
            expected.get('schema') != 'solcodex.evaluator-calibration-expectations.' + version or
            expected.get('status') != 'exposed_development_no_model' or
            expected.get('model_run_authorized') is not False or
            observed.get('schema') != 'solcodex.evaluator-calibration-observations.' + version or
            observed.get('scope') != expected['status'] or
            observed.get('expected_sha256') != hashlib.sha256(expected_raw).hexdigest() or
            observed.get('source_sha256') != expected['source_sha256']):
        raise ValueError('invalid calibration provenance')
    rows = observed.get('observations')
    if not isinstance(rows, list) or len(rows) != 7:
        raise ValueError('expected seven calibration controls')
    by_name = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('control'), str):
            raise ValueError('invalid control row')
        if row['control'] in by_name:
            raise ValueError('duplicate control')
        by_name[row['control']] = row
    names = {task + '_' + variant for task in ('click', 'packaging')
             for variant in ('parent', 'gold', 'wrong')} | {'import_time_spoof'}
    if set(by_name) != names:
        raise ValueError('control set differs from frozen plan')
    mismatches = {}
    required = expected['required_for_all_controls']
    for name, row in sorted(by_name.items()):
        if name == 'import_time_spoof':
            want = expected['expected'][name]
            tree = expected['control_tree_sha256'][name]
        else:
            task, variant = name.split('_', 1)
            want = expected['expected'][task][variant]
            tree = expected['control_tree_sha256'][task][variant]
        differences = []
        for key in ('status', 'quality', 'case_pass_vector'):
            if row.get(key) != want[key]:
                differences.append(key)
        if row.get('candidate_tree_sha256') != tree:
            differences.append('candidate_tree_sha256')
        if row.get('canaries') != dict.fromkeys(
                ('source_write', 'host_read', 'host_write', 'network'),
                required['isolation_canaries']):
            differences.append('canaries')
        if row.get('source_unchanged') is not required['source_unchanged']:
            differences.append('source_unchanged')
        expected_exit = (required['upstream_exit_code'] if version == 'v1'
                         else want['upstream_exit'])
        if type(row.get('upstream_exit')) is not int or row['upstream_exit'] != expected_exit:
            differences.append('upstream_exit')
        if name == 'import_time_spoof':
            if row.get('upstream_wire_error') is not want['upstream_wire_error']:
                differences.append('upstream_wire_error')
        else:
            if row.get('upstream_valid') is not True:
                differences.append('upstream_valid')
            if row.get('upstream_passed') is not want['upstream_passed']:
                differences.append('upstream_passed')
            if (not isinstance(row.get('case_pass_vector'), list) or
                    len(row['case_pass_vector']) != len(expected['expected'][task]['case_order'])):
                differences.append('case_count')
            if version == 'v2':
                for key in ('upstream_inventory_sha256', 'upstream_failed_call_ids_sha256',
                            'upstream_failed_call_count', 'upstream_unexpected_skips'):
                    if row.get(key) != want[key]:
                        differences.append(key)
                if type(row.get('upstream_failed_call_count')) is not int:
                    differences.append('upstream_failed_call_count_type')
        if differences:
            mismatches[name] = differences
    passed = not mismatches
    if observed.get('frozen_acceptance_pass') is not passed:
        raise ValueError('reported acceptance differs from recomputation')
    return {'schema': 'solcodex.evaluator-calibration-decision.v1',
            'development_calibration_pass': passed,
            'controls': len(rows), 'matching_controls': len(rows) - len(mismatches),
            'mismatches': mismatches, 'model_run_authorized': False,
            'full_measurement_path_qualified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected', type=Path, default=DEFAULT_EXPECTED)
    parser.add_argument('--result', type=Path, default=DEFAULT_RESULT)
    args = parser.parse_args()
    expected_raw = args.expected.read_bytes()
    decision = reduce(strict_json(expected_raw), strict_json(args.result.read_bytes()),
                      expected_raw)
    print(json.dumps(decision, sort_keys=True, indent=2))
    if not decision['development_calibration_pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
