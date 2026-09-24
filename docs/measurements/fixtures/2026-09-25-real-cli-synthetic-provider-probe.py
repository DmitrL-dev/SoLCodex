"""Exploratory no-model Codex CLI tool-call shape probe.

Provider replies and usage are synthetic. This file does not qualify the
measurement path or authorize a model experiment.
"""

import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch
import hashlib
import sys

import live_pilot
import pilot
from test_host_bridge import event, provider, send


def response(identifier, output, usage):
    return {'id':identifier,'status':'completed','output':output,
            'usage':{'input_tokens':usage[0],'output_tokens':usage[1],
                     'total_tokens':usage[0]+usage[1],
                     'input_tokens_details':{'cached_tokens':usage[2]}}}


def payload(identifier, item, usage):
    events=[]
    if item is not None:
        added=dict(item,status='in_progress',input='')
        events.extend((
            {'type':'response.output_item.added','response_id':identifier,
             'output_index':0,'item':added},
            {'type':'response.output_item.done','response_id':identifier,
             'output_index':0,'item':item}))
    events.append({'type':'response.completed',
                   'response':response(identifier,[item] if item else [],usage)})
    return b''.join(event(value) for value in events)+b'data: [DONE]\n\n'


def run():
    os.umask(0o077)
    directory=Path(tempfile.mkdtemp(prefix='cli-toolcall-',dir=pilot.HERE))
    protocol=json.loads((pilot.HERE/'selftest-protocol.json').read_text())
    protocol['timeout_seconds']=90
    row=protocol['schedule'][2]
    assert row['task']=='packaging' and row['arm']=='quiet'
    root=directory/row['id']
    work=root/'workspace'
    command=pilot.diagnostic('packaging','quiet',root/'tools/venv/bin/python')
    js=('const r=await tools.exec_command({cmd:'+json.dumps(command)+
        ',workdir:'+json.dumps(str(work))+',yield_time_ms:30000,max_output_tokens:700}); text(r.output);')
    call={'id':'ctc_synthetic_1','type':'custom_tool_call','status':'completed',
          'call_id':'call_synthetic_1','namespace':'functions','name':'exec','input':js}
    first=payload('resp_synthetic_1',call,(120,30,40))
    second=payload('resp_synthetic_2',None,(200,50,150))
    count=0
    count_lock=threading.Lock()
    def action(handler):
        nonlocal count
        with count_lock:
            count+=1
            index=count
        if index==1:
            send(handler,first,declared=len(first))
        elif index==2:
            send(handler,second,declared=len(second))
        else:
            raise ValueError('unexpected provider request')
    original_plan=live_pilot.model_plan
    def suppressed_plan(profile,message):
        argv=original_plan(profile,message)
        argv[-1:-1]=['-c','suppress_unstable_features_warning=true']
        return argv
    with provider(action) as (factory,requests,errors,_):
        with patch.object(live_pilot,'model_plan',side_effect=suppressed_plan):
            result=live_pilot.one_live(row,directory,protocol,
                credential_supplier=lambda:'synthetic-token-never-real',
                provider_connection_factory=factory)
    request_items=[]
    for _,_,raw in requests:
        body=json.loads(raw)
        request_items.append([{'type':item.get('type'),
                               'call_id':item.get('call_id')}
                              for item in body.get('input',[])
                              if isinstance(item,dict) and
                              item.get('type') in ('custom_tool_call',
                                                   'custom_tool_call_output')])
    trace=(root/'host-artifacts/trace.jsonl').read_text() if (root/'host-artifacts/trace.jsonl').exists() else ''
    shapes=[]
    diagnostic_signature=False
    for line in trace.splitlines():
        try:
            value=json.loads(line)
        except ValueError:
            shapes.append(('malformed',None,None))
            continue
        item=value.get('item') or {}
        shapes.append((value.get('type'),item.get('type'),item.get('status')))
        if value.get('type')=='item.completed' and item.get('type')=='command_execution':
            output=item.get('aggregated_output','')
            diagnostic_signature=('1 failed, 290 passed' in output and
                'FAILED tests/test_metadata.py::test_nested_spdx_regression_probe' in output)
    host=root/'host-artifacts'
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    summary={'scope':'exploratory_synthetic_provider_real_cli',
             'private_root':str(directory),'python_version':sys.version.split()[0],
             'cli_binary_sha256':digest(Path('/Applications/ChatGPT.app/Contents/Resources/codex')),
             'source_tree_sha256':protocol['source_tree_sha256']['packaging'],
             'runtime_tree_sha256':protocol['venv_tree_sha256'],
             'controller_sha256':digest(Path(__file__)),
             'trace_sha256':digest(host/'trace.jsonl'),
             'diagnostic_signature':diagnostic_signature,
             'request_items':request_items,
             'provider_requests':len(requests),'provider_errors':len(errors),
             'exit_code':result.get('exit_code'),'timed_out':result.get('timed_out'),
             'cleanup_verified':result.get('cleanup',{}).get('verified'),
             'first_command':result.get('first_command'),
             'trace_event_shapes':shapes,
             'checkpoint_status':result.get('checkpoint',{}).get('status'),
             'checkpoint_tree_sha256':result.get('checkpoint',{}).get('manifest',{}).get('tree_sha256'),
             'source_unchanged':result.get('source_unchanged'),
             'reconciliation_complete':result.get('reconciliation',{}).get('complete'),
             'usage':result.get('usage'),
             'quality_status':result.get('quality_status'),
             'quality_report_sha256':result.get('quality_report_sha256'),
             'technical_ok':result.get('technical_ok'),
             'broker_stopped':result.get('broker_stopped'),
             'bridge_stopped':result.get('bridge_stopped')}
    (directory/'probe-summary.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n')
    return summary


if __name__=='__main__':
    result=run()
    print(json.dumps(result,sort_keys=True))
