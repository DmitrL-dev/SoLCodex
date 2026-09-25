"""Independently verify recorded v4 control evidence from a compact bundle.

This checker uses only Python's standard library. It never imports the campaign
coordinator, exporter, evaluator, or ledger implementation. Passing means the
recorded bytes are internally consistent, not that campaign_path is qualified.
"""

import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import tempfile


USAGE = ('input_tokens', 'cached_input_tokens', 'output_tokens')
REQUIRED_HOST = {'final.json', 'result.json', 'trace.jsonl', 'stderr.txt',
                 'delivery.sqlite3', 'upstream.sqlite3', 'baseline.json'}
PACKAGING_CASES = dict(zip(
    ['nested-single', 'nested-whitespace', 'nested-and-or', 'nested-with',
     'nested-ref', 'metadata-nested', 'simple', 'single-parens',
     *(f'invalid-{index}' for index in range(1, 7))],
    ['((MIT))', '((MIT))', '((MIT AND (Apache-2.0 OR BSD-2-Clause)))',
     '((GPL-2.0-only WITH Classpath-exception-2.0))',
     '((LicenseRef-Custom))', '((MIT))', 'MIT', '(MIT)',
     *(['rejected'] * 6)]))
CLICK_CASES = {name: True for name in (
    'direct_exception', 'scope_exception', 'nested_inner_suppresses',
    'nested_outer_suppresses', 'nested_unsuppressed',
    'cli_transaction_rollback', 'successful_exit', 'explicit_close',
    'context_reuse')}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def document(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON key: ' + key)
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(
                          ValueError('nonfinite JSON number')))


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def bytes_at(root, relative):
    path = root / relative
    require(path.is_file() and not path.is_symlink(),
            'missing or symlinked evidence: ' + relative)
    return path.read_bytes()


def manifest_closure(root):
    manifest_raw = bytes_at(root, 'manifest.json')
    value = document(manifest_raw)
    require(value.get('schema') == 'solcodex.quiet-campaign-compact-evidence.v4',
            'wrong bundle manifest schema')
    listed = value.get('entries')
    require(isinstance(listed, dict), 'missing bundle file index')
    actual = set()
    for path in root.rglob('*'):
        if path.is_file() or path.is_symlink():
            actual.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise ValueError('special bundle entry')
    require(actual == set(listed) | {'manifest.json'}, 'bundle file set differs')
    for name, entry in listed.items():
        relative = Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts
                and relative.as_posix() == name, 'unsafe bundle path')
        path = root / relative
        if entry.get('type') == 'file':
            raw = bytes_at(root, name)
            require(type(entry.get('bytes')) is int
                    and len(raw) == entry['bytes']
                    and digest(raw) == entry.get('sha256'),
                    'bundle content differs: ' + name)
        elif entry.get('type') == 'symlink':
            target = entry.get('target')
            require(path.is_symlink() and isinstance(target, str)
                    and not os.path.isabs(target) and '..' not in Path(target).parts
                    and os.readlink(path) == target,
                    'bundle symlink differs: ' + name)
        else:
            raise ValueError('invalid bundle entry type: ' + name)
    return value, digest(manifest_raw)


def tree_manifest(root):
    entries = []

    def walk(parent, prefix=''):
        for path in sorted(parent.iterdir(), key=lambda item: item.name):
            info = path.lstat()
            name = prefix + path.name
            if stat.S_ISDIR(info.st_mode):
                entries.append({'path': name, 'type': 'directory', 'mode': 0o755})
                walk(path, name + '/')
            elif stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                require(not os.path.isabs(target) and '..' not in Path(target).parts,
                        'unsafe snapshot symlink')
                entries.append({'path': name, 'type': 'symlink', 'target': target})
            elif stat.S_ISREG(info.st_mode):
                raw = path.read_bytes()
                entries.append({'path': name, 'type': 'file', 'size': len(raw),
                                'mode': 0o644 | (info.st_mode & 0o111),
                                'sha256': digest(raw)})
            else:
                raise ValueError('special snapshot entry')

    require(root.is_dir() and not root.is_symlink(), 'snapshot directory missing')
    walk(root)
    entries.sort(key=lambda entry: entry['path'])
    encoded = json.dumps(entries, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True).encode()
    return {'entries': entries, 'tree_sha256': digest(encoded)}


def usage_valid(value):
    return (isinstance(value, dict) and set(value) == set(USAGE)
            and all(type(value[key]) is int and value[key] >= 0 for key in USAGE)
            and value['cached_input_tokens'] <= value['input_tokens'])


def ledger_rows(host, name, row_id, arm):
    source = host / (name + '.sqlite3')
    require(source.is_file() and not source.is_symlink(), 'ledger missing')
    sidecars = [suffix for suffix in ('-wal', '-shm')
                if (host / (name + '.sqlite3' + suffix)).exists()]

    def read(directory, immutable):
        path = directory / (name + '.sqlite3')
        uri = path.resolve(strict=True).as_uri() + (
            '?mode=ro&immutable=1' if immutable else '?mode=ro')
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as database:
            require(database.execute('PRAGMA integrity_check').fetchone() == ('ok',),
                    'SQLite integrity failed')
            columns = [item[1] for item in database.execute(
                'PRAGMA table_info(attempts)').fetchall()]
            require(columns == ['attempt_id', 'run_id', 'arm', 'state', 'response_id',
                                'upstream_status', 'input_tokens', 'output_tokens',
                                'cached_input_tokens', 'reason'],
                    'ledger schema differs')
            return database.execute(
                'SELECT attempt_id,run_id,arm,state,response_id,upstream_status,'
                'input_tokens,output_tokens,cached_input_tokens,reason '
                'FROM attempts ORDER BY attempt_id').fetchall()

    if sidecars:
        # SQLite may create or change -shm even with mode=ro. Use a disposable copy.
        with tempfile.TemporaryDirectory(prefix='v4-ledger-verifier-') as temporary:
            target = Path(temporary)
            for suffix in ('', '-wal', '-shm'):
                path = host / (name + '.sqlite3' + suffix)
                if path.exists():
                    shutil.copyfile(path, target / path.name)
            rows = read(target, False)
    else:
        rows = read(host, True)
    attempts = {}
    response_ids = set()
    for (attempt_id, observed_run, observed_arm, state, response_id,
         status, inputs, outputs, cached, reason) in rows:
        require(isinstance(attempt_id, str) and attempt_id not in attempts
                and observed_run == row_id and observed_arm == arm
                and state in ('pending', 'unknown', 'completed'),
                'ledger row identity or state differs')
        usage = None
        if state == 'completed':
            usage = {'input_tokens': inputs, 'output_tokens': outputs,
                     'cached_input_tokens': cached}
            require(usage_valid(usage) and isinstance(response_id, str)
                    and len(response_id) == 64 and response_id not in response_ids,
                    'ledger completed usage differs')
            response_ids.add(response_id)
        attempts[attempt_id] = {'state': state, 'response_id': response_id,
                                'upstream_status': status, 'usage': usage,
                                'reason': reason}
    return attempts, bool(sidecars)


def provider_usage(transcript):
    replies = transcript.get('response_sse_utf8')
    hashes = transcript.get('response_sha256')
    require(isinstance(replies, list) and isinstance(hashes, list)
            and len(replies) == len(hashes), 'provider transcript shape differs')
    observed = {}
    identifiers = []
    for reply, expected_hash in zip(replies, hashes):
        require(isinstance(reply, str) and digest(reply.encode()) == expected_hash,
                'provider response hash differs')
        completions = []
        for frame in reply.split('\n\n'):
            if frame.startswith('data: ') and frame != 'data: [DONE]':
                event = document(frame[6:].encode())
                if event.get('type') == 'response.completed':
                    completions.append(event['response'])
        require(len(completions) == 1, 'provider completion missing or duplicated')
        response = completions[0]
        usage = response['usage']
        counts = {'input_tokens': usage['input_tokens'],
                  'output_tokens': usage['output_tokens'],
                  'cached_input_tokens': usage['input_tokens_details']['cached_tokens']}
        require(usage_valid(counts)
                and usage['total_tokens'] == counts['input_tokens'] + counts['output_tokens'],
                'provider usage malformed')
        identifier = digest(response['id'].encode())
        require(identifier not in observed, 'duplicate provider response ID')
        observed[identifier] = counts
        identifiers.append(response['id'])
    return observed, identifiers


def provider_exchange(root, row_id, transcript, expected_model):
    requests = transcript.get('requests')
    require(isinstance(requests, list), 'provider requests missing')
    provider, response_ids = provider_usage(transcript)
    require(len(requests) == len(response_ids),
            'provider request/response count differs')
    for request in requests:
        require(isinstance(request, dict)
                and set(request) == {'path', 'request_body_utf8', 'request_sha256'}
                and request['path'] == '/backend-api/codex/responses'
                and isinstance(request['request_body_utf8'], str)
                and digest(request['request_body_utf8'].encode()) == request['request_sha256'],
                'provider request body or route differs')
        body = document(request['request_body_utf8'].encode())
        require(isinstance(body, dict) and body.get('model') == expected_model
                and body.get('stream') is True,
                'provider request model or streaming differs')
    log = root / 'control-transcripts' / (row_id + '-events.jsonl')
    if 'events_sha256' not in transcript:
        require('events' not in transcript and not log.exists(),
                'provider send log missing from transcript')
        return provider, False
    raw = bytes_at(log.parent, log.name)
    require(isinstance(transcript['events_sha256'], str)
            and digest(raw) == transcript['events_sha256']
            and raw.endswith(b'\n'), 'provider send log hash differs')
    events = [document(line) for line in raw.splitlines()]
    require(events == transcript.get('events')
            and [event.get('seq') for event in events] == list(range(len(events))),
            'provider send log differs from transcript')
    groups = [[] for _ in requests]
    for event in events:
        slot = event.get('response_slot')
        require(type(slot) is int and 0 <= slot < len(groups),
                'provider send log slot differs')
        groups[slot].append(event)
    for slot, group in enumerate(groups):
        require([event.get('kind') for event in group] ==
                ['received', 'send_intent', 'sent'],
                'provider request/send sequence differs')
        request = requests[slot]
        require(all(group[0].get(key) == value for key, value in request.items())
                and group[1].get('response_id') == response_ids[slot]
                and group[1].get('response_sha256') == transcript['response_sha256'][slot]
                and group[1].get('response_sse_utf8') == transcript['response_sse_utf8'][slot]
                and group[2].get('response_sha256') == transcript['response_sha256'][slot],
                'provider request/send content differs')
    return provider, True


def recorded_quality(report, host, task, lock, baseline, checkpoint):
    canary_process = document(bytes_at(host, 'canaries.process.json'))
    canary_raw = bytes_at(host, 'canaries.stdout')
    canaries = document(canary_raw)
    canary_names = {'source_write', 'host_read', 'host_write', 'network'}
    require(canary_process.get('exit_code') == 0
            and canary_process.get('reason') is None
            and evaluator_cleanup_clean(canary_process)
            and digest(canary_raw) == canary_process.get('stdout_sha256')
            and len(canary_raw) == canary_process.get('stdout_bytes')
            and isinstance(canaries, dict)
            and set(canaries) == canary_names
            and all(canaries[name] is True for name in canary_names)
            and report.get('canaries') == canaries,
            'recorded evaluator isolation canary differs')
    expected = PACKAGING_CASES if task == 'packaging' else CLICK_CASES
    cases = report['cases']
    require([item.get('case') for item in cases] == list(expected),
            'recorded quality case inventory differs')
    for item in cases:
        name = item['case']
        process = document(bytes_at(host, name + '.process.json'))
        require(all(item.get(key) == value for key, value in process.items())
                and process.get('exit_code') == 0
                and process.get('reason') is None
                and evaluator_cleanup_clean(process),
                'recorded quality case process differs: ' + name)
        raw = bytes_at(host, name + '.stdout')
        stderr = bytes_at(host, name + '.stderr')
        require(digest(raw) == item.get('stdout_sha256')
                and len(raw) == item.get('stdout_bytes')
                and digest(stderr) == item.get('stderr_sha256')
                and len(stderr) == item.get('stderr_bytes'),
                'recorded quality case bytes differ: ' + name)
        observation = document(raw)
        want = expected[name]
        require(isinstance(observation, dict)
                and set(observation) == {'case', 'value', 'error'}
                and observation['case'] == name
                and item.get('observation') == observation
                and item['passed'] is (observation['error'] is None
                    and type(observation['value']) is type(want)
                    and observation['value'] == want),
                'recorded quality case decision differs: ' + name)
    process = document(bytes_at(host, 'upstream.process.json'))
    require(process == report.get('upstream_process')
            and process.get('exit_code') in (0, 1)
            and process.get('reason') is None
            and evaluator_cleanup_clean(process),
            'recorded upstream process differs')
    raw = bytes_at(host, 'upstream.stdout')
    require(digest(raw) == process.get('stdout_sha256')
            and len(raw) == process.get('stdout_bytes'),
            'recorded upstream output bytes differ')
    frame = document(raw)
    require(isinstance(frame, dict)
            and set(frame) == {'schema', 'exit_code', 'finished', 'nodes',
                               'reports', 'collection_errors'}
            and frame['schema'] == 1
            and frame['exit_code'] == process['exit_code']
            and frame['finished'] is True
            and frame['collection_errors'] == []
            and isinstance(frame['nodes'], list) and frame['nodes']
            and all(type(name) is str for name in frame['nodes'])
            and len(set(frame['nodes'])) == len(frame['nodes'])
            and isinstance(frame['reports'], list),
            'recorded upstream frame differs')
    by_node = {name: [] for name in frame['nodes']}
    for item in frame['reports']:
        require(isinstance(item, dict)
                and set(item) == {'nodeid', 'when', 'outcome', 'wasxfail'}
                and item['nodeid'] in by_node
                and item['when'] in ('setup', 'call', 'teardown')
                and item['outcome'] in ('passed', 'failed', 'skipped'),
                'recorded upstream test report differs')
        by_node[item['nodeid']].append(item)
    for rows in by_node.values():
        phases = [item['when'] for item in rows]
        require(len(set(phases)) == len(phases)
                and 'setup' in phases and 'teardown' in phases
                and (next(item for item in rows if item['when'] == 'setup')
                     ['outcome'] != 'passed' or 'call' in phases),
                'recorded upstream test lifecycle missing')
    unexpected = sorted({item['nodeid'] for item in frame['reports']
                         if item['outcome'] == 'skipped' and
                         lock['allowed_skips'][task].get(item['nodeid']) !=
                         ('XFAIL' if item['wasxfail'] is not None else 'SKIPPED')})
    expected_upstream = dict(frame)
    expected_upstream['passed'] = (frame['exit_code'] == 0 and
                                   not any(item['outcome'] == 'failed'
                                           for item in frame['reports']) and
                                   not unexpected)
    expected_upstream['unexpected_skips'] = unexpected
    expected_upstream['inventory_sha256'] = digest(json.dumps(
        frame['nodes'], separators=(',', ':')).encode())
    require(report['upstream'] == expected_upstream
            and frame['nodes'] == lock['inventory'][task],
            'recorded upstream decision differs')
    before = {entry['path']: entry for entry in baseline['entries']}
    after = {entry['path']: entry for entry in checkpoint['entries']}
    changed = sorted(name for name in before.keys() | after.keys()
                     if before.get(name) != after.get(name))
    require(report.get('changed_paths') == changed
            and all(name.startswith('src/' + task + '/') for name in changed),
            'recorded quality changed paths differ')


def evaluator_cleanup_clean(process):
    return (process.get('cleanup_verified') is True
            and process.get('remaining_descendants') == []
            and process.get('cleanup_errors') == []
            and process.get('handles_verified') is True
            and isinstance(process.get('handle_observations'), list)
            and bool(process['handle_observations']))


def expected_sandbox_profile(run_path, protocol, port):
    runtime = Path(protocol['host_python_runtime']['base_prefix'])
    def quoted(value):
        value = str(value)
        require('"' not in value and '\n' not in value,
                'unsafe sandbox profile path')
        return '"' + value + '"'
    literals = ['/', '/private', '/private/tmp', '/Applications', '/Library',
                run_path, run_path, *runtime.parents]
    system = ['/System', '/usr', '/bin', '/sbin',
              '/Applications/ChatGPT.app', '/Applications/Xcode.app',
              '/Library/Developer/CommandLineTools', '/private/etc',
              '/private/var/select', runtime, '/dev']
    readable = [run_path / name for name in
                ('workspace', 'home', 'tmp', 'tools', 'agent-artifacts')]
    writable = [run_path / name for name in
                ('workspace', 'home', 'tmp', 'agent-artifacts')]
    lines = ['(version 1)', '(allow default)', '(deny file-read*)']
    lines += [f'(allow file-read* (literal {quoted(path)}))'
              for path in run_path.parents]
    lines += [f'(allow file-read* (literal {quoted(path)}))'
              for path in literals]
    lines += ['(allow file-read* (literal "/var"))',
              '(allow file-read* (subpath "/var/select"))',
              '(allow file-read* (literal "/private/var"))',
              '(allow file-read* (subpath "/etc"))']
    lines += [f'(allow file-read* (subpath {quoted(path)}))'
              for path in [*system, *readable]]
    lines += ['(deny file-write*)']
    lines += [f'(allow file-write* (subpath {quoted(path)}))'
              for path in writable]
    lines += ['(allow file-write* (literal "/dev/null"))',
              '(deny network-outbound)',
              f'(allow network-outbound (remote ip "localhost:{port}"))']
    return '\n'.join(lines) + '\n'


def first_command(host, final, assignment, manifest, protocol):
    raw = bytes_at(host, 'trace.jsonl')
    require(digest(raw) == final.get('trace_sha256')
            and len(raw) == final.get('trace_bytes'), 'trace hash or size differs')
    lines = raw.splitlines()
    events = [document(line) for line in lines]
    terminals = [event for event in events
                 if event.get('type') in ('turn.completed', 'turn.failed')]
    require(events and events[-1].get('type') == 'turn.completed'
            and len(terminals) == 1
            and isinstance(events[-1].get('usage'), dict)
            and all(events[-1]['usage'].get(key) == final.get('usage', {}).get(key)
                    for key in USAGE),
            'CLI trace terminal outcome differs')
    items = [event for event in events if event.get('type', '').startswith('item.')]
    require(len(items) >= 2 and items[0]['type'] == 'item.started'
            and items[1]['type'] == 'item.completed'
            and items[0]['item'].get('type') == 'command_execution'
            and items[1]['item'].get('type') == 'command_execution'
            and isinstance(items[0]['item'].get('id'), str)
            and bool(items[0]['item']['id'])
            and items[0]['item']['id'] == items[1]['item'].get('id'),
            'first CLI action is not a completed diagnostic')
    command = document(bytes_at(host, 'model-command.json'))
    require(isinstance(command, list), 'model command missing')
    binaries = [item for item in command if isinstance(item, str)
                and item.endswith('/tools/codex')]
    require(len(binaries) == 1, 'model command binary ambiguous')
    run_path = Path(binaries[0]).parent.parent
    require(run_path.name == assignment['id']
            and run_path.parent.name == 'variance-control-' + manifest['case'] +
            '-' + manifest['protocol_sha256'][:16],
            'model command campaign path differs')
    require(final['broker'].get('ledger_path') == str(
        run_path / 'host-artifacts/delivery.sqlite3'),
        'model command ledger path differs')
    fixed = ['exec', '--json', '--ephemeral', '--skip-git-repo-check',
             '-s', 'danger-full-access', '-m', protocol['cli']['model'],
             '-c', 'model_reasoning_effort=' + json.dumps(protocol['cli']['effort']),
             '-c', 'model_provider="local_probe"', '--enable', 'code_mode',
             '--disable', 'hooks', '--disable', 'plugins', '--disable', 'apps',
             '--disable', 'browser_use', '--disable', 'browser_use_external',
             '--disable', 'browser_use_full_cdp_access', '--disable', 'computer_use',
             '--disable', 'multi_agent', '--disable', 'skill_search',
             '-c', 'suppress_unstable_features_warning=true']
    require(protocol['cli']['code_mode'] is True
            and command[:2] == ['/usr/bin/sandbox-exec', '-p']
            and command[3] == binaries[0]
            and command[4:-1] == fixed,
            'model command differs from frozen CLI protocol')
    profile = bytes_at(host, 'seatbelt.sb').decode()
    outbound = re.search(
        r'\(allow network-outbound \(remote ip "localhost:([0-9]+)"\)\)',
        profile)
    require(command[2] == profile
            and outbound is not None and 0 < int(outbound.group(1)) <= 65535
            and profile == expected_sandbox_profile(
                run_path, protocol, outbound.group(1)),
            'model sandbox profile differs from confined policy')
    task = {'click': 'tests/', 'packaging': 'tests/test_metadata.py'}[
        assignment['task']]
    flags = ['-v'] if assignment['arm'] == 'verbose' else ['-q', '--tb=short']
    diagnostic = shlex.join([str(run_path / 'tools/venv/bin/python'), '-m',
                             'pytest', task, *flags, '-p', 'no:cacheprovider',
                             '-o', 'addopts='])
    prompt = bytes_at(host, 'prompt.txt').decode().splitlines()
    require(len(prompt) >= 2 and prompt[1] == diagnostic,
            'diagnostic prompt differs from frozen task')
    require(command[-1] == bytes_at(host, 'prompt.txt').decode(),
            'model command prompt differs')
    expected = '/bin/zsh -lc ' + shlex.quote(diagnostic)
    require(items[0]['item'].get('command') == expected
            and items[1]['item'].get('command') == expected
            and items[1]['item'].get('exit_code') == 1
            and final.get('first_command', {}).get('first_command_exact') is True
            and final['first_command'].get('first_command_completed') is True
            and final['first_command'].get('first_exit_code') == 1
            and final['first_command'].get('malformed_lines') == 0
            and final['first_command'].get('other_prior_actions') == []
            and final['first_command'].get('trace_complete') is True
            and final['first_command'].get('status') == 'observed',
            'first diagnostic command differs')
    return protocol['cli']['model']


def verify_slot(root, manifest, protocol, lock, assignment, finish):
    row_id = assignment['id']
    run = root / row_id
    host = run / 'host-artifacts'
    seal_raw = bytes_at(run, 'seal.json')
    require(digest(seal_raw) == finish.get('seal_sha256'), 'journal seal hash differs')
    seal = document(seal_raw)
    require(seal.get('schema') == 'solcodex.quiet-campaign-partial-seal.v4'
            and seal.get('id') == row_id, 'seal identity differs')
    host_names = {path.name for path in host.iterdir() if path.is_file()}
    require(host_names == set(seal['host_files'])
            and REQUIRED_HOST <= host_names, 'host file set differs')
    for name, expected in seal['host_files'].items():
        require(digest(bytes_at(host, name)) == expected,
                'sealed host byte differs: ' + row_id + '/' + name)
    require(finish.get('final_sha256') == seal['host_files']['final.json'],
            'journal final hash differs')
    final = document(bytes_at(host, 'final.json'))
    result = document(bytes_at(host, 'result.json'))
    require(all(final.get(key) == value for key, value in result.items()
                if key != 'quality'), 'final/result identity differs')
    require(all(final.get(key) == assignment[key]
                for key in ('id', 'task', 'arm')), 'slot assignment differs')
    require(result.get('artifacts') == {name: value for name, value in
                seal['host_files'].items() if name not in ('result.json', 'final.json')},
            'result artifact inventory differs')
    require(seal['campaign_pins'] == {
        'protocol.json': manifest['protocol_sha256'],
        'schedule.json': manifest['schedule_sha256']}, 'campaign pins differ')
    for name, expected in protocol['cli'].items():
        if name == 'sha256':
            require(seal['copied_cli_sha256'].get('codex') == expected,
                    'copied CLI pin differs')
        elif name == 'code_mode_host_sha256':
            require(seal['copied_cli_sha256'].get('codex-code-mode-host') == expected,
                    'copied Code Mode host pin differs')
    require(manifest['observed_copied_cli_sha256'].get(row_id) ==
            seal['copied_cli_sha256'], 'collector observed CLI pin differs')
    expected_model = first_command(host, final, assignment, manifest, protocol)
    baseline = document(bytes_at(host, 'baseline.json'))
    source_tree = tree_manifest(root / 'fixtures' / assignment['task'])
    require(baseline == source_tree
            and baseline['tree_sha256'] == protocol['source_tree_sha256'][assignment['task']],
            'baseline fixture differs')
    checkpoint = tree_manifest(host / 'snapshot')
    require(final.get('checkpoint', {}).get('status') == 'captured'
            and final['checkpoint'].get('manifest') == checkpoint
            and seal['checkpoint_tree_sha256'] == checkpoint['tree_sha256'],
            'checkpoint snapshot differs')
    source_unchanged = checkpoint['tree_sha256'] == baseline['tree_sha256']
    require(final.get('source_unchanged') is source_unchanged,
            'source unchanged flag differs from source trees')
    report_path = root / 'quality' / row_id / 'report.json'
    report_raw = bytes_at(report_path.parent, 'report.json')
    report = document(report_raw)
    original_root = manifest['original_quality_report_roots'].get(row_id)
    require(isinstance(original_root, str)
            and final.get('quality_report_root') == original_root
            and report.get('root') == original_root
            and digest(report_raw) == final.get('quality_report_sha256')
            and digest(report_raw) == seal.get('quality_report_sha256')
            and report.get('evidence_sha256') == seal['host_files']['result.json']
            and report.get('evaluator_sha256') == protocol['code_sha256']['evaluate.py']
            and report.get('assets_lock_sha256') == protocol['quality']['assets_lock_sha256']
            and report.get('baseline_tree_sha256') == baseline['tree_sha256']
            and report.get('candidate_tree_sha256') == checkpoint['tree_sha256']
            and report.get('task') == assignment['task']
            and report.get('status') == final.get('quality_status')
            and report.get('quality') is final.get('quality'),
            'quality report binding differs')
    cases = report.get('cases')
    require(isinstance(cases, list) and cases
            and all(isinstance(item, dict) and type(item.get('passed')) is bool
                    for item in cases)
            and report.get('behavior_total') == len(cases)
            and report.get('behavior_passed') == sum(item['passed'] for item in cases)
            and report.get('restored_source_unchanged') is True
            and isinstance(report.get('upstream'), dict)
            and type(report['upstream'].get('passed')) is bool
            and report.get('status') in ('pass', 'fail')
            and report.get('quality') is (
                all(item['passed'] for item in cases)
                and report['upstream']['passed'])
            and report['status'] == ('pass' if report['quality'] else 'fail'),
            'quality decision differs from recorded cases')
    quality_host = report_path.parent / 'host'
    require(all(path.is_file() and not path.is_symlink()
                for path in quality_host.iterdir()),
            'quality host contains nonregular artifact')
    observed_artifacts = {
        'host/' + path.name: digest(path.read_bytes()) for path in quality_host.iterdir()
        if path.is_file() and not path.is_symlink()}
    require(observed_artifacts == report.get('artifact_hashes')
            and observed_artifacts == seal.get('quality_report_artifacts'),
            'quality artifact set differs')
    recorded_quality(report, quality_host, assignment['task'], lock,
                     baseline, checkpoint)
    upstream, upstream_sidecars = ledger_rows(host, 'upstream', row_id, 'host_upstream')
    delivery, delivery_sidecars = ledger_rows(host, 'delivery', row_id, 'private')
    require(upstream and set(upstream) == set(delivery)
            and not upstream_sidecars and not delivery_sidecars,
            'recorded complete accounting has sidecars or differing attempts')
    require(all(attempt['state'] == 'completed' for attempt in upstream.values())
            and all(attempt['state'] == 'completed' for attempt in delivery.values()),
            'recorded accounting incomplete')
    require(delivery == upstream and seal['ledger'] == {
        'complete': True, 'verified_usage': {
            key: sum(attempt['usage'][key] for attempt in upstream.values())
            for key in USAGE},
        'known_upstream_usage': {
            key: sum(attempt['usage'][key] for attempt in upstream.values())
            for key in USAGE},
        'upstream_attempt_count': len(upstream),
        'delivery_attempts': delivery, 'upstream_attempts': upstream,
        'errors': []}, 'sealed ledger differs from SQLite')
    transcript = document(bytes_at(root / 'control-transcripts', row_id + '.json'))
    require(transcript.get('schema') == 'solcodex.local-control-provider-transcript.v4'
            and transcript.get('id') == row_id
            and transcript.get('case') == manifest['case']
            and transcript.get('errors') == []
            and transcript.get('server_stopped') is True,
            'provider transcript status differs')
    provider, provider_send_log = provider_exchange(root, row_id, transcript,
                                                    expected_model)
    require(set(provider) == {attempt['response_id'] for attempt in upstream.values()},
            'provider response IDs differ from upstream ledger')
    for attempt_id, observed in upstream.items():
        require(provider[observed['response_id']] == observed['usage']
                and delivery[attempt_id]['response_id'] == observed['response_id']
                and delivery[attempt_id]['usage'] == observed['usage'],
                'provider/delivery/upstream usage differs')
    usage = {key: sum(attempt['usage'][key] for attempt in upstream.values())
             for key in USAGE}
    cleanup = final.get('cleanup', {})
    require(cleanup.get('verified') is True
            and type(cleanup.get('remaining_descendants')) is int
            and cleanup['remaining_descendants'] == 0
            and cleanup.get('open_work_handles') is False
            and cleanup.get('watch_errors') == []
            and cleanup.get('output_overflow') is False
            and cleanup.get('output_limit_bytes') == 4 * 1024 * 1024,
            'cleanup receipt contradicts verified state')
    require(type(final.get('elapsed_seconds')) in (float, int)
            and math.isfinite(final['elapsed_seconds'])
            and final['elapsed_seconds'] >= 0
            and type(final.get('timed_out')) is bool,
            'completed slot timing differs')
    require(final.get('live') is True
            and final.get('exit_code') == 0
            and final.get('outcome') == 'worker_completed'
            and final['timed_out'] is False,
            'completed normal-control process outcome differs')
    require(final.get('usage') == usage
            and final.get('reconciliation', {}).get('usage') == usage
            and final['reconciliation'].get('complete') is True
            and final['reconciliation'].get('missing_upstream_attempt_ids') == []
            and final['reconciliation'].get('unexpected_upstream_attempt_ids') == []
            and final.get('broker_stopped') is True
            and final.get('bridge_stopped') is True
            and final.get('cleanup', {}).get('verified') is True
            and final.get('technical_ok') is True
            and seal['ledger'].get('verified_usage') == usage
            and seal['accounting_closed'] is True
            and seal['complete_evidence'] is True
            and seal['issues'] == [], 'closed accounting or admission differs')
    broker = final['broker']
    bridge = final['bridge']
    count = len(upstream)
    expected_states = {'completed': count, 'pending': 0, 'unknown': 0}
    require(broker.get('run_id') == row_id and bridge.get('run_id') == row_id
            and broker.get('ledger_error') is False
            and bridge.get('ledger_error') is False
            and type(bridge.get('active_handlers')) is int
            and bridge['active_handlers'] == 0
            and broker.get('model_requests_forwarded') == count
            and broker.get('usage') == usage
            and bridge.get('usage') == usage
            and broker.get('request_cap') == protocol['request_cap']
            and bridge.get('request_cap') == protocol['request_cap']
            and broker.get('ledger_source_sha256') == protocol['usage_ledger_sha256']
            and bridge.get('ledger_source_sha256') == protocol['usage_ledger_sha256']
            and broker.get('usage_status') == 'complete_observed'
            and broker.get('cap_or_deadline_rejections') == 0
            and bridge.get('rejections') == {}
            and all(isinstance(value.get('accounting'), dict)
                    and value['accounting'].get('attempts') == count
                    and value['accounting'].get('states') == expected_states
                    and value['accounting'].get('all_attempts_have_observed_usage') is True
                    and value['accounting'].get('observed_completed_usage') == usage
                    for value in (broker, bridge)),
            'broker or bridge accounting differs')
    requests = broker['requests']
    bridge_attempts = bridge['attempts']
    require(len(requests) == len(upstream)
            and {item['attempt_id'] for item in requests} == set(upstream)
            and set(bridge_attempts) == set(upstream),
            'broker/bridge attempt IDs differ')
    for request in requests:
        attempt_id = request['attempt_id']
        bridged = bridge_attempts[attempt_id]
        require(request.get('forwarded') is True
                and request.get('upstream_attempted') is True
                and request.get('completed') is True
                and request.get('usage') == upstream[attempt_id]['usage']
                and request.get('status') == upstream[attempt_id]['upstream_status']
                and bridged.get('state') == 'completed'
                and bridged.get('upstream_status') == upstream[attempt_id]['upstream_status']
                and all(bridged.get(key) == upstream[attempt_id]['usage'][key]
                        for key in USAGE), 'broker/bridge attempt differs')
    require(finish.get('admissible') is True
            and finish.get('pins_valid') is True
            and finish.get('run_error') is None
            and finish.get('quality_status') == final['quality_status'],
            'journal admission differs')
    return {'quality': final['quality'], 'quality_status': final['quality_status'],
            'source_unchanged': source_unchanged,
            'usage': usage, 'requests': len(upstream),
            'elapsed_seconds': final['elapsed_seconds'],
            'timed_out': final['timed_out'],
            'seal_sha256': digest(seal_raw),
            'provider_send_log_present': provider_send_log}


def public_anchor(public_root, head, protocol_raw, plan_raw, root, protocol):
    require(public_root.is_dir() and (public_root / '.git').exists(),
            'public Git checkout missing')
    require(isinstance(head, str) and len(head) == 40
            and all(char in '0123456789abcdef' for char in head),
            'journal public commit invalid')

    def committed(relative):
        return subprocess.check_output(['git', 'show', head + ':' + relative],
                                       cwd=public_root, timeout=15)

    require(committed('docs/measurements/data/2026-09-25-quiet-variance-calibration-bundle-v4-candidate.json') == protocol_raw,
            'protocol differs from journal public commit')
    require(committed('docs/research/data/2026-09-25-quiet-variance-bundle-v4-campaign-expectations.json') == plan_raw,
            'plan differs from journal public commit')
    fixture_paths = {
        'variance_calibration_bundle_v4.py':
            'docs/measurements/fixtures/2026-09-25-quiet-variance-calibration-bundle-v4-runner.py',
        'campaign_state_v4.py':
            'docs/measurements/fixtures/2026-09-25-quiet-variance-campaign-state-v4.py',
        'control_provider_v4.py':
            'docs/measurements/fixtures/2026-09-25-quiet-variance-control-provider-v4.py',
        'bundled_cli_v3.py':
            'docs/measurements/fixtures/2026-09-25-quiet-variance-bundled-cli-v3.py',
    }
    for name, relative in fixture_paths.items():
        require(committed(relative) == bytes_at(root / 'source', name),
                'source differs from journal public commit: ' + name)
    require(digest(committed('scripts/reduce_quiet_variance_calibration_v4.py')) ==
            protocol['analysis_reducer_sha256'], 'analysis reducer public pin differs')
    remote = subprocess.check_output(['git', 'rev-parse', 'origin/main'],
                                     cwd=public_root, timeout=10).decode().strip()
    require(subprocess.run(['git', 'merge-base', '--is-ancestor', head, remote],
                           cwd=public_root, timeout=10).returncode == 0,
            'journal commit not on tracked public main')


def verify(root, plan_path, public_root):
    root = root.resolve(strict=True)
    require(root.is_dir() and not root.is_symlink(), 'bundle root differs')
    manifest, manifest_sha = manifest_closure(root)
    protocol_raw = bytes_at(root, 'protocol.json')
    schedule_raw = bytes_at(root, 'schedule.json')
    journal_raw = bytes_at(root, 'journal.jsonl')
    protocol = document(protocol_raw)
    schedule = document(schedule_raw)
    plan_raw = plan_path.read_bytes()
    plan = document(plan_raw)
    require(digest(protocol_raw) == manifest['protocol_sha256']
            and digest(schedule_raw) == manifest['schedule_sha256']
            and digest(journal_raw) == manifest['journal_sha256']
            and digest(plan_raw) == protocol['control_plan_sha256']
            and plan['schedule_sha256'] == manifest['schedule_sha256']
            and protocol['schedule_sha256'] == manifest['schedule_sha256']
            and plan['model_run_authorized'] is False
            and protocol['model_run_authorized'] is False
            and protocol['status'] == 'candidate',
            'protocol, plan, or bundle pins differ')
    require(manifest['case'] in ('quality_fail_continues', 'complete_16')
            and manifest['case'] in {case['id'] for case in plan['cases']},
            'control case absent from plan')
    rows = schedule['schedule']
    require(len(rows) == 16 and len({row['id'] for row in rows}) == 16,
            'frozen 16-slot schedule differs')
    for name, expected in protocol['code_sha256'].items():
        require(digest(bytes_at(root / 'source', name)) == expected,
                'pinned source differs: ' + name)
    lock_raw = bytes_at(root / 'quality-assets', 'lock.json')
    require(digest(lock_raw) == protocol['quality']['assets_lock_sha256'],
            'quality asset lock differs')
    lock = document(lock_raw)
    require(lock.get('schema') == 1
            and set(lock.get('files', {})) == {'verify_click.py', 'verify_packaging.py'}
            and set(lock.get('baselines', {})) == {'click', 'packaging'},
            'quality asset inventory differs')
    for name, expected in lock['files'].items():
        require(digest(bytes_at(root / 'quality-assets', name)) == expected,
                'quality verifier asset differs: ' + name)
    for task, expected in lock['baselines'].items():
        require(tree_manifest(root / 'quality-assets' / task / 'baseline') == expected,
                'quality baseline asset differs: ' + task)
    require(not journal_raw or journal_raw.endswith(b'\n'),
            'partial journal line')
    events = [document(line) for line in journal_raw.splitlines()]
    heads = {event.get('public_head') for event in events
             if event.get('kind') == 'start'}
    require(len(heads) == 1, 'journal public commit absent or inconsistent')
    public_head = next(iter(heads))
    public_anchor(public_root, public_head, protocol_raw, plan_raw, root,
                  protocol)
    next_index = 0
    pending = None
    phase = 'ready'
    finishes = {}
    starts = []
    for seq, event in enumerate(events):
        require(type(event.get('seq')) is int and event['seq'] == seq,
                'journal sequence differs')
        kind = event.get('kind')
        if kind == 'start':
            require(phase == 'ready' and pending is None and next_index < 16
                    and event.get('id') == rows[next_index]['id'],
                    'journal start order differs')
            pending = rows[next_index]
            starts.append(pending['id'])
            next_index += 1
        elif kind == 'finish':
            require(pending is not None and event.get('id') == pending['id'],
                    'unpaired journal finish')
            info = verify_slot(root, manifest, protocol, lock, pending, event)
            finishes[pending['id']] = info
            expected = 'complete' if next_index == 16 else 'continue'
            require(event.get('disposition') == expected
                    and event.get('reason') is None,
                    'successful disposition differs')
            if expected == 'complete':
                phase = 'complete'
            pending = None
        elif kind == 'stopped':
            require(phase == 'ready' and pending is None and next_index < 16
                    and event.get('next_id') == rows[next_index]['id'],
                    'prestart stop differs')
            phase = 'stop'
        else:
            raise ValueError('unsupported journal event in recorded-evidence verifier')
    if pending is not None:
        phase = 'unresolved'
    require(starts == manifest['started_ids'], 'bundle start inventory differs')
    unstarted = [row['id'] for row in rows[next_index:]]
    for row in rows[next_index:]:
        require(not (root / row['id']).exists()
                and not (root / 'quality' / row['id']).exists(),
                'unstarted slot has bundle evidence')
    export = document(bytes_at(root, 'analysis-export.json'))
    require(export['protocol_sha256'] == manifest['protocol_sha256']
            and export['schedule_sha256'] == manifest['schedule_sha256']
            and export['campaign_state'] == {
                'ready': 'paused', 'complete': 'complete',
                'stop': 'stopped', 'unresolved': 'unresolved'}[phase]
            and len(export['slots']) == 16, 'analysis export header differs')
    for assignment, slot in zip(rows, export['slots']):
        require(all(slot.get(key) == assignment[key]
                    for key in ('id', 'task', 'arm', 'block')),
                'analysis assignment differs')
        if assignment['id'] in finishes:
            info = finishes[assignment['id']]
            require(slot['status'] == 'completed'
                    and slot['usage_state'] == 'verified_complete'
                    and slot['usage'] == info['usage']
                    and slot['known_upstream_usage'] == info['usage']
                    and slot['quality'] is info['quality']
                    and slot['timed_out'] is info['timed_out']
                    and slot['elapsed_seconds'] == info['elapsed_seconds']
                    and slot['accounting_evidence_sha256'] == info['seal_sha256'],
                    'analysis completed slot differs')
        elif assignment['id'] in unstarted:
            require(slot['status'] == 'unstarted'
                    and slot['usage_state'] == 'not_started'
                    and slot['usage'] is None
                    and slot['known_upstream_usage'] is None
                    and all(slot.get(key) is None for key in
                            ('quality', 'elapsed_seconds', 'timed_out', 'reason',
                             'accounting_evidence_sha256')),
                    'analysis unstarted slot differs')
        else:
            raise ValueError('analysis state unsupported by this partial verifier')
    first = finishes.get(rows[0]['id'])
    if manifest['case'] == 'quality_fail_continues':
        scenario_met = (first is not None
                        and first['quality_status'] == 'fail'
                        and first['source_unchanged'] is False
                        and len(finishes) >= 2)
    elif manifest['case'] == 'complete_16':
        scenario_met = phase == 'complete' and len(finishes) == 16
    else:
        scenario_met = False
    totals = {key: sum(info['usage'][key] for info in finishes.values())
              for key in USAGE}
    return {'schema': 'solcodex.quiet-campaign-recorded-evidence-check.v4',
            'case': manifest['case'], 'manifest_sha256': manifest_sha,
            'public_head': public_head,
            'verified_recorded_properties': [
                'bundle file hashes and frozen public source pins',
                'journal order and sealed host file hashes',
                'fixture and checkpoint source trees',
                'SQLite attempt accounting and synthetic provider usage',
                'recorded quality summary consistency and asset locks',
            ],
            'recorded_scenario_shape_met': scenario_met,
            'campaign_path_qualified': False,
            'started_count': len(starts), 'sealed_count': len(finishes),
            'unstarted_ids': unstarted,
            'quality_statuses': {row_id: info['quality_status']
                                 for row_id, info in finishes.items()},
            'synthetic_usage': totals,
            'synthetic_provider_requests': sum(info['requests'] for info in finishes.values()),
            'provider_send_log_present': all(
                info['provider_send_log_present'] for info in finishes.values()),
            'limitations': [
                'compact bundle omits copied CLI, Code Mode host, virtual environment, CLI home and live workspace',
                'quality summary and assets are checked, but the external behavioral evaluator is not rerun',
                'provider requests are recorded by the synthetic provider; request-to-ledger-attempt linkage is not independently observed',
                'a source edit and quality failure do not independently prove which proposed repair produced the edit',
                'no contemporaneous process exits, network observer or crash-boundary acknowledgements are bundled',
                'historical public push timing is not established by this bundle',
                'only completed admissible slots are supported by this partial verifier',
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--public-root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.bundle, args.plan, args.public_root), sort_keys=True,
                     separators=(',', ':'), allow_nan=False))


if __name__ == '__main__':
    main()
