"""No-model real-CLI control for the experimental explicit package."""

from contextlib import contextmanager, ExitStack
import hashlib
import json
from pathlib import Path
import secrets
import shlex
import shutil
from unittest.mock import patch

import cli_toolcall_probe as wire
import live_pilot
import pilot
from process import run_managed


PACKAGE = pilot.PUBLIC / 'experiments/explicit_receipt_profile/plugins/sol-codex-explicit'
VERSION = '0.1.0'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def install(root, home):
    market = home / 'market'
    source = market / 'plugins/sol-codex-explicit'
    source.parent.mkdir(parents=True)
    shutil.copytree(PACKAGE, source)
    listing = market / '.agents/plugins'
    listing.mkdir(parents=True)
    (listing / 'marketplace.json').write_text(json.dumps({
        'name':'sol-codex-explicit','plugins':[{'name':'sol-codex-explicit',
        'source':{'source':'local','path':'./plugins/sol-codex-explicit'},
        'policy':{'installation':'AVAILABLE','authentication':'ON_INSTALL'},
        'category':'Productivity'}]}))
    cache = home / 'plugins/cache/sol-codex-explicit/sol-codex-explicit' / VERSION
    cache.parent.mkdir(parents=True)
    shutil.copytree(source, cache)
    with (home / 'config.toml').open('a') as stream:
        stream.write('\n[marketplaces.sol-codex-explicit]\nsource_type="local"\n')
        stream.write('source=' + json.dumps(str(market)) + '\n')
        stream.write('[plugins."sol-codex-explicit@sol-codex-explicit"]\nenabled=true\n')
    return cache


def run_arm(on):
    marker = 'EVIDENCE_' + secrets.token_hex(12)
    retained = {}
    original_provider = wire.provider
    original_one_live = live_pilot.one_live

    def diagnostic(task, arm, python):
        assert task == 'packaging' and arm == 'quiet'
        root = Path(python).parents[3]
        child = [str(python),'-m','unittest','-v','test_probe']
        if not on:
            return shlex.join(child)
        adapter = (root / 'home/plugins/cache/sol-codex-explicit/'
                   'sol-codex-explicit' / VERSION / 'scripts/receipt_command.py')
        return shlex.join([str(python),str(adapter),'--artifact-dir',
            str(root/'agent-artifacts'),'--timeout-seconds','30','--',*child])

    def runner(argv, work, env, remaining):
        fixture = ("import pathlib,unittest\n"
            "class Probe(unittest.TestCase):\n"
            " def test_output(self):\n"
            "  with pathlib.Path('run-count.log').open('a') as f:f.write('run\\n')\n"
            "  for i in range(120):\n"
            "   print('LINE%03d '%i+'A'*60,flush=True)\n"
            "   if i==5: print(" + repr(marker) + ",flush=True)\n")
        (work/'test_probe.py').write_text(fixture)
        if on:
            cache = install(work.parent, Path(env['CODEX_HOME']))
            retained['cache'] = str(cache)
            index = argv.index('plugins')
            assert argv[index-1] == '--disable'
            argv[index-1] = '--enable'
        return run_managed(argv, work, env, remaining)

    def one_live(row, directory, protocol, **kwargs):
        return original_one_live(row, directory, protocol, runner=runner, **kwargs)

    @contextmanager
    def provider(action):
        with original_provider(action) as values:
            retained['requests'] = values[1]
            yield values

    with ExitStack() as patches:
        patches.enter_context(patch.object(pilot,'diagnostic',side_effect=diagnostic))
        patches.enter_context(patch.object(live_pilot,'diagnostic',side_effect=diagnostic))
        patches.enter_context(patch.object(live_pilot,'one_live',side_effect=one_live))
        patches.enter_context(patch.object(wire,'provider',side_effect=provider))
        summary = wire.run()

    root = Path(summary['private_root']) / '03-packaging-on'
    trace = [json.loads(line) for line in (root/'host-artifacts/trace.jsonl').read_text().splitlines()]
    commands = [event['item'] for event in trace if event.get('type')=='item.completed'
                and event.get('item',{}).get('type')=='command_execution']
    output = commands[0].get('aggregated_output','') if commands else ''
    command_text = commands[0].get('command','') if commands else ''
    requests = retained['requests']
    request_bodies = [raw for _,_,raw in requests]
    receipt = None
    try:
        receipt = json.loads(output) if on else None
    except ValueError:
        pass
    artifact = None
    if receipt and isinstance(receipt.get('path'),str):
        path = Path(receipt['path'])
        if path.is_file() and path.is_relative_to(root/'agent-artifacts'):
            data = path.read_bytes()
            artifact = {'sha256_matches':digest(data)==receipt.get('sha256'),
                        'marker_present':marker.encode() in data,
                        'bytes':len(data)}
    count = root/'workspace/run-count.log'
    package_script = PACKAGE/'scripts/receipt_command.py'
    installed_script = (root/'home/plugins/cache/sol-codex-explicit/'
                        'sol-codex-explicit'/VERSION/'scripts/receipt_command.py')
    return {'scope':'no_model_installed_explicit_package',
        'private_root':summary['private_root'],'arm':'on' if on else 'off',
        'package_sha256':digest((PACKAGE/'.codex-plugin/plugin.json').read_bytes()) if on else None,
        'installed_cache_present':Path(retained['cache']).is_dir() if on else False,
        'installed_script_matches_package':
            digest(installed_script.read_bytes())==digest(package_script.read_bytes()) if on else None,
        'tool_command_uses_installed_script':str(installed_script) in command_text,
        'provider_requests':len(requests),'provider_errors':summary['provider_errors'],
        'request_body_bytes':[len(body) for body in request_bodies],
        'request_marker_flags':[marker.encode() in body for body in request_bodies],
        'request_receipt_flags':[b'solcodex.command-receipt.v1' in body for body in request_bodies],
        'first_request_skill_flag':b'explicit-receipts' in request_bodies[0] if request_bodies else False,
        'trace_sha256':digest((root/'host-artifacts/trace.jsonl').read_bytes()),
        'cli_exit_code':summary['exit_code'],
        'tool_calls':len(commands),'tool_exit_codes':[item.get('exit_code') for item in commands],
        'tool_output_bytes':len(output.encode()),'tool_output_marker':marker in output,
        'child_executions':len(count.read_text().splitlines()) if count.exists() else 0,
        'receipt_status':receipt.get('status') if receipt else None,
        'receipt_complete':receipt.get('capture_complete') if receipt else None,
        'receipt_exit_code':receipt.get('exit_code') if receipt else None,
        'receipt_bytes':receipt.get('bytes') if receipt else None,
        'artifact':artifact,
        'reconciliation_complete':summary['reconciliation_complete'],
        'cleanup_verified':summary['cleanup_verified'],
        'broker_stopped':summary['broker_stopped'],
        'bridge_stopped':summary['bridge_stopped']}


if __name__=='__main__':
    print(json.dumps([run_arm(False),run_arm(True)],sort_keys=True))
