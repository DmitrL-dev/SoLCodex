"""Private behavior verifier for the historical Click managed-resource incident."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('source',type=Path)
args=parser.parse_args()
sys.path.insert(0,str(args.source.resolve()/'src'))
import click
from click.testing import CliRunner

class Boom(Exception):
    pass
class Resource:
    def __init__(self, label='one', suppress=False, log=None):
        self.label=label
        self.suppress=suppress
        self.log=log if log is not None else []
        self.events=[]
    def __enter__(self):
        return self
    def __exit__(self, typ, value, tb):
        self.events.append((typ,value,tb))
        self.log.append(self.label)
        return self.suppress

def context():
    return click.Context(click.Command('test'))
def raised(body):
    try:
        body()
    except Boom as error:
        return error
    return None

def direct_exception():
    ctx=context(); res=Resource(); cause=Boom('direct')
    def body():
        with ctx:
            ctx.with_resource(res)
            raise cause
    assert raised(body) is cause
    typ,value,tb=res.events[0]
    assert typ is Boom and value is cause and tb is not None and tb.tb_frame.f_code.co_name == 'body'

def scope_exception():
    ctx=context(); res=Resource(); cause=Boom('scope')
    def body():
        with ctx.scope():
            ctx.with_resource(res)
            raise cause
    assert raised(body) is cause
    typ,value,tb=res.events[0]
    assert typ is Boom and value is cause and tb is not None

def nested_inner_suppresses():
    ctx=context(); log=[]; outer=Resource('outer',False,log); inner=Resource('inner',True,log)
    with ctx.scope():
        ctx.with_resource(outer); ctx.with_resource(inner)
        raise Boom('suppressed')
    assert log==['inner','outer']
    assert inner.events[0][0] is Boom and outer.events[0][0] is None

def nested_outer_suppresses():
    ctx=context(); log=[]; outer=Resource('outer',True,log); inner=Resource('inner',False,log)
    with ctx.scope():
        ctx.with_resource(outer); ctx.with_resource(inner)
        raise Boom('suppressed')
    assert log==['inner','outer']
    assert inner.events[0][0] is Boom and outer.events[0][0] is Boom

def nested_unsuppressed():
    ctx=context(); log=[]; outer=Resource('outer',False,log); inner=Resource('inner',False,log)
    cause=Boom('propagate')
    def body():
        with ctx.scope():
            ctx.with_resource(outer); ctx.with_resource(inner)
            raise cause
    assert raised(body) is cause
    assert log==['inner','outer']
    assert inner.events[0][1] is cause and outer.events[0][1] is cause

def cli_transaction_rollback():
    log=[]
    class Transaction:
        def __enter__(self):
            log.append('begin'); return self
        def __exit__(self, typ, value, tb):
            log.append('rollback' if typ else 'commit')
    @click.group()
    @click.pass_context
    def cli(ctx):
        ctx.obj=ctx.with_resource(Transaction())
    @cli.command()
    def fail():
        raise Boom('failure')
    result=CliRunner().invoke(cli,['fail'])
    assert isinstance(result.exception,Boom)
    assert log==['begin','rollback']

def successful_exit():
    ctx=context(); res=Resource()
    with ctx.scope():
        ctx.with_resource(res)
    assert len(res.events)==1 and res.events[0]==(None,None,None)

def explicit_close():
    ctx=context(); res=Resource()
    ctx.with_resource(res)
    assert res.events==[]
    ctx.close()
    assert len(res.events)==1 and res.events[0]==(None,None,None)

def context_reuse():
    ctx=context(); first=Resource('first'); second=Resource('second')
    ctx.with_resource(first); ctx.close(); ctx.with_resource(second); ctx.close(); ctx.close()
    assert len(first.events)==len(second.events)==1

cases=[direct_exception,scope_exception,nested_inner_suppresses,nested_outer_suppresses,nested_unsuppressed,cli_transaction_rollback,successful_exit,explicit_close,context_reuse]
passed=0
for case in cases:
    try:
        case(); print(case.__name__+': PASS'); passed+=1
    except Exception as error:
        print(case.__name__+': FAIL ('+type(error).__name__+')')
print(f'TOTAL {passed}/{len(cases)}')
sys.exit(0 if passed==len(cases) else 1)
