"""Reduce a complete exposed-task calibration without imputing missing runs.

Input is a redacted, independently checked campaign export. This reducer checks
its schedule and arithmetic; it does not authenticate raw provider or evaluator
artifacts. An incomplete campaign has spend accounting, but no treatment estimate.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = ROOT / 'docs/research/data/2026-09-25-quiet-variance-calibration-schedule.json'
SCHEMA = 'solcodex.quiet-variance-calibration-analysis-input.v4'


def strict(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def natural(value):
    return type(value) is int and value >= 0


def finite_nonnegative(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def usage_counts(value):
    if value is None:
        return None
    if (not isinstance(value, dict)
            or set(value) != {'input_tokens', 'cached_input_tokens', 'output_tokens'}
            or not all(natural(count) for count in value.values())
            or value['cached_input_tokens'] > value['input_tokens']):
        raise ValueError('invalid provider usage')
    return value


def evidence_hash(value):
    return (isinstance(value, str) and len(value) == 64
            and all(char in '0123456789abcdef' for char in value))


def accounting(row):
    state = row['usage_state']
    usage = usage_counts(row['usage'])
    known = usage_counts(row['known_upstream_usage'])
    if not evidence_hash(row['accounting_evidence_sha256']):
        raise ValueError('started slot lacks accounting evidence pin')
    if state == 'verified_complete':
        if usage is None or known != usage:
            raise ValueError('verified usage differs from known upstream usage')
    elif state == 'lower_bound':
        if usage is not None or known is None:
            raise ValueError('invalid lower bound accounting')
    elif state == 'unknown':
        if usage is not None or known is not None:
            raise ValueError('unknown accounting contains usage')
    else:
        raise ValueError('invalid started usage state')


def validate(value, schedule_raw):
    expected = strict(schedule_raw)['schedule']
    if (not isinstance(value, dict)
            or set(value) != {'schema', 'protocol_sha256', 'schedule_sha256',
                              'campaign_state',
                              'terminal_reason', 'slots'}
            or value['schema'] != SCHEMA
            or not evidence_hash(value['protocol_sha256'])
            or value['schedule_sha256'] != hashlib.sha256(schedule_raw).hexdigest()
            or not isinstance(value['slots'], list)
            or len(value['slots']) != len(expected)):
        raise ValueError('invalid campaign export or schedule pin')
    phase = 'completed'
    for row, assignment in zip(value['slots'], expected):
        if (not isinstance(row, dict)
                or set(row) != {'id', 'block', 'task', 'arm', 'status', 'quality',
                                'usage_state', 'usage', 'known_upstream_usage',
                                'accounting_evidence_sha256', 'elapsed_seconds',
                                'timed_out', 'reason'}
                or any(row[key] != assignment[key] for key in assignment)):
            raise ValueError('campaign slot differs from frozen assignment')
        status = row['status']
        if status == 'completed' and phase == 'completed':
            accounting(row)
            if (row['usage_state'] != 'verified_complete'
                    or type(row['quality']) is not bool
                    or not finite_nonnegative(row['elapsed_seconds'])
                    or type(row['timed_out']) is not bool or row['reason'] is not None):
                raise ValueError('incomplete observation in completed slot')
        elif status in ('stopped', 'unresolved') and phase == 'completed':
            phase = 'unstarted'
            accounting(row)
            if (status == 'unresolved' and row['usage_state'] == 'verified_complete'
                    or row['quality'] is not None or row['timed_out'] is not None
                    or row['elapsed_seconds'] is not None
                    and not finite_nonnegative(row['elapsed_seconds'])
                    or not isinstance(row['reason'], str) or not row['reason'].strip()):
                raise ValueError('invalid stopped slot')
        elif status == 'unstarted' and phase in ('completed', 'unstarted'):
            phase = 'unstarted'
            if (row['usage_state'] != 'not_started'
                    or any(row[key] is not None for key in
                           ('quality', 'usage', 'known_upstream_usage',
                            'accounting_evidence_sha256', 'elapsed_seconds',
                            'timed_out', 'reason'))):
                raise ValueError('unstarted slot contains observations')
        else:
            raise ValueError('campaign has a gap, restart, or multiple stops')
    statuses = [row['status'] for row in value['slots']]
    campaign_state = value['campaign_state']
    reason = value['terminal_reason']
    if campaign_state == 'complete':
        if statuses != ['completed'] * len(expected) or reason is not None:
            raise ValueError('complete campaign has missing or stopped slots')
    elif campaign_state in ('stopped', 'unresolved', 'paused'):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('incomplete campaign lacks terminal reason')
        marker = {'stopped': 'stopped', 'unresolved': 'unresolved',
                  'paused': None}[campaign_state]
        if marker is None:
            if not statuses.count('unstarted') or any(
                    status in ('stopped', 'unresolved') for status in statuses):
                raise ValueError('paused campaign has a started terminal slot')
        elif campaign_state == 'stopped' and marker not in statuses:
            if (not statuses.count('unstarted')
                    or any(status in ('stopped', 'unresolved') for status in statuses)):
                raise ValueError('prestart stop has no unstarted slots')
        elif statuses.count(marker) != 1 or any(
                status in ('stopped', 'unresolved') and status != marker
                for status in statuses):
            raise ValueError('terminal campaign state differs from slots')
        if marker in statuses and next(
                row['reason'] for row in value['slots'] if row['status'] == marker
        ) != reason:
            raise ValueError('terminal reason differs from stopped slot')
    else:
        raise ValueError('unknown campaign state')
    return value['slots']


def spread(values):
    return {'n': len(values), 'mean': statistics.mean(values),
            'sample_sd': statistics.stdev(values) if len(values) > 1 else None,
            'min': min(values), 'max': max(values)}


def totals(rows):
    return {
        'input_tokens': sum(row['usage']['input_tokens'] for row in rows),
        'cached_input_tokens': sum(row['usage']['cached_input_tokens'] for row in rows),
        'output_tokens': sum(row['usage']['output_tokens'] for row in rows),
        'input_plus_output_tokens': sum(row['usage']['input_tokens'] +
                                        row['usage']['output_tokens'] for row in rows),
    }


def known_totals(rows):
    return totals([{'usage': row['known_upstream_usage']} for row in rows
                   if row['known_upstream_usage'] is not None])


def reduce(value, schedule_raw):
    rows = validate(value, schedule_raw)
    started = [row for row in rows if row['status'] != 'unstarted']
    incomplete_usage = [row['id'] for row in started
                        if row['usage_state'] != 'verified_complete']
    complete = value['campaign_state'] == 'complete'
    result = {
        'schema': 'solcodex.quiet-variance-calibration-analysis.v4',
        'protocol_sha256': value['protocol_sha256'],
        'complete': complete,
        'started_count': len(started),
        'unstarted_ids': [row['id'] for row in rows if row['status'] == 'unstarted'],
        'stopped_ids': [row['id'] for row in rows if row['status'] == 'stopped'],
        'unresolved_ids': [row['id'] for row in rows if row['status'] == 'unresolved'],
        'campaign_state': value['campaign_state'],
        'terminal_reason': value['terminal_reason'],
        'incomplete_usage_ids': incomplete_usage,
        'all_started_usage_complete': not incomplete_usage,
        'known_started_usage': known_totals(started),
        'all_started_usage': totals(started) if not incomplete_usage else None,
        'arm_summary': None,
        'pairs': None,
        'paired_spread_by_task': None,
        'order_effects': None,
    }
    if not complete:
        return result

    arm_summary = {}
    for arm in ('verbose', 'quiet'):
        arm_rows = [row for row in rows if row['arm'] == arm]
        accepted = sum(row['quality'] for row in arm_rows)
        arm_totals = totals(arm_rows)
        arm_summary[arm] = {
            'runs': len(arm_rows), 'accepted': accepted,
            'failed_repairs': len(arm_rows) - accepted,
            'accounted_timeouts': sum(row['timed_out'] for row in arm_rows),
            'usage': arm_totals,
            'input_plus_output_per_accepted': (
                arm_totals['input_plus_output_tokens'] / accepted if accepted else None),
            'elapsed_seconds_per_accepted': (
                sum(row['elapsed_seconds'] for row in arm_rows) / accepted
                if accepted else None),
        }
    pairs = []
    for block in range(1, 5):
        for task in ('click', 'packaging'):
            pair = {row['arm']: row for row in rows
                    if row['block'] == block and row['task'] == task}
            quiet, verbose = pair['quiet'], pair['verbose']
            quiet_tokens = quiet['usage']['input_tokens'] + quiet['usage']['output_tokens']
            verbose_tokens = verbose['usage']['input_tokens'] + verbose['usage']['output_tokens']
            first = next(row['arm'] for row in rows
                         if row['block'] == block and row['task'] == task)
            pairs.append({
                'block': block, 'task': task, 'first_arm': first,
                'quiet_accepted': quiet['quality'], 'verbose_accepted': verbose['quality'],
                'acceptance': ('both' if quiet['quality'] and verbose['quality'] else
                               'quiet_only' if quiet['quality'] else
                               'verbose_only' if verbose['quality'] else 'neither'),
                'quiet_minus_verbose_tokens': quiet_tokens - verbose_tokens,
                'quiet_over_verbose_tokens': (
                    quiet_tokens / verbose_tokens if verbose_tokens else None),
                'quiet_minus_verbose_elapsed_seconds': (
                    quiet['elapsed_seconds'] - verbose['elapsed_seconds']),
                'quiet_over_verbose_elapsed_seconds': (
                    quiet['elapsed_seconds'] / verbose['elapsed_seconds']
                    if verbose['elapsed_seconds'] else None),
            })
    spread_by_task = {}
    for task in ('click', 'packaging'):
        task_pairs = [pair for pair in pairs if pair['task'] == task]
        spread_by_task[task] = {
            'token_difference': spread([pair['quiet_minus_verbose_tokens']
                                        for pair in task_pairs]),
            'token_ratio': spread([pair['quiet_over_verbose_tokens'] for pair in task_pairs
                                   if pair['quiet_over_verbose_tokens'] is not None])
            if any(pair['quiet_over_verbose_tokens'] is not None
                   for pair in task_pairs) else None,
            'elapsed_difference': spread([pair['quiet_minus_verbose_elapsed_seconds']
                                          for pair in task_pairs]),
            'elapsed_ratio': spread([pair['quiet_over_verbose_elapsed_seconds']
                                     for pair in task_pairs
                                     if pair['quiet_over_verbose_elapsed_seconds'] is not None])
            if any(pair['quiet_over_verbose_elapsed_seconds'] is not None
                   for pair in task_pairs) else None,
            'acceptance_discordance': {
                status: sum(pair['acceptance'] == status for pair in task_pairs)
                for status in ('both', 'quiet_only', 'verbose_only', 'neither')},
        }
    order = {'first_arm': {}, 'first_task': {}}
    for first_arm in ('quiet', 'verbose'):
        group = [pair for pair in pairs if pair['first_arm'] == first_arm]
        order['first_arm'][first_arm] = {
            'pairs': len(group),
            'token_difference': spread([pair['quiet_minus_verbose_tokens']
                                        for pair in group]),
            'quiet_accepted': sum(pair['quiet_accepted'] for pair in group),
            'verbose_accepted': sum(pair['verbose_accepted'] for pair in group),
        }
    for first_task in ('click', 'packaging'):
        blocks = [block for block in range(1, 5)
                  if next(row['task'] for row in rows if row['block'] == block)
                  == first_task]
        group = [pair for pair in pairs if pair['block'] in blocks]
        order['first_task'][first_task] = {
            'blocks': len(blocks),
            'pairs': len(group),
            'token_difference': spread([pair['quiet_minus_verbose_tokens']
                                        for pair in group]),
            'quiet_accepted': sum(pair['quiet_accepted'] for pair in group),
            'verbose_accepted': sum(pair['verbose_accepted'] for pair in group),
        }
    result.update(arm_summary=arm_summary, pairs=pairs,
                  paired_spread_by_task=spread_by_task, order_effects=order)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    args = parser.parse_args()
    result = reduce(strict(args.campaign.read_bytes()), SCHEDULE.read_bytes())
    print(json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False))


if __name__ == '__main__':
    main()
