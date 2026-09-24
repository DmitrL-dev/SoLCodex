"""Exploratory, no-model CLI probe for diagnosis/resume/reset context boundaries.

Only a local synthetic provider is used. Output contains booleans and hashes,
not the private markers or raw requests. This is a transport gate, not an
efficacy or token-saving experiment.
"""

import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

from broker_route import Broker
from cli_toolcall_probe import payload
from host_bridge import HostBridge
from isolation import probe_result, quote, strict_profile
from live_pilot import environment, reconcile
from pilot import HERE
from process import run_managed
from test_host_bridge import provider, send


CLI = '/Applications/ChatGPT.app/Contents/Resources/codex'
DISABLE = ('hooks', 'plugins', 'apps', 'browser_use', 'browser_use_external',
           'browser_use_full_cdp_access', 'computer_use', 'multi_agent',
           'skill_search')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def setup(root):
    root.mkdir(mode=0o700)
    paths = {name: root/name for name in
             ('workspace', 'home', 'tmp', 'tools', 'host-artifacts', 'agent-artifacts')}
    for name, path in paths.items():
        path.mkdir(mode=0o700)
    (paths['workspace']/'README.md').write_text('Synthetic session-boundary fixture.\n')
    env = environment(root, paths['workspace'], paths['home'], paths['tmp'],
                      paths['tools'])
    return paths, env


def item(identifier, text):
    return {'id': identifier, 'type': 'message', 'role': 'assistant',
            'status': 'completed',
            'content': [{'type': 'output_text', 'text': text}]}


def run_turn(label, root, paths, env, prompt, answer, session=None,
             forbidden_home=None):
    host = paths['host-artifacts']
    deadline = time.monotonic() + 90
    key = secrets.token_hex(32)
    sent = payload('resp_'+label, item('msg_'+label, answer), (120, 20, 0))
    with provider(lambda handler: send(handler, sent, declared=len(sent))) as \
            (factory, requests, errors, _):
        bridge = HostBridge(label, key, host/(label+'-upstream.sqlite3'),
                            lambda: 'synthetic-token-never-real', deadline,
                            factory)
        broker = Broker(upstream=f'http://127.0.0.1:{bridge.server_address[1]}',
                        ledger_path=host/(label+'-delivery.sqlite3'),
                        run_id=label, bridge_key=key, deadline=deadline)
        try:
            port = broker.server_address[1]
            profile = strict_profile(root, paths['workspace'], paths['home'],
                                     paths['tmp'], paths['tools'],
                                     paths['agent-artifacts'], port)
            profile = profile.replace('(deny file-read*)\n',
                '(deny file-read*)\n'+''.join(
                    f'(allow file-read* (literal {quote(p)}))\n'
                    for p in root.parents))
            if forbidden_home is not None:
                sessions = [p for p in (forbidden_home/'sessions').rglob('*')
                            if p.is_file()]
                if not sessions:
                    raise AssertionError('parent session file missing')
                marker = sessions[0]
                denied = probe_result(profile, marker, 'read', paths['workspace'])
                if denied.returncode == 0 or 'PermissionError' not in denied.stderr:
                    raise AssertionError('parent session file was not denied by sandbox')
            (paths['home']/'config.toml').write_text(
                '[model_providers.local_probe]\nname="Synthetic loopback"\n'
                f'base_url="http://127.0.0.1:{port}/backend-api/codex"\n'
                'requires_openai_auth=false\nsupports_websockets=false\n'
                'request_max_retries=0\nstream_max_retries=0\n')
            common = ['--json', '--skip-git-repo-check', '-m', 'gpt-6-luna',
                      '-c', 'model_reasoning_effort="low"',
                      '-c', 'model_provider="local_probe"',
                      '-c', 'suppress_unstable_features_warning=true',
                      '--enable', 'code_mode']
            for feature in DISABLE:
                common += ['--disable', feature]
            if session is None:
                args = ['exec', *common, '-s', 'danger-full-access', prompt]
            else:
                args = ['exec', 'resume', *common,
                        '--dangerously-bypass-approvals-and-sandbox',
                        session, prompt]
            process, timed_out, cleanup = run_managed(
                ['/usr/bin/sandbox-exec', '-p', profile, CLI, *args],
                paths['workspace'], env, 80)
            trace = [json.loads(line) for line in process.stdout.splitlines()
                     if line.startswith('{')]
            thread = [row.get('thread_id') for row in trace
                      if row.get('type') == 'thread.started']
            finals = [row.get('item', {}).get('text') for row in trace
                      if row.get('type') == 'item.completed' and
                      row.get('item', {}).get('type') == 'agent_message']
            request_bodies = [json.loads(raw) for _, _, raw in requests]
            result = {'exit': process.returncode, 'timeout': timed_out,
                      'cleanup': cleanup['verified'], 'session': thread[-1] if thread else None,
                      'final_match': answer in finals,
                      'provider_requests': len(requests), 'provider_errors': len(errors),
                      'request_text': json.dumps(request_bodies, sort_keys=True),
                      'trace_sha256': hashlib.sha256(process.stdout.encode()).hexdigest(),
                      'stderr_sha256': hashlib.sha256(process.stderr.encode()).hexdigest(),
                      'event_types': [row.get('type') for row in trace]}
        finally:
            broker_stopped = broker.close()
            bridge_stopped = bridge.close()
            accounting = reconcile(broker.summary(), bridge.summary())
        result.update(broker_stopped=broker_stopped,
                      bridge_stopped=bridge_stopped,
                      accounted=accounting['complete'],
                      usage=accounting['usage'])
    return result


def main():
    os.umask(0o077)
    base = Path(tempfile.mkdtemp(prefix='phase-reset-transport-', dir=HERE))
    parent, parent_env = setup(base/'parent')
    reset, reset_env = setup(base/'reset')
    prior_only = 'PRIOR_ONLY_'+secrets.token_hex(12)
    handoff = 'HANDOFF_'+secrets.token_hex(12)
    diagnosis = run_turn('diagnosis', base/'parent', parent, parent_env,
                         'Diagnosis only. Private marker: '+prior_only,
                         'Diagnosis handoff: '+handoff)
    resume = run_turn('resume', base/'parent', parent, parent_env,
                      'Continue from the diagnosis.', 'Resume complete.',
                      diagnosis['session'])
    reset_sessions_before = list((reset['home']/'sessions').rglob('*')) \
        if (reset['home']/'sessions').exists() else []
    fresh = run_turn('reset', base/'reset', reset, reset_env,
                     'Continue from this explicit diagnosis handoff: '+handoff,
                     'Reset complete.', forbidden_home=parent['home'])
    report = {'schema': 'solcodex.phase-reset-transport-exploratory.v1',
              'scope': 'synthetic_provider_real_cli_no_model',
              'controller_sha256': digest(__file__),
              'cli_sha256': digest(CLI),
              'same_workspace_content':
                  (parent['workspace']/'README.md').read_bytes() ==
                  (reset['workspace']/'README.md').read_bytes(),
              'reset_sessions_empty_before': not reset_sessions_before,
              'resume_same_session': resume['session'] == diagnosis['session'],
              'reset_new_session': fresh['session'] != diagnosis['session'],
              'diagnosis_marker_in_resume_request': prior_only in resume['request_text'],
              'diagnosis_marker_in_reset_request': prior_only in fresh['request_text'],
              'handoff_in_reset_request': handoff in fresh['request_text'],
              'runs': {name: {k: v for k, v in run.items() if k not in
                             ('request_text', 'session')}
                       for name, run in [('diagnosis', diagnosis),
                                         ('resume', resume), ('reset', fresh)]},
              'private_root': str(base)}
    report['passed'] = (report['same_workspace_content'] and
                        report['reset_sessions_empty_before'] and
                        report['resume_same_session'] and report['reset_new_session'] and
                        report['diagnosis_marker_in_resume_request'] and
                        not report['diagnosis_marker_in_reset_request'] and
                        report['handoff_in_reset_request'] and
                        all(run['exit'] == 0 and not run['timeout'] and
                            run['cleanup'] and run['final_match'] and
                            run['provider_requests'] == 1 and
                            run['provider_errors'] == 0 and run['accounted'] and
                            run['broker_stopped'] and run['bridge_stopped']
                            for run in report['runs'].values()))
    (base/'report.json').write_text(json.dumps(report, sort_keys=True, indent=2)+'\n')
    print(json.dumps(report, sort_keys=True))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
