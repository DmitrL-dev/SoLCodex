"""Recompute the public late-missing-usage no-model control decision."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/research/data/2026-09-25-real-cli-late-missing-usage-expectations.json'
RESULT = ROOT / 'docs/measurements/data/2026-09-25-real-cli-late-missing-usage-result.json'
PROBE = ROOT / 'docs/measurements/fixtures/2026-09-25-real-cli-late-missing-usage-probe.py'
GATE = ROOT / 'docs/measurements/fixtures/2026-09-25-real-cli-late-missing-usage-gate.py'
LEDGER = ROOT / 'scripts/usage_attempt_ledger.py'
META = {'expectations_sha256', 'private_source_sha256', 'public_head',
        'predeclared_match', 'mismatched_fields'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reduce():
    plan = json.loads(PLAN.read_text())
    result = json.loads(RESULT.read_text())
    expected = plan['expected_observations']
    pins = plan['private_source_sha256']
    mismatched = sorted(key for key, value in expected.items()
                         if result.get(key) != value)
    if set(result) != set(expected) | META:
        mismatched.append('observation_key_set')
    provenance = (
        result.get('expectations_sha256') == sha(PLAN)
        and result.get('private_source_sha256') == pins
        and sha(PROBE) == pins['cli_late_missing_usage_probe.py']
        and sha(GATE) == pins['frozen_cli_late_missing_usage.py']
        and sha(LEDGER) == plan['usage_ledger_sha256']
        and isinstance(result.get('public_head'), str)
        and len(result['public_head']) == 40
    )
    decision = (not mismatched and provenance
                and result.get('predeclared_match') is True
                and result.get('mismatched_fields') == [])
    return {'schema': 'solcodex.real-cli-late-missing-usage-decision.v1',
            'no_model_negative_control_pass': decision,
            'mismatched_fields': mismatched,
            'provenance_consistent': provenance,
            'full_measurement_path_qualified': False,
            'plugin_savings_established': False}


if __name__ == '__main__':
    print(json.dumps(reduce(), sort_keys=True))
