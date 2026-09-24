"""Reduce the frozen no-model real-CLI cancellation and late-usage control."""

import argparse
import hashlib
import json
from pathlib import Path

from reduce_real_cli_synthetic_provider_probe import HASH, compare, strict_json


ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT / 'docs/research/data/2026-09-25-real-cli-late-checkpoint-expectations.json'
RESULT = ROOT / 'docs/measurements/data/2026-09-25-real-cli-late-checkpoint-result.json'
PROBE = ROOT / 'docs/measurements/fixtures/2026-09-25-real-cli-late-checkpoint-probe.py'
GATE = ROOT / 'docs/measurements/fixtures/2026-09-25-real-cli-late-checkpoint-gate.py'
AUDIT = ROOT / 'docs/measurements/fixtures/2026-09-25-real-cli-first-diagnostic-audit.py'


def reduce(expected, result, expected_raw):
    if (expected.get('schema') != 'solcodex.real-cli-late-checkpoint-expectations.v1'
            or expected.get('status') != 'prospective_no_model_development_control'
            or expected.get('model_run_authorized') is not False
            or result.get('schema') != 'solcodex.real-cli-late-checkpoint-observations.v1'
            or result.get('expectations_sha256') != hashlib.sha256(expected_raw).hexdigest()
            or hashlib.sha256(PROBE.read_bytes()).hexdigest() !=
            expected['private_source_sha256']['cli_late_checkpoint_probe.py']
            or hashlib.sha256(GATE.read_bytes()).hexdigest() !=
            expected['private_source_sha256']['frozen_cli_late_checkpoint.py']
            or hashlib.sha256(AUDIT.read_bytes()).hexdigest() !=
            expected['private_source_sha256']['audit.py']):
        raise ValueError('invalid frozen provenance')
    mismatches = compare(expected['expected_observations'], result)
    for field in ('trace_sha256','quality_report_sha256'):
        if not isinstance(result.get(field), str) or not HASH.fullmatch(result[field]):
            mismatches.append(field)
    if 'private_root' in result:
        mismatches.append('private_root')
    if result.get('checkpoint_tree_sha256') != expected['gold_tree_sha256']:
        mismatches.append('checkpoint_tree_sha256')
    passed = not mismatches
    return {'schema':'solcodex.real-cli-late-checkpoint-decision.v1',
            'no_model_late_checkpoint_pass':passed,
            'mismatches':sorted(set(mismatches)),
            'model_run_authorized':False,
            'full_measurement_path_qualified':False,
            'plugin_savings_established':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected', type=Path, default=EXPECTED)
    parser.add_argument('--result', type=Path, default=RESULT)
    args = parser.parse_args()
    raw = args.expected.read_bytes()
    decision = reduce(strict_json(raw), strict_json(args.result.read_bytes()), raw)
    print(json.dumps(decision, sort_keys=True, indent=2))
    if not decision['no_model_late_checkpoint_pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
