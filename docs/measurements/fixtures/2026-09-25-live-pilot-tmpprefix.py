"""Explicit, sequential four-run development screen with host-only credentials.

Importing this module never reads credentials or launches a model. The CLI
requires --execute and a frozen protocol committed and pushed to origin/main.
This is exposed development work, not a held-out efficacy experiment.
"""

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from audit import audit_trace
from broker_route import Broker
from evaluate import checkpoint, evaluate
from host_bridge import HostBridge
from isolation import quote, strict_profile
from pilot import HERE, PUBLIC, WORK, VENV, diagnostic, model_plan, prompt, schedule
from process import run_managed
from protocol_gate import strict_json, validate_local_pins, validate_public_freeze
from runtime_manifest import digest as runtime_digest
from snapshot import capture


GIT = '/Applications/Xcode.app/Contents/Developer/usr/bin/git'
PATH = ('/Applications/Xcode.app/Contents/Developer/usr/bin:'
        '/Applications/ChatGPT.app/Contents/Resources:/usr/bin:/bin:/usr/sbin:/sbin')


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2)+'\n')


def host_access_token() -> str:
    """Called only by the host bridge after an admitted live request."""
    auth = json.loads((Path.home()/'.codex/auth.json').read_text())
    token = auth.get('tokens', {}).get('access_token')
    if not isinstance(token, str) or not token:
        raise ValueError('host access token unavailable')
    return token


def environment(root: Path, work: Path, home: Path, tmp: Path, tools: Path) -> dict:
    return {'HOME':str(home),'CODEX_HOME':str(home),'TMPDIR':str(tmp),
            'TMPPREFIX':str(tmp/'zsh-'),
            'XDG_CACHE_HOME':str(home/'.cache'),'XDG_CONFIG_HOME':str(home/'.config'),
            'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1',
            'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','PYTHONPATH':str(work/'src'),
            'PATH':str(tools/'venv/bin')+':'+PATH,
            'LANG':'en_US.UTF-8','LC_ALL':'en_US.UTF-8','TERM':'dumb',
            'SHELL':'/bin/zsh','ZDOTDIR':str(home),
            'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1',
            'GIT_DIR':str(home/'repo.git'),'GIT_WORK_TREE':str(work),
            'NO_PROXY':'127.0.0.1,localhost','no_proxy':'127.0.0.1,localhost'}


def make_git_baseline(work: Path, home: Path, env: dict) -> None:
    init_env = {key:value for key,value in env.items() if key not in ('GIT_DIR','GIT_WORK_TREE')}
    subprocess.run([GIT,'init','--bare','-q',str(home/'repo.git')],cwd=work,env=init_env,
                   check=True,capture_output=True,timeout=30)
    for argv in ([GIT,'add','-A'],
                 [GIT,'-c','user.name=Experiment','-c','user.email=experiment@example.invalid',
                  '-c','commit.gpgsign=false','commit','-qm','Frozen source baseline']):
        subprocess.run(argv,cwd=work,env=env,check=True,capture_output=True,timeout=30)


def sandbox_canaries(profile: str, work: Path, home: Path, tmp: Path, tools: Path,
                     host: Path, bridge_port: int, broker_port: int, env: dict) -> dict:
    (host/'synthetic-auth-canary').write_text('synthetic-only\n')
    (work/'sandbox-visible').write_text('visible\n')
    (work/'sandbox-escape').symlink_to(host/'synthetic-auth-canary')
    code = r'''
import errno,json,shutil,socket,subprocess,sys,urllib.request
from pathlib import Path
work,home,tmp,tools,host,public,bridge,broker=map(str,sys.argv[1:])
r={}
def denied(path,write=False):
 try:
  if write: Path(path).write_text('forbidden')
  else: Path(path).read_bytes()
 except PermissionError: return True
 return False
r['workspace_read']=Path(work,'sandbox-visible').read_text()=='visible\n'
for name,path in [('host',Path(host,'synthetic-auth-canary')),
                  ('symlink',Path(work,'sandbox-escape')),
                  ('public',Path(public,'README.md')),
                  ('delivery_ledger',Path(host,'delivery.sqlite3')),
                  ('upstream_ledger',Path(host,'upstream.sqlite3'))]:
 r[name+'_read_denied']=denied(path)
r['tools_write_denied']=denied(Path(tools,'forbidden'),True)
r['host_write_denied']=denied(Path(host,'forbidden'),True)
for name,path in [('workspace',work),('home',home),('tmp',tmp)]:
 p=Path(path,'write-canary');p.write_text('ok');r[name+'_write']=p.read_text()=='ok';p.unlink()
r['broker_health']=urllib.request.urlopen('http://127.0.0.1:'+broker+'/health',timeout=3).status==200
try:
 socket.create_connection(('127.0.0.1',int(bridge)),timeout=2)
 r['bridge_network_denied']=False
except OSError as error:
 r['bridge_network_denied']=error.errno in (errno.EPERM,errno.EACCES)
r['rg_available']=bool(shutil.which('rg')) and subprocess.run(['rg','--version'],capture_output=True,timeout=5).returncode==0
r['git_worktree_ready']=subprocess.run(['git','rev-parse','--is-inside-work-tree'],capture_output=True,timeout=5).stdout.strip()==b'true'
print(json.dumps(r))
'''
    try:
        argv = ['/usr/bin/sandbox-exec','-p',profile,str(tools/'venv/bin/python'),'-B',
                '-c',code,str(work),str(home),str(tmp),str(tools),str(host),
                str(PUBLIC),str(bridge_port),str(broker_port)]
        process,timed_out,cleanup = run_managed(argv,work,env,20)
        checks = json.loads(process.stdout) if process.returncode == 0 else {}
        result = {'checks':checks,'exit':process.returncode,'timeout':timed_out,
                  'cleanup':cleanup,'stderr_sha256':sha(process.stderr.encode())}
        save(host/'canaries.json',result)
        if not checks or not all(checks.values()) or timed_out or not cleanup['verified']:
            raise RuntimeError('sandbox canary failure')
        heredoc = (str(tools/'venv/bin/python') +
                   " - <<'PYCODE'\nprint('HEREDOC_OK')\nPYCODE")
        shell, shell_timeout, shell_cleanup = run_managed(
            ['/usr/bin/sandbox-exec', '-p', profile, '/bin/zsh', '-lc', heredoc],
            work, env, 10)
        shell_ok = (shell.returncode == 0 and shell.stdout == 'HEREDOC_OK\n'
                    and not shell_timeout and shell_cleanup['verified'])
        save(host/'heredoc-canary.json',
             {'ok':shell_ok, 'exit_code':shell.returncode,
              'timed_out':shell_timeout, 'cleanup_verified':shell_cleanup['verified'],
              'stderr_sha256':sha(shell.stderr.encode())})
        result['heredoc_ok'] = shell_ok
        save(host/'canaries.json', result)
        if not shell_ok:
            raise RuntimeError('sandbox heredoc canary failure')
        return result
    finally:
        (work/'sandbox-visible').unlink(missing_ok=True)
        (work/'sandbox-escape').unlink(missing_ok=True)


def reconcile(delivery: dict, upstream: dict) -> dict:
    """Count each upstream completion once, including late completions."""
    drows = delivery.get('requests', [])
    brows = upstream.get('attempts', {})
    usage = upstream.get('usage')
    usage_valid = (isinstance(usage, dict) and
                   set(usage) == {'input_tokens', 'output_tokens', 'cached_input_tokens'} and
                   all(type(value) is int and value >= 0 for value in usage.values()) and
                   usage['cached_input_tokens'] <= usage['input_tokens'])
    ids = [row.get('attempt_id') for row in drows]
    if len(ids) != len(set(ids)) or not isinstance(brows, dict):
        return {'complete':False,'reason':'duplicate_or_invalid_attempts','usage':None}
    missing = sorted(set(ids)-set(brows))
    unexpected = sorted(set(brows)-set(ids))
    complete = (bool(ids) and not missing and not unexpected and
                delivery.get('ledger_error') is False and upstream.get('ledger_error') is False and
                delivery.get('accounting',{}).get('attempts') == len(ids) and
                upstream.get('accounting',{}).get('attempts') == len(brows) and
                all(brows[identifier].get('state') == 'completed' for identifier in ids) and
                usage_valid)
    return {'complete':complete,'missing_upstream_attempt_ids':missing,
            'unexpected_upstream_attempt_ids':unexpected,
            'usage':upstream['usage'] if complete else None,
            'provider_billing_complete':False}


def screen_result(rows: list[dict], thresholds: dict) -> dict:
    """Apply the frozen all-or-fail rule to the four assigned runs."""
    expected = {(task, arm) for task in ('click', 'packaging')
                for arm in ('verbose', 'quiet')}
    complete = len(rows) == 4 and {(r.get('task'), r.get('arm')) for r in rows} == expected
    counts = {}
    times = {}
    for row in rows:
        key = (row.get('task'), row.get('arm'))
        usage = row.get('usage')
        if isinstance(usage, dict) and row.get('reconciliation', {}).get('complete') is True:
            inputs, outputs, cached = (usage.get(name) for name in
                                       ('input_tokens', 'output_tokens', 'cached_input_tokens'))
            if all(type(n) is int and n >= 0 for n in (inputs, outputs, cached)) and cached <= inputs:
                counts[key] = inputs + outputs
        seconds = row.get('elapsed_seconds')
        if type(seconds) in (int, float) and math.isfinite(seconds) and seconds > 0:
            times[key] = seconds
    usage_complete = complete and len(counts) == 4
    time_complete = complete and len(times) == 4
    on_tokens = sum(counts.get((task, 'quiet'), 0) for task in ('click', 'packaging'))
    off_tokens = sum(counts.get((task, 'verbose'), 0) for task in ('click', 'packaging'))
    on_seconds = sum(times.get((task, 'quiet'), 0) for task in ('click', 'packaging'))
    off_seconds = sum(times.get((task, 'verbose'), 0) for task in ('click', 'packaging'))
    token_ratio = on_tokens / off_tokens if usage_complete and off_tokens > 0 else None
    time_ratio = on_seconds / off_seconds if time_complete and off_seconds > 0 else None
    gates = {
        'four_assigned_runs': complete,
        'all_technical_ok': complete and all(r.get('technical_ok') is True for r in rows),
        'all_cli_success': complete and all(r.get('exit_code') == 0 and
                                            r.get('timed_out') is False for r in rows),
        'all_four_quality_pass': complete and all(r.get('quality_status') == 'pass' and
                                                 r.get('quality') is True for r in rows),
        'all_four_first_commands_verified': complete and all(
            r.get('first_command', {}).get('first_command_exact') is True for r in rows),
        'all_usage_observed': usage_complete,
        'max_total_token_ratio': token_ratio is not None and
                                 token_ratio <= thresholds['max_total_token_ratio'],
        'on_no_more_tokens_in_each_task': usage_complete and all(
            counts[(task, 'quiet')] <= counts[(task, 'verbose')]
            for task in ('click', 'packaging')),
        'max_wall_time_ratio': time_ratio is not None and
                               time_ratio <= thresholds['max_wall_time_ratio'],
    }
    return {'pass': all(gates.values()), 'gates': gates,
            'token_ratio': token_ratio, 'wall_time_ratio': time_ratio,
            'on_tokens': on_tokens if usage_complete else None,
            'off_tokens': off_tokens if usage_complete else None,
            'on_seconds': on_seconds if time_complete else None,
            'off_seconds': off_seconds if time_complete else None}


def one_live(row: dict, directory: Path, protocol: dict, *,
             credential_supplier=host_access_token, provider_connection_factory=None,
             runner=run_managed) -> dict:
    root = directory/row['id']
    root.mkdir(mode=0o700)
    work,home,tmp,tools,host,agent = (root/name for name in
                                      ('workspace','home','tmp','tools','host-artifacts','agent-artifacts'))
    for path in (home,tmp,tools,host,agent):
        path.mkdir(mode=0o700)
    result = {'id':row['id'],'task':row['task'],'arm':row['arm'],'live':True,
              'quality':None,'usage':None,'provider_billing_complete':False,
              'outcome':'harness_failure'}
    bridge = broker = None
    started = None
    try:
        baseline = capture(WORK/f'prehook-{row["task"]}-dev/fixture',work)
        save(host/'baseline.json',baseline)
        if baseline['tree_sha256'] != protocol['source_tree_sha256'][row['task']]:
            raise ValueError('fixture differs from frozen protocol')
        shutil.copytree(VENV,tools/'venv',symlinks=True)
        if runtime_digest(tools/'venv') != protocol['venv_tree_sha256']:
            raise ValueError('copied runtime differs from frozen protocol')
        env = environment(root,work,home,tmp,tools)
        (home/'.zprofile').write_text('export PATH='+shlex.quote(env['PATH'])+'\n')
        make_git_baseline(work,home,env)
        save(host/'environment.json',env)
        # One absolute deadline covers both transports and the CLI. The bridge
        # may finish an upstream response after the CLI/sidecar disconnects.
        deadline = time.monotonic()+protocol['timeout_seconds']
        key = secrets.token_hex(32)
        bridge = HostBridge(row['id'],key,host/'upstream.sqlite3',credential_supplier,
                            deadline,provider_connection_factory)
        broker = Broker(upstream=f'http://127.0.0.1:{bridge.server_address[1]}',
                        ledger_path=host/'delivery.sqlite3',run_id=row['id'],
                        bridge_key=key,deadline=deadline)
        port = broker.server_address[1]
        profile = strict_profile(root,work,home,tmp,tools,agent,port)
        profile = profile.replace('(deny file-read*)\n','(deny file-read*)\n'+''.join(
            f'(allow file-read* (literal {quote(p)}))\n' for p in root.parents))
        (host/'seatbelt.sb').write_text(profile)
        (home/'config.toml').write_text(
            '[model_providers.local_probe]\nname="Private sidecar"\n'
            f'base_url="http://127.0.0.1:{port}/backend-api/codex"\n'
            'requires_openai_auth=false\nsupports_websockets=false\n'
            'request_max_retries=0\nstream_max_retries=0\n')
        sandbox_canaries(profile,work,home,tmp,tools,host,bridge.server_address[1],port,env)
        command = diagnostic(row['task'],row['arm'],tools/'venv/bin/python')
        message = prompt(row['task'],command)
        (host/'prompt.txt').write_text(message)
        argv = model_plan(profile,message)
        save(host/'model-command.json',argv)
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('deadline before model launch')
        started = time.monotonic()
        process,timed_out,cleanup = runner(argv,work,env,remaining)
        result.update(exit_code=process.returncode,timed_out=timed_out,
                      cli_elapsed_seconds=time.monotonic()-started,cleanup=cleanup,
                      trace_bytes=len(process.stdout.encode()),
                      trace_sha256=sha(process.stdout.encode()),
                      stderr_bytes=len(process.stderr.encode()),
                      first_command=audit_trace(
                          process.stdout, command, expected_exit=1,
                          allow_open_turn=timed_out and cleanup['verified']))
        (host/'trace.jsonl').write_text(process.stdout)
        (host/'stderr.txt').write_text(process.stderr)
        result['checkpoint'] = checkpoint(work,host,cleanup)
        result['source_unchanged'] = result['checkpoint'].get('manifest',{}).get('tree_sha256') == baseline['tree_sha256']
        if result['checkpoint']['status'] != 'captured':
            raise RuntimeError('checkpoint incomplete')
        result['outcome'] = 'timeout' if timed_out else 'worker_completed'
    except BaseException as error:
        result.update(outcome='harness_failure',error=type(error).__name__,detail=str(error))
    finally:
        if broker is not None:
            result['broker_stopped'] = broker.close()
            result['broker'] = broker.summary()
        if bridge is not None:
            while time.monotonic() < bridge.deadline and bridge.summary()['active_handlers']:
                time.sleep(.05)
            result['bridge_stopped'] = bridge.close()
            result['bridge'] = bridge.summary()
        if started is not None:
            # The wall-time gate includes late provider completion/accounting.
            result['elapsed_seconds'] = time.monotonic()-started
        if broker is not None and bridge is not None:
            result['reconciliation'] = reconcile(result['broker'],result['bridge'])
            result['usage'] = result['reconciliation']['usage']
        result['artifacts'] = {str(p.relative_to(host)):sha(p.read_bytes())
                               for p in host.iterdir() if p.is_file()}
        save(host/'result.json',result)
    if result.get('checkpoint',{}).get('status') == 'captured' and \
            result.get('cleanup',{}).get('verified') and \
            result.get('broker_stopped') and result.get('bridge_stopped'):
        evidence = host/'result.json'
        quality = evaluate(row['task'],evidence,sha(evidence.read_bytes()))
        result['quality'] = quality['quality']
        result['quality_status'] = quality['status']
        result['quality_report_root'] = quality['root']
        result['quality_report_sha256'] = sha((Path(quality['root'])/'report.json').read_bytes())
    result['technical_ok'] = (result.get('checkpoint',{}).get('status') == 'captured' and
                              result.get('cleanup',{}).get('verified') is True and
                              result.get('broker_stopped') is True and result.get('bridge_stopped') is True and
                              result.get('reconciliation',{}).get('complete') is True and
                              result.get('quality_status') in ('pass','fail'))
    save(host/'final.json',result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true',required=True)
    parser.add_argument('--protocol',type=Path,required=True)
    args = parser.parse_args()
    os.umask(0o077)
    protocol_path = args.protocol.resolve(strict=True)
    raw = protocol_path.read_bytes()
    value = strict_json(raw)
    schedule(value)
    validate_local_pins(value)
    public_head = validate_public_freeze(protocol_path,raw)
    with (HERE/'execution.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        directory = Path(tempfile.mkdtemp(prefix='live-screen-',dir=HERE))
        (directory/'protocol.json').write_bytes(raw)
        save(directory/'inputs.json',{'protocol_sha256':sha(raw),'public_head':public_head,
              'code_sha256':value['code_sha256'],'start_utc':time.time()})
        results=[]
        for row in value['schedule']:
            item=one_live(row,directory,value)
            results.append(item)
            # Quality/adherence failures stay in the assigned dataset; technical
            # accounting/isolation failures stop further launches without retry.
            if not item['technical_ok']:
                break
        summary={'schema':'solcodex.quiet-model-screen-result.v1',
                 'protocol_sha256':sha(raw),'public_head':public_head,
                 'runs':[{key:item.get(key) for key in
                          ('id','task','arm','outcome','exit_code','timed_out','cli_elapsed_seconds',
                           'elapsed_seconds',
                           'first_command','quality','quality_status','usage','reconciliation',
                           'technical_ok','quality_report_sha256')}
                         for item in results],
                 'unstarted_ids':[row['id'] for row in value['schedule'][len(results):]],
                 'provider_billing_complete':False}
        summary['screen'] = screen_result(results, value['thresholds'])
        save(directory/'summary.json',summary)
        print(json.dumps({'directory':str(directory),'runs':len(results),
                          'unstarted_ids':summary['unstarted_ids'],
                          'technical_ok':[item['technical_ok'] for item in results],
                          'screen_pass':summary['screen']['pass']}))
        return 0 if summary['screen']['pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
