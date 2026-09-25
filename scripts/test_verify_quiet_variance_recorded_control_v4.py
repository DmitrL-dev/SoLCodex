"""Reject coherent forgeries of the compact recorded-evidence bundle."""

import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from verify_quiet_variance_recorded_control_v4 import tree_manifest, verify


PUBLIC = Path(__file__).resolve().parent.parent
ARCHIVE = PUBLIC / 'docs/measurements/data/2026-09-25-quiet-variance-v4r2-quality-fail-block1-evidence.tar.gz'
ARCHIVE_SHA256 = 'be69c455e5d3b357ab211f7122ad9aa3acf76d93f7ad9978cf2dd551d982d378'
PLAN_RELATIVE = 'docs/research/data/2026-09-25-quiet-variance-bundle-v4-campaign-expectations.json'
FROZEN_HEAD = '8a54fd2df741c6cadb9eea83a0417bb45cc12c53'
FROZEN_PLAN_SHA256 = '41c1dd959a08dac7a215dd9959d9abf69815463af2b931f524269b8cee635196'
ROW = '01-b1-packaging-verbose'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(root, relative):
    return json.loads((root / relative).read_bytes())


def write(root, relative, value):
    raw = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    (root / relative).write_bytes(raw)


def update_manifest(root, names):
    manifest = read(root, 'manifest.json')
    for name in names:
        raw = (root / name).read_bytes()
        manifest['entries'][name]['sha256'] = sha(raw)
        manifest['entries'][name]['bytes'] = len(raw)
    if 'journal.jsonl' in names:
        manifest['journal_sha256'] = sha((root / 'journal.jsonl').read_bytes())
    write(root, 'manifest.json', manifest)


def forge_host(root, mutation, extra_host_files=(), extra_quality_files=(),
               extra_snapshot_files=()):
    prefix = ROW + '/host-artifacts/'
    final = read(root, prefix + 'final.json')
    result = read(root, prefix + 'result.json')
    report = read(root, 'quality/' + ROW + '/report.json')
    mutation(final, result, report)
    for name in extra_quality_files:
        report['artifact_hashes']['host/' + name] = sha(
            (root / 'quality' / ROW / 'host' / name).read_bytes())
    for name in extra_host_files:
        observed = sha((root / (prefix + name)).read_bytes())
        final['artifacts'][name] = observed
        result['artifacts'][name] = observed
    write(root, prefix + 'result.json', result)
    report['evidence_sha256'] = sha((root / (prefix + 'result.json')).read_bytes())
    write(root, 'quality/' + ROW + '/report.json', report)
    report_hash = sha((root / 'quality' / ROW / 'report.json').read_bytes())
    final['quality_report_sha256'] = report_hash
    write(root, prefix + 'final.json', final)
    seal = read(root, ROW + '/seal.json')
    seal['host_files']['final.json'] = sha((root / (prefix + 'final.json')).read_bytes())
    seal['host_files']['result.json'] = sha((root / (prefix + 'result.json')).read_bytes())
    for name in extra_host_files:
        seal['host_files'][name] = sha((root / (prefix + name)).read_bytes())
    seal['quality_report_sha256'] = report_hash
    if extra_snapshot_files:
        seal['checkpoint_tree_sha256'] = final['checkpoint']['manifest'][
            'tree_sha256']
    for name in extra_quality_files:
        seal['quality_report_artifacts']['host/' + name] = report[
            'artifact_hashes']['host/' + name]
    write(root, ROW + '/seal.json', seal)
    journal = [json.loads(line) for line in (root / 'journal.jsonl').read_text().splitlines()]
    journal[1]['final_sha256'] = seal['host_files']['final.json']
    journal[1]['seal_sha256'] = sha((root / ROW / 'seal.json').read_bytes())
    journal[1]['quality_status'] = final['quality_status']
    (root / 'journal.jsonl').write_text(''.join(
        json.dumps(event, sort_keys=True, separators=(',', ':')) + '\n'
        for event in journal))
    export = read(root, 'analysis-export.json')
    export['slots'][0]['accounting_evidence_sha256'] = journal[1]['seal_sha256']
    export['slots'][0]['quality'] = final['quality']
    export['slots'][0]['elapsed_seconds'] = final['elapsed_seconds']
    export['slots'][0]['timed_out'] = final['timed_out']
    write(root, 'analysis-export.json', export)
    update_manifest(root, {prefix + 'final.json', prefix + 'result.json',
                           'quality/' + ROW + '/report.json', ROW + '/seal.json',
                           'journal.jsonl', 'analysis-export.json',
                           *(prefix + name for name in extra_host_files),
                           *(prefix + 'snapshot/' + name
                             for name in extra_snapshot_files),
                           *('quality/' + ROW + '/host/' + name
                             for name in extra_quality_files)})


class RecordedEvidenceForgeryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='v4-verifier-forgery-')
        self.addCleanup(self.temp.cleanup)
        self.assertEqual(sha(ARCHIVE.read_bytes()), ARCHIVE_SHA256)
        with tarfile.open(ARCHIVE, 'r:gz') as source:
            members = source.getmembers()
            self.assertTrue(all((item.isfile() or item.isdir())
                                and not Path(item.name).is_absolute()
                                and '..' not in Path(item.name).parts
                                for item in members))
            source.extractall(self.temp.name)
        self.root = Path(self.temp.name) / 'quality-fail-block1-evidence-v4r2'
        plan_raw = subprocess.check_output(['git', 'show',
                                            FROZEN_HEAD + ':' + PLAN_RELATIVE],
                                           cwd=PUBLIC)
        self.assertEqual(sha(plan_raw), FROZEN_PLAN_SHA256)
        self.plan = Path(self.temp.name) / 'frozen-plan.json'
        self.plan.write_bytes(plan_raw)

    def rejected(self, reason):
        with self.assertRaisesRegex(ValueError, reason):
            verify(self.root, self.plan, PUBLIC)

    def test_unmodified_recording_stays_partial(self):
        value = verify(self.root, self.plan, PUBLIC)
        self.assertFalse(value['campaign_path_qualified'])
        self.assertTrue(value['recorded_scenario_shape_met'])

    def test_active_handlers_forgery(self):
        def change(final, result, _report):
            final['bridge']['active_handlers'] = 9
            result['bridge']['active_handlers'] = 9
        forge_host(self.root, change)
        self.rejected('broker or bridge accounting differs')

    def test_source_unchanged_forgery(self):
        def change(final, result, _report):
            final['source_unchanged'] = True
            result['source_unchanged'] = True
        forge_host(self.root, change)
        self.rejected('source unchanged flag differs')

    def test_quality_decision_forgery(self):
        def change(final, result, report):
            final['quality'] = True
            result['quality'] = True
            report['quality'] = True
        forge_host(self.root, change)
        self.rejected('quality decision differs')

    def test_quality_pass_forged_over_raw_failures(self):
        def change(final, result, report):
            final['quality'] = True
            final['quality_status'] = 'pass'
            result['quality'] = True
            report['quality'] = True
            report['status'] = 'pass'
            for item in report['cases']:
                item['passed'] = True
            report['behavior_passed'] = len(report['cases'])
            report['upstream']['passed'] = True
        forge_host(self.root, change)
        self.rejected('recorded quality case decision differs')

    def test_null_provider_requests_forgery(self):
        name = 'control-transcripts/' + ROW + '.json'
        transcript = read(self.root, name)
        transcript['requests'] = [None] * len(transcript['requests'])
        write(self.root, name, transcript)
        update_manifest(self.root, {name})
        self.rejected('provider request body or route differs')

    def test_fake_provider_send_log_forgery(self):
        name = 'control-transcripts/' + ROW + '.json'
        transcript = read(self.root, name)
        transcript['events_sha256'] = None
        write(self.root, name, transcript)
        update_manifest(self.root, {name})
        self.rejected('provider send log hash differs')

    def test_quality_asset_forgery(self):
        name = 'quality-assets/verify_packaging.py'
        with (self.root / name).open('ab') as stream:
            stream.write(b'\n# tampered\n')
        update_manifest(self.root, {name})
        self.rejected('quality verifier asset differs')

    def test_prompt_and_trace_forgery(self):
        prefix = ROW + '/host-artifacts/'
        prompt = (self.root / (prefix + 'prompt.txt')).read_text()
        lines = prompt.splitlines()
        lines[1] = '/bin/true'
        (self.root / (prefix + 'prompt.txt')).write_text('\n'.join(lines))
        events = [json.loads(line) for line in
                  (self.root / (prefix + 'trace.jsonl')).read_text().splitlines()]
        commands = [event for event in events if event.get('type', '').startswith('item.')]
        self.assertGreaterEqual(len(commands), 2)
        for event in commands[:2]:
            event['item']['command'] = '/bin/zsh -lc /bin/true'
        trace = ''.join(json.dumps(event, sort_keys=True, separators=(',', ':')) + '\n'
                        for event in events).encode()
        (self.root / (prefix + 'trace.jsonl')).write_bytes(trace)

        def change(final, result, _report):
            for value in (final, result):
                value['trace_sha256'] = sha(trace)
                value['trace_bytes'] = len(trace)
        forge_host(self.root, change, ('prompt.txt', 'trace.jsonl'))
        self.rejected('diagnostic prompt differs from frozen task')

    def test_cleanup_receipt_forgery(self):
        def change(final, result, _report):
            for value in (final, result):
                value['cleanup']['remaining_descendants'] = 17
                value['cleanup']['open_work_handles'] = True
                value['cleanup']['watch_errors'] = ['observer failed']
                value['cleanup']['output_overflow'] = True
        forge_host(self.root, change)
        self.rejected('cleanup receipt contradicts verified state')

    def test_negative_elapsed_forgery(self):
        def change(final, result, _report):
            final['elapsed_seconds'] = -123
            result['elapsed_seconds'] = -123
        forge_host(self.root, change)
        self.rejected('completed slot timing differs')

    def test_unstarted_slot_result_forgery(self):
        export = read(self.root, 'analysis-export.json')
        export['slots'][4].update(quality=True, elapsed_seconds=42,
                                  timed_out=False,
                                  accounting_evidence_sha256='f' * 64)
        write(self.root, 'analysis-export.json', export)
        update_manifest(self.root, {'analysis-export.json'})
        self.rejected('analysis unstarted slot differs')

    def test_model_command_forgery(self):
        name = ROW + '/host-artifacts/model-command.json'
        command = read(self.root, name)
        command[11] = 'unfrozen-model'
        command[13] = 'model_reasoning_effort="high"'
        command[17] = 'other_feature'
        write(self.root, name, command)
        forge_host(self.root, lambda *_: None, ('model-command.json',))
        self.rejected('model command differs from frozen CLI protocol')

    def test_sandbox_profile_forgery(self):
        profile = '(allow default)\n'
        prefix = ROW + '/host-artifacts/'
        command = read(self.root, prefix + 'model-command.json')
        command[2] = profile
        write(self.root, prefix + 'model-command.json', command)
        (self.root / (prefix + 'seatbelt.sb')).write_text(profile)
        forge_host(self.root, lambda *_: None,
                   ('model-command.json', 'seatbelt.sb'))
        self.rejected('model sandbox profile differs')

    def test_crashed_cli_claimed_as_success(self):
        def change(final, result, _report):
            for value in (final, result):
                value['exit_code'] = -9
                value['outcome'] = 'worker_crashed'
        forge_host(self.root, change)
        self.rejected('completed normal-control process outcome differs')

    def test_evaluator_cleanup_receipt_forgery(self):
        name = 'nested-single.process.json'
        path = 'quality/' + ROW + '/host/' + name
        process = read(self.root, path)
        process['remaining_descendants'] = [12345]
        process['handles_verified'] = False
        process['cleanup_errors'] = ['lsof failed']
        write(self.root, path, process)

        def change(_final, _result, report):
            report['cases'][0].update(remaining_descendants=[12345],
                                       handles_verified=False,
                                       cleanup_errors=['lsof failed'])
        forge_host(self.root, change, extra_quality_files=(name,))
        self.rejected('recorded quality case process differs')

    def test_first_trace_item_id_forgery(self):
        prefix = ROW + '/host-artifacts/'
        events = [json.loads(line) for line in
                  (self.root / (prefix + 'trace.jsonl')).read_text().splitlines()]
        first = next(event for event in events if event.get('type') == 'item.started')
        first['item']['id'] = 'never-completed'
        trace = ''.join(json.dumps(event, sort_keys=True, separators=(',', ':')) + '\n'
                        for event in events).encode()
        (self.root / (prefix + 'trace.jsonl')).write_bytes(trace)

        def change(final, result, _report):
            for value in (final, result):
                value['trace_sha256'] = sha(trace)
                value['trace_bytes'] = len(trace)
        forge_host(self.root, change, ('trace.jsonl',))
        self.rejected('first CLI action is not a completed diagnostic')

    def test_protected_test_edit_cannot_count_as_repair(self):
        relative = 'tests/test_metadata.py'
        snapshot = self.root / ROW / 'host-artifacts/snapshot'
        with (snapshot / relative).open('ab') as stream:
            stream.write(b'\n# forged test edit\n')
        checkpoint = tree_manifest(snapshot)

        def change(final, result, report):
            for value in (final, result):
                value['checkpoint']['manifest'] = checkpoint
                value['source_unchanged'] = False
            report['candidate_tree_sha256'] = checkpoint['tree_sha256']
            report['changed_paths'] = sorted([
                'src/packaging/licenses/__init__.py', relative])
        forge_host(self.root, change, extra_snapshot_files=(relative,))
        self.rejected('recorded quality changed paths differ')

    def test_failed_evaluator_isolation_canary(self):
        prefix = 'quality/' + ROW + '/host/'
        canaries = read(self.root, prefix + 'canaries.stdout')
        canaries['host_write'] = False
        write(self.root, prefix + 'canaries.stdout', canaries)
        process = read(self.root, prefix + 'canaries.process.json')
        raw = (self.root / (prefix + 'canaries.stdout')).read_bytes()
        process['stdout_sha256'] = sha(raw)
        process['stdout_bytes'] = len(raw)
        write(self.root, prefix + 'canaries.process.json', process)

        def change(_final, _result, report):
            report['canaries'] = canaries
        forge_host(self.root, change, extra_quality_files=(
            'canaries.stdout', 'canaries.process.json'))
        self.rejected('recorded evaluator isolation canary differs')

    def test_missing_upstream_test_lifecycle(self):
        prefix = 'quality/' + ROW + '/host/'
        frame = read(self.root, prefix + 'upstream.stdout')
        frame['reports'] = []
        write(self.root, prefix + 'upstream.stdout', frame)
        process = read(self.root, prefix + 'upstream.process.json')
        raw = (self.root / (prefix + 'upstream.stdout')).read_bytes()
        process['stdout_sha256'] = sha(raw)
        process['stdout_bytes'] = len(raw)
        write(self.root, prefix + 'upstream.process.json', process)

        def change(_final, _result, report):
            report['upstream']['reports'] = []
            report['upstream_process'] = process
        forge_host(self.root, change, extra_quality_files=(
            'upstream.stdout', 'upstream.process.json'))
        self.rejected('recorded upstream test lifecycle missing')

    def test_failed_cli_turn_cannot_count_as_success(self):
        prefix = ROW + '/host-artifacts/'
        path = self.root / (prefix + 'trace.jsonl')
        events = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(events[-1]['type'], 'turn.completed')
        events[-1]['type'] = 'turn.failed'
        trace = ''.join(json.dumps(event, sort_keys=True, separators=(',', ':'))
                        + '\n' for event in events).encode()
        path.write_bytes(trace)

        def change(final, result, _report):
            for value in (final, result):
                value['trace_sha256'] = sha(trace)
                value['trace_bytes'] = len(trace)
        forge_host(self.root, change, ('trace.jsonl',))
        self.rejected('CLI trace terminal outcome differs')


if __name__ == '__main__':
    unittest.main()
