"""Post-run publication audit for the adaptive evaluator calibration v2.

This audit was written after the model-free evaluator run. It verifies public
records and the frozen reducer; it cannot replay private host execution.
"""

import hashlib
import importlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT / 'docs/research/data/2026-09-25-evaluator-calibration-expectations-v2.json'
RESULT = ROOT / 'docs/measurements/data/2026-09-25-evaluator-calibration-v2-result.json'
REDUCER = Path(__file__).resolve().with_name('reduce_evaluator_calibration.py')
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


def audit(expected, result, expected_raw):
    if (expected.get('schema') != 'solcodex.evaluator-calibration-expectations.v2' or
            result.get('schema') != 'solcodex.evaluator-calibration-observations.v2' or
            result.get('expected_sha256') != hashlib.sha256(expected_raw).hexdigest()):
        raise ValueError('wrong calibration revision')
    reducer_hash = hashlib.sha256(REDUCER.read_bytes()).hexdigest()
    if reducer_hash != expected['source_sha256']['reducer']:
        raise ValueError('frozen reducer source changed')
    frozen = importlib.import_module('reduce_evaluator_calibration')
    runtime = result.get('observed_runtime')
    if (not isinstance(runtime, dict) or
            runtime.get('host_python_version', '').split()[0] !=
            expected['runtime']['host_python'] or
            runtime.get('evaluator_assets_lock_sha256') !=
            expected['source_sha256']['assets_lock'] or
            runtime.get('evaluator_source_sha256') !=
            expected['source_sha256']['evaluator']):
        raise ValueError('observed host runtime or evaluator pins differ')
    if result.get('cleanup_handle_unit_checks') != expected['cleanup_checks']:
        raise ValueError('cleanup unit-check observations differ')
    rows = result.get('observations')
    if not isinstance(rows, list) or len(rows) != 7:
        raise ValueError('wrong control count')
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid control row')
        vector = row.get('case_pass_vector')
        if not isinstance(vector, list):
            raise ValueError('invalid behavior vector')
        if row.get('control') == 'import_time_spoof':
            if row.get('quality') is not None or any(value is not None for value in vector):
                raise ValueError('spoof yielded accepted behavior')
        else:
            if (type(row.get('quality')) is not bool or
                    any(type(value) is not bool for value in vector) or
                    type(row.get('behavior_passed')) is not int or
                    type(row.get('behavior_total')) is not int or
                    row['behavior_passed'] != sum(vector) or
                    row['behavior_total'] != len(vector) or
                    type(row.get('upstream_passed')) is not bool or
                    type(row.get('upstream_failed_call_count')) is not int or
                    row.get('upstream_unexpected_skips') != []):
                raise ValueError('invalid behavior or upstream observation')
        canaries = row.get('canaries')
        if (not isinstance(canaries, dict) or
                set(canaries) != {'source_write', 'host_read', 'host_write', 'network'} or
                any(type(value) is not bool for value in canaries.values())):
            raise ValueError('invalid isolation canaries')
        report_hash = row.get('private_evaluator_report_sha256')
        if not isinstance(report_hash, str) or not HASH.fullmatch(report_hash):
            raise ValueError('invalid private report hash')
    decision = frozen.reduce(expected, result, expected_raw)
    return {'schema': 'solcodex.evaluator-calibration-publication-audit.v1',
            'post_run_audit': True,
            'publication_audit_pass': decision['development_calibration_pass'],
            'controls': decision['controls'],
            'full_measurement_path_qualified': False,
            'model_run_authorized': False}


def main():
    expected_raw = EXPECTED.read_bytes()
    result = audit(strict_json(expected_raw),
                   strict_json(RESULT.read_bytes()), expected_raw)
    print(json.dumps(result, sort_keys=True, indent=2))
    if not result['publication_audit_pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
