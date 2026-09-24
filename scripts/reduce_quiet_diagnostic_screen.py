"""Recompute the frozen quiet-diagnostic screen from published run rows."""

import argparse
import hashlib
import json
import math
from pathlib import Path


def read_json(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f'duplicate key: {key}')
            value[key] = item
        return value
    return json.loads(path.read_bytes(), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def reduce_rows(protocol, result):
    assignment = protocol['schedule']
    rows = result['runs']
    expected = {(r['task'], r['arm']): r['id'] for r in assignment}
    actual = {(r['task'], r['arm']): r['id'] for r in rows}
    complete = len(rows) == 4 and len(actual) == 4 and actual == expected
    indexed = {(r['task'], r['arm']): r for r in rows}
    counts, times = {}, {}
    for key, row in indexed.items():
        usage = row.get('usage')
        if isinstance(usage, dict) and row.get('reconciliation_complete') is True:
            fields = [usage.get(name) for name in
                      ('input_tokens', 'output_tokens', 'cached_input_tokens')]
            if all(type(n) is int and n >= 0 for n in fields) and fields[2] <= fields[0]:
                counts[key] = fields[0] + fields[1]
        seconds = row.get('elapsed_seconds')
        if type(seconds) in (int, float) and math.isfinite(seconds) and seconds > 0:
            times[key] = seconds
    usage_complete = complete and len(counts) == 4
    time_complete = complete and len(times) == 4
    tasks = ('click', 'packaging')
    on_tokens = sum(counts.get((task, 'quiet'), 0) for task in tasks)
    off_tokens = sum(counts.get((task, 'verbose'), 0) for task in tasks)
    on_seconds = sum(times.get((task, 'quiet'), 0) for task in tasks)
    off_seconds = sum(times.get((task, 'verbose'), 0) for task in tasks)
    token_ratio = on_tokens / off_tokens if usage_complete and off_tokens > 0 else None
    time_ratio = on_seconds / off_seconds if time_complete and off_seconds > 0 else None
    limits = protocol['thresholds']
    gates = {
        'four_assigned_runs': complete,
        'all_technical_ok': complete and all(r.get('technical_ok') is True for r in rows),
        'all_cli_success': complete and all(r.get('exit_code') == 0 and
                                            r.get('timed_out') is False for r in rows),
        'all_four_quality_pass': complete and all(r.get('quality_status') == 'pass' and
                                                 r.get('quality') is True for r in rows),
        'all_four_first_commands_verified': complete and all(
            r.get('first_command_frozen', {}).get('first_command_exact') is True for r in rows),
        'all_usage_observed': usage_complete,
        'max_total_token_ratio': token_ratio is not None and
                                 token_ratio <= limits['max_total_token_ratio'],
        'on_no_more_tokens_in_each_task': usage_complete and all(
            counts[(task, 'quiet')] <= counts[(task, 'verbose')] for task in tasks),
        'max_wall_time_ratio': time_ratio is not None and
                               time_ratio <= limits['max_wall_time_ratio'],
    }
    return {'pass': all(gates.values()), 'gates': gates,
            'token_ratio': token_ratio, 'wall_time_ratio': time_ratio,
            'on_tokens': on_tokens if usage_complete else None,
            'off_tokens': off_tokens if usage_complete else None,
            'on_seconds': on_seconds if time_complete else None,
            'off_seconds': off_seconds if time_complete else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    protocol_raw = args.protocol.read_bytes()
    protocol, result = read_json(args.protocol), read_json(args.result)
    if result.get('schema') != 'solcodex.quiet-model-screen-result-public.v1':
        raise ValueError('unexpected result schema')
    if result.get('protocol_sha256') != hashlib.sha256(protocol_raw).hexdigest():
        raise ValueError('protocol hash mismatch')
    if result.get('provider_billing_complete') is not False:
        raise ValueError('billing status changed')
    reduced = reduce_rows(protocol, result)
    if reduced != result.get('screen'):
        raise ValueError('published screen differs from recomputed result')
    print(json.dumps(reduced, sort_keys=True))


if __name__ == '__main__':
    main()
