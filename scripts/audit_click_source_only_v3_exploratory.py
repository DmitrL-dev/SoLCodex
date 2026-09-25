"""Recompute the public part of the exploratory Click CLI observation."""

import hashlib
import json
from pathlib import Path
import shlex


ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / 'docs/measurements/data/2026-09-25-click-source-only-v3-exploratory.json'
CANDIDATE = ROOT / 'docs/measurements/data/2026-09-25-quiet-variance-calibration-bundle-v3-candidate.json'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def confined(path):
    if not isinstance(path, str) or not path.startswith('docs/measurements/'):
        raise ValueError('unexpected public artifact path')
    target = ROOT / path
    if not target.resolve().is_relative_to(ROOT) or target.is_symlink():
        raise ValueError('artifact escaped public tree')
    return target


def trace_checks(record):
    trace = record['trace']
    raw = confined(trace['path']).read_bytes()
    case = record['case']
    events = [strict_json(line) for line in raw.splitlines()]
    shapes = [(event['type'], (event.get('item') or {}).get('type'),
               (event.get('item') or {}).get('status')) for event in events]
    expected = [
        ('thread.started', None, None), ('turn.started', None, None),
        ('item.started', 'command_execution', 'in_progress'),
        ('item.completed', 'command_execution', 'failed'),
        ('item.started', 'file_change', 'in_progress'),
        ('item.completed', 'file_change', 'completed'),
        ('turn.completed', None, None),
    ]
    started, completed = events[2]['item'], events[3]['item']
    outer = shlex.split(started['command'])
    args = shlex.split(outer[2]) if len(outer) == 3 else []
    command_ok = (outer[:2] == ['/bin/zsh', '-lc'] and len(args) == 10
                  and args[0].endswith('/tools/venv/bin/python')
                  and args[1:] == ['-m', 'pytest', 'tests/', '-q', '--tb=short',
                                   '-p', 'no:cacheprovider', '-o', 'addopts='])
    changes = [event['item']['changes'] for event in events[4:6]]
    patch_ok = (changes[0] == changes[1] and len(changes[0]) == 1
                and changes[0][0]['kind'] == 'update'
                and changes[0][0]['path'].endswith('/workspace/src/click/core.py'))
    usage = events[-1].get('usage') or {}
    return {
        'trace_hash': sha(raw) == trace['sha256'] == case['trace_sha256'],
        'normal_event_sequence': shapes == expected,
        'diagnostic_command': command_ok and started['id'] == completed['id']
            and started['command'] == completed['command']
            and completed['exit_code'] == 1
            and 'FAILED tests/test_context.py::test_resource_exit_receives_original_exception'
                in completed.get('aggregated_output', ''),
        'patch_event': patch_ok,
        'trace_usage': (usage.get('input_tokens') == case['usage']['input_tokens']
                        and usage.get('output_tokens') == case['usage']['output_tokens']
                        and usage.get('cached_input_tokens')
                            == case['usage']['cached_input_tokens']),
    }


def audit():
    record = strict_json(RECORD.read_bytes())
    candidate = strict_json(CANDIDATE.read_bytes())
    fixtures = record['fixtures']
    checks = {
        'exploratory_scope': (record['schema'] == 'solcodex.click-source-only-normal-cli-exploratory.v1'
                              and record['status'] == 'exploratory_not_qualification'
                              and record['model_requests'] == 0
                              and record['provider'] == 'local_scripted_synthetic'),
        'source_pins': (record['source_commit'] == candidate['source_commits']['click']
                        and record['baseline_tree_sha256']
                            == candidate['source_tree_sha256']['click']
                        and record['cli'] == candidate['cli']),
        'fixture_names': set(fixtures) == {'patch', 'probe', 'regressions'},
        'fixture_hashes': all(sha(confined(item['path']).read_bytes()) == item['sha256']
                              for item in fixtures.values()),
        'patch_template': (confined(fixtures['patch']['path']).read_text().startswith(
            '*** Begin Patch\n*** Update File: src/click/core.py\n')
            and record['patched_source_tree_sha256']
                == '715e83baae94b5b3271ff19de66e96e13fa85cafaeebb28a123df11f3b4f7227'),
        'reported_case_shape': (record['case']['id'] == '03-b1-click-quiet'
                                and record['case']['task'] == 'click'
                                and record['case']['arm'] == 'quiet'
                                and record['case']['exit_code'] == 0
                                and record['case']['timed_out'] is False
                                and record['case']['quality_status'] == 'pass'
                                and record['case']['technical_ok'] is True
                                and record['case']['reconciliation_complete'] is True),
    }
    checks.update(trace_checks(record))
    return {'schema': 'solcodex.click-source-only-exploratory-public-audit.v1',
            'public_trace_and_pins_consistent': all(checks.values()),
            'checks': checks,
            'private_evaluator_report_publicly_verified': False,
            'prospective_normal_matrix_qualified': False,
            'plugin_savings_established': False}


if __name__ == '__main__':
    print(json.dumps(audit(), sort_keys=True))
