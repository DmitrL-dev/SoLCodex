"""Reduce the frozen no-model Codex CLI diagnostic control.

The public reducer checks the published observations. It does not replay the
private sandbox, provider stub, checkpoint, or external evaluator.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT / 'docs/research/data/2026-09-25-real-cli-synthetic-provider-expectations.json'
RESULT = ROOT / 'docs/measurements/data/2026-09-25-real-cli-synthetic-provider-result.json'
PROBE = ROOT / 'docs/measurements/fixtures/2026-09-25-real-cli-synthetic-provider-probe.py'
GATE = ROOT / 'docs/measurements/fixtures/2026-09-25-real-cli-synthetic-provider-gate.py'
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
    if type(got) is not type(want):
        return [prefix or 'root']
    if isinstance(want, dict):
        return [path for key, value in want.items()
                for path in ([prefix + '.' + key] if key not in got else
                             compare(value, got[key], prefix + '.' + key))]
    if isinstance(want, list):
        if len(want) != len(got):
            return [prefix]
        return [path for index, (a, b) in enumerate(zip(want, got))
                for path in compare(a, b, prefix + '[' + str(index) + ']')]
    return [] if got == want else [prefix]


def reduce(expected, result, expected_raw):
    if (expected.get('schema') != 'solcodex.real-cli-synthetic-provider-expectations.v1'
            or expected.get('status') != 'prospective_no_model_development_control'
            or expected.get('model_run_authorized') is not False
            or result.get('schema') != 'solcodex.real-cli-synthetic-provider-observations.v1'
            or result.get('expectations_sha256') != hashlib.sha256(expected_raw).hexdigest()
            or hashlib.sha256(PROBE.read_bytes()).hexdigest() !=
            expected['private_source_sha256']['cli_toolcall_probe.py']
            or hashlib.sha256(GATE.read_bytes()).hexdigest() !=
            expected['private_source_sha256']['frozen_cli_probe.py']):
        raise ValueError('invalid frozen provenance')
    wanted = dict(expected['expected_observations'])
    wanted.update({'python_version':expected['python_version'],
                   'cli_binary_sha256':expected['cli_binary_sha256'],
                   'controller_sha256':expected['private_source_sha256']['cli_toolcall_probe.py'],
                   'source_tree_sha256':expected['source_tree_sha256'],
                   'runtime_tree_sha256':expected['runtime_tree_sha256']})
    mismatches = compare(wanted, result)
    for field in ('trace_sha256','quality_report_sha256','checkpoint_tree_sha256'):
        if not isinstance(result.get(field), str) or not HASH.fullmatch(result[field]):
            mismatches.append(field)
    if result.get('checkpoint_tree_sha256') != expected['source_tree_sha256']:
        mismatches.append('checkpoint_tree_sha256')
    if 'private_root' in result:
        mismatches.append('private_root')
    passed = not mismatches
    return {'schema':'solcodex.real-cli-synthetic-provider-decision.v1',
            'no_model_cli_control_pass':passed,
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
    if not decision['no_model_cli_control_pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
