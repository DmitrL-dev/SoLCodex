"""No-model development probe joining transport, checkpoint and real evaluator.

The controlled child runs under the pilot's Seatbelt profile and process
supervisor. It is not Codex CLI, and its code-mode trace is synthetic.
"""

import hashlib
import fcntl
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import evaluate
import live_pilot
import pilot
import runtime_manifest
from test_host_bridge import BODY, event, provider, send
from snapshot import capture, manifest


CASES = (
    ('gold_complete', 'gold', True, False),
    ('wrong_complete', 'wrong', True, False),
    ('gold_missing_usage', 'gold', False, False),
    ('gold_cleanup_failure', 'gold', True, True),
)
GOLD = pilot.HERE.parent / 'packaging-928-gold/src/packaging/licenses/__init__.py'
OLD = b'    if not raw_license_expression:'
NEW = (b'    raw_license_expression = raw_license_expression.replace("(", "").replace(")", "")\n'
       b'    if not raw_license_expression:')
TREES = {
    'gold': 'ad9f932ad4671fa1c3d080fc96bc042722c88d6bdfd06bf15ed7590b7841d62d',
    'wrong': '607c7f277ab7b7ff9bbe94a77feb5e6aee3f3e131cc3abc63d06d71d14b509b5',
}

CHILD = r'''
import http.client,json,os,resource,shlex,socket,struct,subprocess,sys,tomllib,time
from pathlib import Path

patch_file,body_file,command,artifacts=sys.argv[1:]
work=Path.cwd(); artifacts=Path(artifacts)
limit=1024*1024
def cap_files(): resource.setrlimit(resource.RLIMIT_FSIZE,(limit,limit))
with (artifacts/'diagnostic.stdout').open('wb') as out, (artifacts/'diagnostic.stderr').open('wb') as err:
    diagnostic=subprocess.run(shlex.split(command),cwd=work,env=os.environ,
                              stdout=out,stderr=err,timeout=45,preexec_fn=cap_files)
stdout=(artifacts/'diagnostic.stdout').read_bytes()
stderr=(artifacts/'diagnostic.stderr').read_bytes()
(artifacts/'diagnostic.json').write_text(json.dumps({'exit_code':diagnostic.returncode,
    'command':command,'stdout_bytes':len(stdout),'stderr_bytes':len(stderr),
    'baseline_probe_complete':b'1 failed, 290 passed' in stdout and
        b'FAILED tests/test_metadata.py::test_nested_spdx_regression_probe' in stdout}))
if (diagnostic.returncode!=1 or len(stdout)>=limit or len(stderr)>=limit or
    b'1 failed, 290 passed' not in stdout or
    b'FAILED tests/test_metadata.py::test_nested_spdx_regression_probe' not in stdout):
    raise SystemExit(31)
config=tomllib.loads((Path(os.environ['HOME'])/'config.toml').read_text())
base=config['model_providers']['local_probe']['base_url']
port=int(base.split(':')[-1].split('/')[0]); body=Path(body_file).read_bytes()
first=socket.create_connection(('127.0.0.1',port),timeout=5)
try:
    first.settimeout(5)
    first.sendall(b'POST /backend-api/codex/responses HTTP/1.1\r\n'
        b'Host: 127.0.0.1\r\nContent-Type: application/json\r\n'
        +f'Content-Length: {len(body)}\r\n\r\n'.encode()+body)
    received=b''
    while b'synthetic-first' not in received:
        chunk=first.recv(4096)
        if not chunk: raise EOFError('first response ended before synthetic delta')
        received+=chunk
        if len(received)>65536: raise RuntimeError('first response exceeded bound')
    first.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
finally: first.close()
second=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
try:
    second.request('POST','/backend-api/codex/responses',body,
                   {'Content-Type':'application/json'})
    response=second.getresponse()
    payload=response.read(65537)
    if response.status!=200 or len(payload)>65536 or b'provider-second' not in payload:
        raise RuntimeError('second response incomplete')
finally: second.close()
target=work/'src/packaging/licenses/__init__.py'
target.write_bytes(Path(patch_file).read_bytes())
events=[
    {'type':'thread.started'}, {'type':'turn.started'},
    {'type':'item.started','item':{'type':'command_execution','id':'diagnostic',
                                   'command':command}},
    {'type':'item.completed','item':{'type':'command_execution','id':'diagnostic',
                                     'command':command,'status':'failed','exit_code':1}},
    {'type':'item.started','item':{'type':'file_change','id':'patch'}},
    {'type':'item.completed','item':{'type':'file_change','id':'patch'}},
    {'type':'turn.completed'},
]
for item in events: print(json.dumps(item,sort_keys=True),flush=True)
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def patch_bytes(variant):
    raw = GOLD.read_bytes()
    if raw.count(OLD) != 1:
        raise ValueError('gold source changed')
    return raw if variant == 'gold' else raw.replace(OLD, NEW)


def preflight(protocol):
    row=protocol['schedule'][2]
    if row['task']!='packaging' or row['arm']!='quiet':
        raise ValueError('wrong synthetic schedule row')
    baseline=pilot.WORK/'prehook-packaging-dev/fixture'
    if manifest(baseline)['tree_sha256']!=protocol['source_tree_sha256']['packaging']:
        raise ValueError('baseline differs from frozen protocol')
    with tempfile.TemporaryDirectory(dir=pilot.HERE) as directory:
        source=Path(directory)/'source'
        for variant in ('gold','wrong'):
            capture(baseline,source)
            (source/'src/packaging/licenses/__init__.py').write_bytes(patch_bytes(variant))
            if manifest(source)['tree_sha256']!=TREES[variant]:
                raise ValueError('candidate differs from frozen tree: '+variant)
            shutil.rmtree(source)
    return row


def preflight_pins(protocol):
    expected_path = (pilot.PUBLIC/'docs/research/data/'
                     '2026-09-25-integrated-checkpoint-expectations.json')
    raw = expected_path.read_bytes()
    expected = json.loads(raw)
    if (expected.get('schema')!='solcodex.integrated-checkpoint-expectations.v1' or
        expected.get('model_run_authorized') is not False or
        expected.get('case_order') != [name for name,_,_,_ in CASES]):
        raise ValueError('invalid frozen integration plan')
    local = ('audit.py','broker_route.py','evaluate.py','host_bridge.py',
             'integrated_checkpoint_probe.py','isolation.py','live_pilot.py',
             'pilot.py','process.py','protocol_gate.py','runtime_manifest.py',
             'safe_tree.py','selftest-protocol.json','snapshot.py',
             'test_host_bridge.py','quality-assets/lock.json',
             'quality-assets/verify_packaging.py')
    observed = {name:sha((pilot.HERE/name).read_bytes()) for name in local}
    observed.update({
        'controlled_child':sha(CHILD.encode()),
        'gold_patch':sha(patch_bytes('gold')),
        'wrong_patch':sha(patch_bytes('wrong')),
        'usage_attempt_ledger.py':sha((pilot.PUBLIC/'scripts/usage_attempt_ledger.py').read_bytes()),
        'reducer':sha((pilot.PUBLIC/'scripts/reduce_integrated_checkpoint_probe.py').read_bytes()),
    })
    if observed != expected['source_sha256']:
        raise ValueError('executed source differs from frozen plan')
    public_fixture=(pilot.PUBLIC/'docs/measurements/fixtures/'
                    '2026-09-25-integrated-checkpoint-probe.py')
    if sha(public_fixture.read_bytes()) != observed['integrated_checkpoint_probe.py']:
        raise ValueError('published fixture differs from executed controller')
    runtime = {
        'host_python':'.'.join(map(str,sys.version_info[:3])),
        'pilot_venv_tree_sha256':runtime_manifest.digest(pilot.VENV),
        'evaluator_venv_tree_sha256':evaluate.runtime_digest(pilot.VENV),
        'codex_cli':'not_executed',
    }
    if runtime != expected['runtime']:
        raise ValueError('runtime differs from frozen plan')
    preflight(protocol)
    return expected,raw,observed,runtime


def run_case(name, variant, first_usage, inject_cleanup_failure, root, protocol):
    child_done = threading.Event()
    action_lock = threading.Lock()
    times = {}
    requests_seen = 0
    first_response = {'id':'provider-first','status':'completed'}
    if first_usage:
        first_response['usage'] = {'input_tokens':120,'output_tokens':30,
            'total_tokens':150,'input_tokens_details':{'cached_tokens':40}}
    first_payload = event({'type':'response.completed','response':first_response}) + b'data: [DONE]\n\n'
    second_payload = event({'type':'response.completed','response':{
        'id':'provider-second','status':'completed','usage':{
            'input_tokens':200,'output_tokens':50,'total_tokens':250,
            'input_tokens_details':{'cached_tokens':150}}}}) + b'data: [DONE]\n\n'

    def action(handler):
        nonlocal requests_seen
        with action_lock:
            requests_seen += 1
            index = requests_seen
        if index == 1:
            handler.send_response(200)
            handler.send_header('Content-Type','text/event-stream')
            handler.end_headers()
            handler.wfile.write(event({'type':'response.output_text.delta',
                                       'delta':'synthetic-first'}))
            handler.wfile.flush()
            if not child_done.wait(30):
                raise TimeoutError('controlled child did not finish')
            time.sleep(.25)
            times['first_upstream_completion_sent'] = time.monotonic()
            handler.wfile.write(first_payload)
            handler.wfile.flush()
        elif index == 2:
            send(handler,second_payload,declared=len(second_payload))
        else:
            raise AssertionError('unexpected third request')

    row = preflight(protocol)
    case_root = root/name
    case_root.mkdir(mode=0o700)
    true_cleanup = {}
    evaluator_calls = []

    def runner(argv, cwd, env, remaining):
        tools = cwd.parent/'tools'
        artifacts = cwd.parent/'agent-artifacts'
        child = tools/'controlled-child.py'
        patch_file = tools/'candidate-source.bin'
        body_file = tools/'synthetic-body.json'
        child.write_text(CHILD)
        patch_file.write_bytes(patch_bytes(variant))
        body_file.write_bytes(BODY)
        command = pilot.diagnostic('packaging','quiet',tools/'venv/bin/python')
        if argv[:3] != ['/usr/bin/sandbox-exec','-p',argv[2]]:
            raise ValueError('unexpected worker command envelope')
        controlled = argv[:3] + [str(tools/'venv/bin/python'),'-B',str(child),
                                 str(patch_file),str(body_file),command,str(artifacts)]
        try:
            process,timed_out,cleanup = live_pilot.run_managed(controlled,cwd,env,min(remaining,90))
            true_cleanup.update(cleanup)
            if cleanup.get('verified') is True:
                true_cleanup['candidate_tree_sha256'] = manifest(cwd)['tree_sha256']
            if inject_cleanup_failure:
                cleanup = dict(cleanup,verified=False,watch_errors=['injected_cleanup_uncertainty'])
            return process,timed_out,cleanup
        finally:
            times['runner_finished'] = time.monotonic()
            child_done.set()

    real_evaluate = live_pilot.evaluate
    def counted_evaluate(*args, **kwargs):
        evaluator_calls.append(True)
        return real_evaluate(*args, **kwargs)
    with provider(action) as (factory, requests, errors, _):
        with patch.object(live_pilot,'evaluate',side_effect=counted_evaluate):
            result = live_pilot.one_live(row,case_root,protocol,
                credential_supplier=lambda:'synthetic-token-never-real',
                provider_connection_factory=factory,runner=runner)
    if errors:
        raise RuntimeError('synthetic provider errors: '+str(len(errors)))
    artifact_dir = case_root/row['id']/'agent-artifacts'
    diagnostic = json.loads((artifact_dir/'diagnostic.json').read_text())
    full = case_root/row['id']/'host-artifacts/final.json'
    canary_record = json.loads((case_root/row['id']/'host-artifacts/canaries.json').read_text())
    quality_root = result.get('quality_report_root')
    quality = json.loads((Path(quality_root)/'report.json').read_text()) if quality_root else None
    broker_rows = result.get('broker',{}).get('requests',[])
    bridge_rows = result.get('bridge',{}).get('attempts',{})
    upstream = quality.get('upstream') if quality else None
    failed_calls = sorted({item['nodeid'] for item in upstream['reports']
                           if item['outcome']=='failed' and item['when']=='call'}) if upstream else None
    summary = {
        'case':name,'scope':'synthetic_no_model_controlled_child',
        'runner_exit':result.get('exit_code'),'runner_timed_out':result.get('timed_out'),
        'real_supervisor_cleanup_verified':true_cleanup.get('verified'),
        'supervisor_candidate_tree_sha256':true_cleanup.get('candidate_tree_sha256'),
        'injected_cleanup_failure':inject_cleanup_failure,
        'reported_cleanup_verified':result.get('cleanup',{}).get('verified'),
        'diagnostic_exit':diagnostic['exit_code'],
        'diagnostic_command_exact':diagnostic['command']==pilot.diagnostic('packaging','quiet',
            case_root/row['id']/'tools/venv/bin/python'),
        'diagnostic_baseline_probe_complete':diagnostic['baseline_probe_complete'],
        'diagnostic_stdout_bytes':diagnostic['stdout_bytes'],
        'diagnostic_stderr_bytes':diagnostic['stderr_bytes'],
        'diagnostic_stdout_sha256':sha((artifact_dir/'diagnostic.stdout').read_bytes()),
        'diagnostic_stderr_sha256':sha((artifact_dir/'diagnostic.stderr').read_bytes()),
        'first_command':result.get('first_command'),
        'provider_requests':len(requests),
        'provider_errors':len(errors),
        'runner_before_first_upstream_completion':
            times.get('runner_finished',float('inf')) < times.get('first_upstream_completion_sent',float('-inf')),
        'broker_attempts':len(broker_rows),
        'distinct_attempt_ids':len({item['attempt_id'] for item in broker_rows})==2,
        'broker_bridge_attempt_ids_match':{item['attempt_id'] for item in broker_rows}==set(bridge_rows),
        'broker_first_delivery_failed':bool(broker_rows[0].get('error')) and broker_rows[0].get('completed') is False if len(broker_rows)>0 else False,
        'broker_second_delivery_complete':broker_rows[1].get('completed') is True if len(broker_rows)>1 else False,
        'upstream_attempts':result.get('bridge',{}).get('accounting',{}).get('attempts'),
        'upstream_states':result.get('bridge',{}).get('accounting',{}).get('states'),
        'usage':result.get('usage'),
        'observed_completed_usage':result.get('bridge',{}).get('accounting',{}).get('observed_completed_usage'),
        'reconciliation_complete':result.get('reconciliation',{}).get('complete'),
        'checkpoint_status':result.get('checkpoint',{}).get('status'),
        'checkpoint_tree_sha256':result.get('checkpoint',{}).get('manifest',{}).get('tree_sha256'),
        'expected_candidate_tree_sha256':TREES[variant],
        'source_unchanged_from_parent':result.get('source_unchanged') if
            result.get('checkpoint',{}).get('status')=='captured' else None,
        'quality_status':result.get('quality_status'),
        'quality':result.get('quality'),
        'evaluator_call_count':len(evaluator_calls),
        'evaluator_called':bool(evaluator_calls),
        'evaluator_report_present':quality is not None,
        'behavior_passed':quality.get('behavior_passed') if quality else None,
        'behavior_total':quality.get('behavior_total') if quality else None,
        'behavior_case_pass_vector':[item.get('passed') for item in quality['cases']] if quality else None,
        'evaluator_upstream_exit':quality.get('upstream_process',{}).get('exit_code') if quality else None,
        'evaluator_upstream_inventory_sha256':upstream.get('inventory_sha256') if upstream else None,
        'evaluator_upstream_unexpected_skips':upstream.get('unexpected_skips') if upstream else None,
        'evaluator_upstream_failed_call_count':len(failed_calls) if failed_calls is not None else None,
        'evaluator_upstream_failed_call_ids_sha256':sha(json.dumps(failed_calls,separators=(',',':')).encode()) if failed_calls is not None else None,
        'evaluator_canaries':quality.get('canaries') if quality else None,
        'evaluator_source_unchanged':quality.get('restored_source_unchanged') if quality else None,
        'worker_sandbox_canaries_pass':bool(canary_record.get('checks')) and
            all(value is True for value in canary_record['checks'].values()),
        'technical_ok':result.get('technical_ok'),
        'broker_stopped':result.get('broker_stopped'),
        'bridge_stopped':result.get('bridge_stopped'),
        'provider_billing_complete':result.get('provider_billing_complete'),
        'private_final_sha256':sha(full.read_bytes()),
        'private_quality_report_sha256':sha((Path(quality_root)/'report.json').read_bytes()) if quality_root else None,
    }
    (case_root/'probe-summary.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n')
    return summary


def main():
    protocol = json.loads((pilot.HERE/'selftest-protocol.json').read_text())
    pilot.schedule(protocol)
    expected, expected_raw, observed_sources, observed_runtime = preflight_pins(protocol)
    with (pilot.HERE/'execution.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        root=Path(tempfile.mkdtemp(prefix='integrated-checkpoint-',dir=pilot.HERE))
        results=[]
        for name,variant,first_usage,cleanup_failure in CASES:
            results.append(run_case(name,variant,first_usage,cleanup_failure,root,protocol))
            (root/'partial-results.json').write_text(json.dumps(results,sort_keys=True,indent=2)+'\n')
            print(json.dumps({'case':name,'technical_ok':results[-1]['technical_ok'],
                              'quality':results[-1]['quality'],'usage':results[-1]['usage']}),flush=True)
            if not results[-1]['real_supervisor_cleanup_verified']:
                raise RuntimeError('actual supervisor cleanup uncertainty; stop campaign')
        sys.path.insert(0,str(pilot.PUBLIC/'scripts'))
        from reduce_integrated_checkpoint_probe import reduce
        envelope = {
            'schema':'solcodex.integrated-checkpoint-observations.v1',
            'scope':'synthetic_development_no_model',
            'expected_sha256':sha(expected_raw),
            'source_sha256':observed_sources,
            'observed_runtime':observed_runtime,
            'cases':results,
            'frozen_acceptance_pass':False,
        }
        decision = reduce(expected,envelope,expected_raw,require_declared=False)
        envelope['frozen_acceptance_pass'] = decision['synthetic_integration_pass']
        if reduce(expected,envelope,expected_raw) != decision:
            raise RuntimeError('frozen reducer disagrees with recorded decision')
        (root/'results.json').write_text(json.dumps(envelope,sort_keys=True,indent=2)+'\n')
        print(json.dumps({'private_result_root':str(root),'cases':len(results)}))
        if not decision['synthetic_integration_pass']:
            raise SystemExit(1)


if __name__=='__main__':
    main()
