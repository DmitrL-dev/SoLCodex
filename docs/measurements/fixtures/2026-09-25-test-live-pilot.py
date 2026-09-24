"""No-model integration of the live runner with synthetic loopback transport."""

import http.client
import json
from pathlib import Path
import socket
import subprocess
import struct
import tempfile
import threading
import time
import tomllib
import unittest
from unittest.mock import patch

import live_pilot
import pilot
from test_host_bridge import BODY, completed, event, provider, send


class LivePilotTests(unittest.TestCase):
    def run_two_attempts(self, *, first_usage):
        first_started = threading.Event()
        cli_done = threading.Event()
        action_lock = threading.Lock()
        action_count = 0
        first_response = {'id': 'provider-first', 'status': 'completed'}
        if first_usage:
            first_response['usage'] = {'input_tokens': 120, 'output_tokens': 30,
                                       'total_tokens': 150,
                                       'input_tokens_details': {'cached_tokens': 40}}
        first_payload = event({'type': 'response.completed',
                               'response': first_response}) + b'data: [DONE]\n\n'
        second_payload = event({'type': 'response.completed', 'response': {
            'id': 'provider-second', 'status': 'completed', 'usage': {
                'input_tokens': 200, 'output_tokens': 50, 'total_tokens': 250,
                'input_tokens_details': {'cached_tokens': 150}}}}) + b'data: [DONE]\n\n'

        def action(handler):
            nonlocal action_count
            with action_lock:
                action_count += 1
                index = action_count
            if index == 1:
                handler.send_response(200)
                handler.send_header('Content-Type', 'text/event-stream')
                handler.end_headers()
                handler.wfile.write(event({'type': 'response.output_text.delta',
                                           'delta': 'synthetic-first'}))
                handler.wfile.flush()
                first_started.set()
                self.assertTrue(cli_done.wait(3))
                time.sleep(.25)
                handler.wfile.write(first_payload)
                handler.wfile.flush()
            else:
                send(handler, second_payload, declared=len(second_payload))

        def fake_runner(argv, cwd, env, remaining):
            home = Path(env['HOME'])
            config = tomllib.loads((home/'config.toml').read_text())
            base = config['model_providers']['local_probe']['base_url']
            port = int(base.split(':')[-1].split('/')[0])
            with socket.create_connection(('127.0.0.1', port), timeout=5) as first:
                first.settimeout(5)
                raw = (b'POST /backend-api/codex/responses HTTP/1.1\r\n'
                       b'Host: 127.0.0.1\r\nContent-Type: application/json\r\n'
                       + f'Content-Length: {len(BODY)}\r\n\r\n'.encode() + BODY)
                first.sendall(raw)
                self.assertTrue(first_started.wait(3))
                received = b''
                while b'synthetic-first' not in received:
                    received += first.recv(4096)
                    self.assertLess(len(received), 65536)
                first.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                 struct.pack('ii', 1, 0))
            second = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                second.request('POST', '/backend-api/codex/responses', BODY,
                               {'Content-Type': 'application/json'})
                response = second.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), second_payload)
            finally:
                second.close()
            command = pilot.diagnostic('packaging', 'quiet',
                                       home.parent/'tools/venv/bin/python')
            trace = '\n'.join(json.dumps(item) for item in (
                {'type': 'thread.started'}, {'type': 'turn.started'},
                {'type': 'item.started', 'item': {'type': 'command_execution',
                                                'id': 'diagnostic', 'command': command}},
                {'type': 'item.completed', 'item': {'type': 'command_execution',
                                                  'id': 'diagnostic', 'command': command,
                                                  'status': 'failed', 'exit_code': 1}},
                {'type': 'turn.completed'})) + '\n'
            cleanup = {'verified': True, 'remaining_descendants': 0,
                       'open_work_handles': False, 'watch_errors': []}
            cli_done.set()
            return subprocess.CompletedProcess(argv, 0, trace, ''), False, cleanup

        def fake_evaluate(task, evidence, digest):
            self.assertEqual(live_pilot.sha(evidence.read_bytes()), digest)
            root = evidence.parent/'synthetic-quality'
            root.mkdir()
            (root/'report.json').write_text('{}\n')
            return {'quality': False, 'status': 'fail', 'root': str(root)}

        protocol = json.loads((pilot.HERE/'selftest-protocol.json').read_text())
        row = protocol['schedule'][2]
        with tempfile.TemporaryDirectory(dir=pilot.HERE) as directory:
            with provider(action) as (factory, requests, errors, _):
                with patch.object(live_pilot, 'evaluate', side_effect=fake_evaluate):
                    result = live_pilot.one_live(row, Path(directory), protocol,
                        credential_supplier=lambda: 'synthetic-token-never-real',
                        provider_connection_factory=factory, runner=fake_runner)
        return result, requests, errors, protocol

    def test_two_attempts_count_late_completion_after_client_disconnect(self):
        result, requests, errors, protocol = self.run_two_attempts(first_usage=True)
        self.assertEqual(len(requests), 2)
        self.assertEqual(errors, [])
        self.assertTrue(result['technical_ok'], result.get('detail'))
        self.assertEqual(result['usage'], {'input_tokens': 320, 'output_tokens': 80,
                                           'cached_input_tokens': 190})
        self.assertEqual(result['bridge']['accounting']['states']['completed'], 2)
        first_id, second_id = (item['attempt_id'] for item in result['broker']['requests'])
        self.assertNotEqual(first_id, second_id)
        attempts = result['bridge']['attempts']
        self.assertEqual(set(attempts), {first_id, second_id})
        self.assertEqual(attempts[first_id]['delivery']['state'], 'stream_finished')
        self.assertEqual(attempts[second_id]['delivery']['state'], 'stream_finished')
        self.assertIsNotNone(result['broker']['requests'][0]['error'],
                             result['broker']['requests'][0])
        self.assertFalse(result['broker']['requests'][0]['completed'])
        self.assertTrue(result['broker']['requests'][1]['completed'])
        self.assertEqual((attempts[first_id]['input_tokens'],
                          attempts[first_id]['output_tokens'],
                          attempts[first_id]['cached_input_tokens']), (120, 30, 40))
        self.assertEqual((attempts[second_id]['input_tokens'],
                          attempts[second_id]['output_tokens'],
                          attempts[second_id]['cached_input_tokens']), (200, 50, 150))
        self.assertTrue(result['reconciliation']['complete'])
        self.assertGreater(result['elapsed_seconds']-result['cli_elapsed_seconds'], .15)
        self.assertTrue(result['first_command']['first_command_exact'])
        self.assertEqual(result['checkpoint']['status'], 'captured')
        self.assertTrue(result['source_unchanged'])
        self.assertEqual(result['checkpoint']['manifest']['tree_sha256'],
                         protocol['source_tree_sha256']['packaging'])
        self.assertEqual(result['quality_status'], 'fail')
        self.assertFalse(result['quality'])
        self.assertFalse(result['provider_billing_complete'])

    def test_two_attempts_missing_first_usage_blocks_complete_accounting(self):
        result, requests, errors, protocol = self.run_two_attempts(first_usage=False)
        self.assertEqual(len(requests), 2)
        self.assertEqual(errors, [])
        self.assertFalse(result['technical_ok'])
        self.assertFalse(result['reconciliation']['complete'])
        self.assertIsNone(result['usage'])
        self.assertEqual(result['bridge']['accounting']['states'],
                         {'pending': 0, 'completed': 1, 'unknown': 1})
        self.assertEqual(result['bridge']['accounting']['observed_completed_usage'],
                         {'input_tokens': 200, 'output_tokens': 50,
                          'cached_input_tokens': 150})
        self.assertTrue(result['first_command']['first_command_exact'])
        self.assertEqual(result['checkpoint']['status'], 'captured')
        self.assertTrue(result['source_unchanged'])
        self.assertFalse(result['provider_billing_complete'])

    def test_late_upstream_completion_counts_in_wall_time(self):
        started = threading.Event()
        payload = completed() + b'data: [DONE]\n\n'
        def action(handler):
            started.set()
            time.sleep(.55)
            send(handler, payload, declared=len(payload))

        def fake_runner(argv, cwd, env, remaining):
            config = tomllib.loads((Path(env['HOME'])/'config.toml').read_text())
            port = int(config['model_providers']['local_probe']['base_url'].split(':')[-1].split('/')[0])
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            connection.request('POST', '/backend-api/codex/responses', BODY)
            self.assertTrue(started.wait(3))
            connection.close()
            command = pilot.diagnostic('packaging', 'quiet',
                                       Path(env['HOME']).parent/'tools/venv/bin/python')
            trace = '\n'.join(json.dumps(event) for event in (
                {'type':'thread.started'}, {'type':'turn.started'},
                {'type':'item.started','item':{'type':'command_execution','id':'first',
                                               'command':command}},
                {'type':'item.completed','item':{'type':'command_execution','id':'first',
                                                 'command':command,'status':'failed',
                                                 'exit_code':1}},
                {'type':'turn.completed'}))+'\n'
            cleanup = {'verified':True,'remaining_descendants':0,
                       'open_work_handles':False,'watch_errors':[]}
            return subprocess.CompletedProcess(argv, 0, trace, ''), False, cleanup

        def fake_evaluate(task, evidence, digest):
            root = evidence.parent/'synthetic-quality'
            root.mkdir()
            (root/'report.json').write_text('{}\n')
            return {'quality':False,'status':'fail','root':str(root)}

        protocol = json.loads((pilot.HERE/'selftest-protocol.json').read_text())
        with tempfile.TemporaryDirectory(dir=pilot.HERE) as directory:
            with provider(action) as (factory, requests, errors, _):
                with patch.object(live_pilot, 'evaluate', side_effect=fake_evaluate):
                    result = live_pilot.one_live(protocol['schedule'][2], Path(directory), protocol,
                        credential_supplier=lambda:'synthetic-token-never-real',
                        provider_connection_factory=factory, runner=fake_runner)
            self.assertTrue(result['technical_ok'], result.get('detail'))
            self.assertTrue(result['reconciliation']['complete'])
            self.assertEqual(result['usage']['input_tokens'], 100)
            self.assertGreater(result['elapsed_seconds']-result['cli_elapsed_seconds'], .25)
            self.assertEqual(len(requests), 1)
            self.assertEqual(errors, [])

    def test_screen_rule_requires_quality_adherence_usage_and_both_ratios(self):
        thresholds = {'max_total_token_ratio': .85,
                      'max_wall_time_ratio': 1.25}
        rows = []
        for task in ('click', 'packaging'):
            for arm in ('verbose', 'quiet'):
                rows.append({'task': task, 'arm': arm, 'technical_ok': True,
                             'exit_code': 0, 'timed_out': False,
                             'quality_status': 'pass', 'quality': True,
                             'first_command': {'first_command_exact': True},
                             'reconciliation': {'complete': True},
                             'usage': {'input_tokens': 80 if arm == 'quiet' else 100,
                                       'output_tokens': 0, 'cached_input_tokens': 0},
                             'elapsed_seconds': 10 if arm == 'quiet' else 9})
        self.assertTrue(live_pilot.screen_result(rows, thresholds)['pass'])
        self.assertEqual(live_pilot.screen_result(rows, thresholds)['token_ratio'], .8)
        rows[0]['quality'] = False
        self.assertFalse(live_pilot.screen_result(rows, thresholds)['pass'])
        rows[0]['quality'] = True
        rows[1]['usage'] = None
        failed = live_pilot.screen_result(rows, thresholds)
        self.assertFalse(failed['pass'])
        self.assertIsNone(failed['token_ratio'])
        rows[1]['usage'] = {'input_tokens': 80, 'output_tokens': 0,
                            'cached_input_tokens': 0}
        rows[1]['first_command']['first_command_exact'] = False
        self.assertFalse(live_pilot.screen_result(rows, thresholds)['pass'])
        rows[1]['first_command']['first_command_exact'] = True
        rows[1]['elapsed_seconds'] = 30
        self.assertFalse(live_pilot.screen_result(rows, thresholds)['gates']['max_wall_time_ratio'])
        self.assertFalse(live_pilot.screen_result(rows[:3], thresholds)['pass'])

    def test_reconcile_rejects_incomplete_and_unmatched_attempts(self):
        delivery = {'requests': [{'attempt_id': 'a'}], 'ledger_error': False,
                    'accounting': {'attempts': 1}}
        upstream = {'attempts': {'a': {'state': 'completed'}}, 'ledger_error': False,
                    'accounting': {'attempts': 1},
                    'usage': {'input_tokens': 10, 'output_tokens': 2,
                              'cached_input_tokens': 0}}
        self.assertTrue(live_pilot.reconcile(delivery, upstream)['complete'])
        for change in ({'attempts': {}},
                       {'attempts': {'a': {'state': 'unknown'}}},
                       {'usage': None},
                       {'usage': {'input_tokens': 10}},
                       {'usage': {'input_tokens': 10, 'output_tokens': 2,
                                  'cached_input_tokens': 11}},
                       {'ledger_error': True}):
            changed = dict(upstream, **change)
            self.assertFalse(live_pilot.reconcile(delivery, changed)['complete'])

    def test_one_run_synthetic_provider_no_model(self):
        payload = event({'type': 'response.output_text.delta', 'delta': 'synthetic'})
        payload += completed() + b'data: [DONE]\n\n'
        supplied = []

        def credential():
            supplied.append(True)
            return 'synthetic-token-never-real'

        def fake_runner(argv, cwd, env, remaining):
            self.assertGreater(remaining, 0)
            self.assertEqual(supplied, [])
            home = Path(env['HOME'])
            config = tomllib.loads((home/'config.toml').read_text())
            base = config['model_providers']['local_probe']['base_url']
            port = int(base.split(':')[-1].split('/')[0])
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                connection.request('POST', '/backend-api/codex/responses', BODY,
                                   {'Content-Type': 'application/json'})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), payload)
            finally:
                connection.close()
            command = pilot.diagnostic('packaging', 'quiet',
                                       home.parent/'tools/venv/bin/python')
            trace = '\n'.join(json.dumps({'type': kind}) for kind in
                              ('thread.started', 'turn.started')) + '\n'
            trace += '\n'.join(json.dumps({'type': kind, 'item': {
                'type': 'command_execution', 'id': 'synthetic-command',
                'command': command, 'exit_code': None if kind == 'item.started' else 1,
                'status': 'in_progress' if kind == 'item.started' else 'failed'}})
                              for kind in ('item.started', 'item.completed')) + '\n'
            trace += json.dumps({'type': 'turn.completed'}) + '\n'
            cleanup = {'verified': True, 'remaining_descendants': 0,
                       'open_work_handles': False, 'watch_errors': []}
            return subprocess.CompletedProcess(argv, 0, trace, ''), False, cleanup

        def fake_evaluate(task, evidence, digest):
            self.assertEqual(task, 'packaging')
            self.assertEqual(live_pilot.sha(evidence.read_bytes()), digest)
            root = evidence.parent/'synthetic-quality'
            root.mkdir()
            (root/'report.json').write_text('{"synthetic": true}\n')
            return {'quality': False, 'status': 'fail', 'root': str(root)}

        protocol = json.loads((pilot.HERE/'selftest-protocol.json').read_text())
        row = protocol['schedule'][2]
        with tempfile.TemporaryDirectory(dir=pilot.HERE) as directory:
            with provider(lambda handler: send(handler, payload,
                                               declared=len(payload))) as (factory, requests, errors, _):
                with patch.object(live_pilot, 'evaluate', side_effect=fake_evaluate):
                    result = live_pilot.one_live(row, Path(directory), protocol,
                        credential_supplier=credential,
                        provider_connection_factory=factory, runner=fake_runner)
            self.assertEqual(len(supplied), 1)
            self.assertEqual(len(requests), 1)
            self.assertEqual(errors, [])
            self.assertTrue(result['technical_ok'], result.get('detail'))
            self.assertTrue(result['first_command']['first_command_exact'])
            self.assertTrue(result['reconciliation']['complete'])
            self.assertEqual(result['usage'], {'input_tokens': 100,
                                                'output_tokens': 7,
                                                'cached_input_tokens': 40})
            self.assertFalse(result['quality'])
            self.assertFalse(result['provider_billing_complete'])
            self.assertTrue(result['broker_stopped'])
            self.assertTrue(result['bridge_stopped'])
            self.assertEqual(result['checkpoint']['status'], 'captured')
            saved = (Path(directory)/row['id']/'host-artifacts'/'result.json').read_text()
            self.assertNotIn('synthetic-token-never-real', saved)


if __name__ == '__main__':
    unittest.main()
