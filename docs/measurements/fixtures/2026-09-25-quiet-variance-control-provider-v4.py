"""Deterministic loopback provider for v4 campaign-path controls only.

This module supplies synthetic responses to the real CLI through HostBridge.
It does not authorize a model run or substitute for a campaign-path verifier.
"""

from __future__ import annotations

import hashlib
import http.client
import http.server
import json
import os
from pathlib import Path
import threading

from pilot import diagnostic, HERE


TOKEN = 'synthetic-token-never-real'
CLICK_PATCH_SHA256 = 'c3dd29662dedbf48068582ae97f8ace13b7933b5dc678d4daf606dae2327f183'
USAGE = ((120, 30, 40), (200, 50, 150), (70, 10, 5))
SUPPORTED_CASES = frozenset({'complete_16', 'quality_fail_continues'})


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_patch(task: str, work: Path) -> str:
    if task == 'packaging':
        return ('*** Begin Patch\n*** Update File: ' + str(work / 'src/packaging/licenses/__init__.py') + '\n'
            '@@\n'
            '-        elif token == "(" and python_tokens and python_tokens[-1] not in {"or", "and"}:\n'
            '+        elif (\n'
            '+            token == "("\n'
            '+            and python_tokens\n'
            '+            and python_tokens[-1] not in {"or", "and", "("}\n'
            '+        ):\n'
            '             message = f"Invalid license expression: {raw_license_expression!r}"\n'
            '*** End Patch')
    if task == 'click':
        path = HERE / 'click_source_only_patch_v3.txt'
        if path.is_symlink() or not path.is_file():
            raise ValueError('control Click patch unavailable')
        raw = path.read_bytes()
        if sha(raw) != CLICK_PATCH_SHA256:
            raise ValueError('control Click patch differs')
        patch = raw.decode('utf-8')
        expected = '*** Update File: src/click/core.py'
        if patch.count(expected) != 1:
            raise ValueError('control Click patch target differs')
        return patch.replace(expected, '*** Update File: ' + str(work / 'src/click/core.py'))
    raise ValueError('unsupported control task')


def rejected_packaging_patch(work: Path) -> str:
    """An attempted repair that accepts nested grouping by losing other semantics."""
    return ('*** Begin Patch\n*** Update File: ' +
            str(work / 'src/packaging/licenses/__init__.py') + '\n'
            '@@\n'
            '-    if not raw_license_expression:\n'
            '+    raw_license_expression = raw_license_expression.replace("(", "").replace(")", "")\n'
            '+    if not raw_license_expression:\n'
            '@@\n'
            '-        elif token == "(" and python_tokens and python_tokens[-1] not in {"or", "and"}:\n'
            '+        elif (\n'
            '+            token == "("\n'
            '+            and python_tokens\n'
            '+            and python_tokens[-1] not in {"or", "and", "("}\n'
            '+        ):\n'
            '             message = f"Invalid license expression: {raw_license_expression!r}"\n'
            '*** End Patch')


def payload(identifier: str, item: dict | None, usage: tuple[int, int, int]) -> bytes:
    def event(value: dict) -> bytes:
        return b'data: ' + json.dumps(value, sort_keys=True,
                                     separators=(',', ':')).encode() + b'\n\n'
    events = []
    if item is not None:
        events += [
            {'type': 'response.output_item.added', 'response_id': identifier,
             'output_index': 0, 'item': {**item, 'status': 'in_progress', 'input': ''}},
            {'type': 'response.output_item.done', 'response_id': identifier,
             'output_index': 0, 'item': item},
        ]
    events.append({'type': 'response.completed', 'response': {
        'id': identifier, 'status': 'completed',
        'output': [item] if item is not None else [],
        'usage': {'input_tokens': usage[0], 'output_tokens': usage[1],
                  'total_tokens': usage[0] + usage[1],
                  'input_tokens_details': {'cached_tokens': usage[2]}}}})
    return b''.join(event(value) for value in events) + b'data: [DONE]\n\n'


def _call(index: int, javascript: str) -> dict:
    return {'id': f'ctc_synthetic_{index}', 'type': 'custom_tool_call',
            'status': 'completed', 'call_id': f'call_synthetic_{index}',
            'namespace': 'functions', 'name': 'exec', 'input': javascript}


class LocalProvider:
    def __init__(self, case: str, row: dict, campaign_root: Path):
        if case not in SUPPORTED_CASES:
            raise ValueError('control case has no implemented provider')
        if row['task'] not in ('click', 'packaging') or row['arm'] not in ('quiet', 'verbose'):
            raise ValueError('unsupported control assignment')
        self.case, self.row, self.campaign_root = case, row, campaign_root
        run = campaign_root / row['id']
        work = run / 'workspace'
        command = diagnostic(row['task'], row['arm'], run / 'tools/venv/bin/python')
        first_js = ('const r=await tools.exec_command({cmd:' + json.dumps(command) +
                    ',workdir:' + json.dumps(str(work)) +
                    ',yield_time_ms:30000,max_output_tokens:700}); text(r.output);')
        rejected = case == 'quality_fail_continues' and row['id'] == '01-b1-packaging-verbose'
        if case == 'quality_fail_continues' and row['id'].startswith('01-') and not rejected:
            raise ValueError('quality-failure control assignment differs')
        self.replies = [payload('resp_synthetic_1', _call(1, first_js), USAGE[0])]
        patch_text = (rejected_packaging_patch(work) if rejected
                      else source_patch(row['task'], work))
        second_js = ('const r=await tools.apply_patch(' + json.dumps(patch_text) +
                     '); text(r);')
        self.replies.append(payload('resp_synthetic_2', _call(2, second_js), USAGE[1]))
        self.replies.append(payload('resp_synthetic_3', None, USAGE[2]))
        self.requests: list[dict] = []
        self.events: list[dict] = []
        self.errors: list[str] = []
        self._lock = threading.RLock()
        self._server = None
        self._thread = None
        self._events_fd = None
        self._events_path = None

    def _record(self, event: dict):
        with self._lock:
            value = {'seq': len(self.events), **event}
            raw = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
            remaining = memoryview(raw)
            while remaining:
                remaining = remaining[os.write(self._events_fd, remaining):]
            os.fsync(self._events_fd)
            self.events.append(value)

    def credential(self) -> str:
        return TOKEN

    def connection(self, host: str, port: int, *, timeout: float):
        if (host, port) != ('chatgpt.com', 443) or self._server is None:
            raise ValueError('control provider route differs')
        return http.client.HTTPConnection(*self._server.server_address, timeout=timeout)

    def __enter__(self):
        owner = self
        output = self.campaign_root / 'control-transcripts'
        if output.is_symlink():
            raise ValueError('control transcript directory is a symlink')
        output.mkdir(mode=0o700, exist_ok=True)
        self._events_path = output / (self.row['id'] + '-events.jsonl')
        self._events_fd = os.open(self._events_path,
                                  os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                                  os.O_APPEND | os.O_NOFOLLOW, 0o600)

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                try:
                    length = int(self.headers.get('Content-Length', '-1'))
                    if (self.path != '/backend-api/codex/responses'
                            or not 0 <= length <= 2_000_000
                            or self.headers.get('Authorization') != 'Bearer ' + TOKEN):
                        self.send_error(400)
                        return
                    body = self.rfile.read(length)
                    with owner._lock:
                        index = len(owner.requests)
                        request = {'path': self.path,
                                   'request_body_utf8': body.decode('utf-8'),
                                   'request_sha256': sha(body)}
                        owner.requests.append(request)
                        owner._record({'kind': 'received', 'response_slot': index,
                                       **request})
                    if index >= len(owner.replies):
                        owner._record({'kind': 'unexpected_request',
                                       'response_slot': index})
                        self.send_error(503)
                        return
                    reply = owner.replies[index]
                    owner._record({'kind': 'send_intent', 'response_slot': index,
                                   'response_id': f'resp_synthetic_{index+1}',
                                   'response_sha256': sha(reply),
                                   'response_sse_utf8': reply.decode('utf-8')})
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Content-Length', str(len(reply)))
                    self.end_headers()
                    self.wfile.write(reply)
                    self.wfile.flush()
                    owner._record({'kind': 'sent', 'response_slot': index,
                                   'response_sha256': sha(reply)})
                except (BrokenPipeError, ConnectionResetError):
                    owner._record({'kind': 'downstream_closed',
                                   'response_slot': locals().get('index')})
                except Exception as error:
                    owner.errors.append(type(error).__name__)
                    owner._record({'kind': 'error', 'response_slot': locals().get('index'),
                                   'error': type(error).__name__})

        self._server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = False
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={'poll_interval': .01})
        self._thread.start()
        return self

    def __exit__(self, _type, _value, _traceback):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)
        os.close(self._events_fd)
        sent_slots = {event['response_slot'] for event in self.events
                      if event['kind'] == 'sent'}
        sent_replies = [reply for index, reply in enumerate(self.replies)
                        if index in sent_slots]
        transcript = {'schema': 'solcodex.local-control-provider-transcript.v4',
                      'case': self.case, 'id': self.row['id'],
                      'requests': self.requests,
                      'events_sha256': sha(self._events_path.read_bytes()),
                      'events': self.events,
                      'response_sse_utf8': [reply.decode('utf-8')
                                            for reply in sent_replies],
                      'response_sha256': [sha(reply) for reply in sent_replies],
                      'errors': self.errors,
                      'server_stopped': not self._thread.is_alive()}
        output = self._events_path.parent
        with (output / (self.row['id'] + '.json')).open('x') as stream:
            json.dump(transcript, stream, sort_keys=True, separators=(',', ':'))
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if self._thread.is_alive() or self.errors:
            raise RuntimeError('local control provider failed')
