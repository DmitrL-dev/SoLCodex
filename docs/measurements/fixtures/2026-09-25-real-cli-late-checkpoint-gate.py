"""Run the frozen no-model CLI cancellation and late-accounting control."""

import hashlib
import json
from pathlib import Path
import sys

import cli_late_checkpoint_probe
import evaluate
import pilot
import runtime_manifest
from snapshot import manifest


PLAN = pilot.PUBLIC / 'docs/research/data/2026-09-25-real-cli-late-checkpoint-expectations.json'
RESULT = pilot.PUBLIC / 'docs/measurements/data/2026-09-25-real-cli-late-checkpoint-result.json'
PUBLIC_PROBE = (pilot.PUBLIC / 'docs/measurements/fixtures/'
                '2026-09-25-real-cli-late-checkpoint-probe.py')
PUBLIC_GATE = (pilot.PUBLIC / 'docs/measurements/fixtures/'
               '2026-09-25-real-cli-late-checkpoint-gate.py')
PUBLIC_AUDIT = (pilot.PUBLIC / 'docs/measurements/fixtures/'
                '2026-09-25-real-cli-first-diagnostic-audit.py')
CLI = Path('/Applications/ChatGPT.app/Contents/Resources/codex')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def preflight():
    raw = PLAN.read_bytes()
    expected = json.loads(raw)
    if (expected.get('schema') != 'solcodex.real-cli-late-checkpoint-expectations.v1'
            or expected.get('model_run_authorized') is not False
            or expected.get('case') != 'packaging_patch_cancel_late_usage'):
        raise ValueError('invalid frozen plan')
    observed = {name:digest(pilot.HERE / name)
                for name in expected['private_source_sha256']}
    if observed != expected['private_source_sha256']:
        raise ValueError('private source differs from frozen plan')
    if (digest(PUBLIC_PROBE) != observed['cli_late_checkpoint_probe.py'] or
            digest(PUBLIC_GATE) != observed['frozen_cli_late_checkpoint.py'] or
            digest(PUBLIC_AUDIT) != observed['audit.py'] or
            digest(CLI) != expected['cli_binary_sha256'] or
            sys.version.split()[0] != expected['python_version']):
        raise ValueError('published source or CLI differs from frozen plan')
    protocol = json.loads((pilot.HERE / 'selftest-protocol.json').read_text())
    if (protocol['schedule'][2] != expected['schedule_row'] or
            protocol['source_tree_sha256']['packaging'] != expected['source_tree_sha256'] or
            protocol['venv_tree_sha256'] != expected['runtime_tree_sha256'] or
            manifest(pilot.WORK / 'prehook-packaging-dev/fixture')['tree_sha256'] !=
            expected['source_tree_sha256'] or
            runtime_manifest.digest(pilot.VENV) != expected['runtime_tree_sha256'] or
            evaluate.runtime_digest(pilot.VENV) != expected['evaluator_runtime_tree_sha256']):
        raise ValueError('baseline or runtime differs from frozen plan')
    return raw


def main():
    raw = preflight()
    result = cli_late_checkpoint_probe.run()
    result.pop('private_root')
    result['schema'] = 'solcodex.real-cli-late-checkpoint-observations.v1'
    result['expectations_sha256'] = hashlib.sha256(raw).hexdigest()
    RESULT.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'result_path':str(RESULT),
                      'expectations_sha256':result['expectations_sha256']},sort_keys=True))


if __name__ == '__main__':
    main()
