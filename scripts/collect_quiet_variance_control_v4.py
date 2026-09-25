"""Copy exact, compact v4 control evidence without copied CLI/runtime binaries.

The output is a private evidence bundle. It is not a qualification decision.
The source campaign remains untouched; SQLite databases are copied as bytes
and are never opened by this collector.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON key')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(
                          ValueError('nonfinite JSON number')))


def source_file(path):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('source file is not regular: ' + str(path))
    raw = path.read_bytes()
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('source file changed during collection: ' + str(path))
    return raw


def safe_leaf(value, label):
    if (not isinstance(value, str) or value in ('', '.', '..')
            or Path(value).name != value or '\\' in value):
        raise ValueError('unsafe ' + label + ': ' + repr(value))
    return value


def pinned_cli_names(sealed, protocol):
    expected = {'codex': protocol['cli']['sha256'],
                'codex-code-mode-host': protocol['cli']['code_mode_host_sha256']}
    if sealed.get('copied_cli_sha256') != expected:
        raise ValueError('sealed CLI names or hashes differ from protocol')
    return expected


def transcript_directory(root, starts):
    directory = root / 'control-transcripts'
    if starts and (directory.is_symlink() or not directory.is_dir()):
        raise ValueError('control transcript directory missing or symlinked')
    return directory


def collect(source, destination, catalog):
    """Copy a regular file or a directory, preserving safe relative symlinks."""
    relative_path = destination.relative_to(catalog['root'])
    if '..' in relative_path.parts or not relative_path.parts:
        raise ValueError('unsafe evidence destination: ' + str(destination))
    ancestor = destination.parent
    while not ancestor.exists() and not ancestor.is_symlink():
        ancestor = ancestor.parent
    if (ancestor.resolve(strict=True) != ancestor
            or not ancestor.is_relative_to(catalog['root'])):
        raise ValueError('evidence destination escaped output: ' + str(destination))
    mode = source.lstat().st_mode
    relative = relative_path.as_posix()
    if stat.S_ISLNK(mode):
        target = os.readlink(source)
        if os.path.isabs(target) or '..' in Path(target).parts:
            raise ValueError('unsafe evidence symlink: ' + str(source))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(target)
        catalog['entries'][relative] = {'type': 'symlink', 'target': target}
    elif stat.S_ISDIR(mode):
        destination.mkdir(parents=True, exist_ok=False)
        children = sorted(source.iterdir(), key=lambda item: item.name)
        catalog['directories'].append((source, tuple(child.name for child in children)))
        for child in children:
            collect(child, destination / child.name, catalog)
    elif stat.S_ISREG(mode):
        raw = source_file(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(destination, stat.S_IMODE(mode))
        if sha(destination.read_bytes()) != sha(raw):
            raise ValueError('copied evidence differs: ' + relative)
        catalog['entries'][relative] = {'type': 'file', 'bytes': len(raw),
                                         'sha256': sha(raw)}
        catalog['origins'].append((source, sha(raw)))
    else:
        raise ValueError('unsupported evidence entry: ' + str(source))


def _make_bundle_locked(root, output):
    schedule = strict_json(source_file(root / 'schedule.json'))
    protocol = strict_json(source_file(root / 'protocol.json'))
    protocol_sha256 = sha(source_file(root / 'protocol.json'))
    prefix = 'variance-control-'
    suffix = '-' + protocol_sha256[:16]
    if not root.name.startswith(prefix) or not root.name.endswith(suffix):
        raise ValueError('control campaign name differs from protocol')
    case = root.name[len(prefix):-len(suffix)]
    if not case:
        raise ValueError('empty control case')
    rows = schedule['schedule']
    expected_ids = [row['id'] for row in rows]
    journal_raw = source_file(root / 'journal.jsonl')
    if journal_raw and not journal_raw.endswith(b'\n'):
        raise ValueError('journal has a partial line')
    events = [strict_json(line) for line in journal_raw.splitlines()]
    starts = [event['id'] for event in events if event.get('kind') == 'start']
    if starts != expected_ids[:len(starts)]:
        raise ValueError('journal start order differs from schedule')
    finishes = {event['id']: event for event in events if event.get('kind') == 'finish'}
    output.mkdir(mode=0o700)
    catalog = {'root': output, 'entries': {}, 'origins': [], 'directories': []}
    cli_pins = {}
    quality_paths = {}
    for name in ('protocol.json', 'schedule.json', 'journal.jsonl'):
        collect(root / name, output / name, catalog)
    for name, digest in sorted(protocol['code_sha256'].items()):
        safe_leaf(name, 'campaign source name')
        if re.fullmatch(r'[a-z][a-z0-9_]*\.py', name) is None:
            raise ValueError('invalid campaign source name: ' + name)
        source = root.parent / name
        if sha(source_file(source)) != digest:
            raise ValueError('pinned campaign source differs: ' + name)
        collect(source, output / 'source' / name, catalog)
    assets = root.parent / 'quality-assets'
    if sha(source_file(assets / 'lock.json')) != protocol['quality']['assets_lock_sha256']:
        raise ValueError('quality asset lock differs')
    collect(assets, output / 'quality-assets', catalog)
    for task in sorted(protocol['source_tree_sha256']):
        if task not in ('click', 'packaging'):
            raise ValueError('unsupported source fixture: ' + repr(task))
        fixture = root.parent.parent / ('prehook-' + task + '-dev') / 'fixture'
        if not fixture.is_dir() or fixture.is_symlink():
            raise ValueError('source fixture missing: ' + task)
        collect(fixture, output / 'fixtures' / task, catalog)
    transcript_dir = transcript_directory(root, starts)
    for row_id in starts:
        safe_leaf(row_id, 'schedule row ID')
        if re.fullmatch(r'[0-9]{2}-b[1-4]-(click|packaging)-(quiet|verbose)', row_id) is None:
            raise ValueError('invalid schedule row ID: ' + row_id)
        run = root / row_id
        if run.is_symlink() or not run.is_dir():
            raise ValueError('started run directory missing: ' + row_id)
        source_host = run / 'host-artifacts'
        if source_host.is_dir() and not source_host.is_symlink():
            collect(source_host, output / row_id / 'host-artifacts', catalog)
        seal_path = run / 'seal.json'
        if row_id in finishes:
            raw = source_file(seal_path)
            if sha(raw) != finishes[row_id].get('seal_sha256'):
                raise ValueError('journal seal hash differs: ' + row_id)
            sealed = strict_json(raw)
            if set(sealed['host_files']) != {
                    path.name for path in source_host.iterdir() if path.is_file()}:
                raise ValueError('unsealed host file: ' + row_id)
            for name, digest in sealed['host_files'].items():
                if sha(source_file(source_host / name)) != digest:
                    raise ValueError('sealed host byte differs: ' + row_id + '/' + name)
            collect(seal_path, output / row_id / 'seal.json', catalog)
            copied_cli = {}
            for name, digest in pinned_cli_names(sealed, protocol).items():
                observed = sha(source_file(run / 'tools' / name))
                if observed != digest:
                    raise ValueError('copied CLI differs from seal: ' + row_id)
                copied_cli[name] = observed
            cli_pins[row_id] = copied_cli
        final_path = source_host / 'final.json'
        if final_path.is_file() and not final_path.is_symlink():
            final = strict_json(source_file(final_path))
            report_root = final.get('quality_report_root')
            if report_root is not None:
                quality = Path(report_root)
                if (quality.is_symlink() or not quality.is_dir()
                        or not quality.resolve(strict=True).is_relative_to(root.parent)
                        or not quality.name.startswith('quality-run-')):
                    raise ValueError('quality report escaped source snapshot')
                report = quality / 'report.json'
                if sha(source_file(report)) != final.get('quality_report_sha256'):
                    raise ValueError('quality report hash differs: ' + row_id)
                collect(report, output / 'quality' / row_id / 'report.json', catalog)
                collect(quality / 'host', output / 'quality' / row_id / 'host', catalog)
                quality_paths[row_id] = str(quality)
        for name in (row_id + '.json', row_id + '-events.jsonl'):
            transcript = transcript_dir / name
            if transcript.exists():
                collect(transcript, output / 'control-transcripts' / name,
                        catalog)
    optional_export = root / 'analysis-export.json'
    if optional_export.exists():
        collect(optional_export, output / 'analysis-export.json', catalog)
    for source, expected in catalog['origins']:
        if sha(source_file(source)) != expected:
            raise ValueError('source evidence changed during collection: ' + str(source))
    for source, names in catalog['directories']:
        if tuple(sorted(path.name for path in source.iterdir())) != names:
            raise ValueError('source directory changed during collection: ' + str(source))
    manifest = {
        'schema': 'solcodex.quiet-campaign-compact-evidence.v4',
        'case': case,
        'scope': 'private copy of exact raw evidence; no qualification decision',
        'protocol_sha256': protocol_sha256,
        'schedule_sha256': sha(source_file(root / 'schedule.json')),
        'journal_sha256': sha(journal_raw),
        'started_ids': starts,
        'observed_copied_cli_sha256': cli_pins,
        'original_quality_report_roots': quality_paths,
        'omitted': ['copied CLI and Code Mode host binaries',
                    'copied Python runtime', 'live workspace', 'CLI home and temporary files'],
        'entries': catalog['entries'],
    }
    with (output / 'manifest.json').open('x') as stream:
        json.dump(manifest, stream, sort_keys=True, indent=2)
        stream.write('\n')
    return {'files': sum(item['type'] == 'file' for item in catalog['entries'].values()),
            'bytes': sum(item.get('bytes', 0) for item in catalog['entries'].values()),
            'started': len(starts), 'manifest_sha256': sha((output / 'manifest.json').read_bytes())}


def make_bundle(root, output):
    root_path = root.absolute()
    if root_path.is_symlink():
        raise ValueError('campaign root is a symlink')
    root = root_path.resolve(strict=True)
    output = output.absolute()
    safe_leaf(output.name, 'output name')
    parent = output.parent.resolve(strict=True)
    output = parent / output.name
    protected = [root.parent]
    for task in ('click', 'packaging'):
        fixture = root.parent.parent / ('prehook-' + task + '-dev') / 'fixture'
        if fixture.exists():
            protected.append(fixture.resolve(strict=True))
    if (not root.is_dir() or output.exists() or output.is_symlink()
            or any(output.is_relative_to(path) or path.is_relative_to(output)
                   for path in protected)):
        raise ValueError('invalid campaign root or output path')
    lock_path = root.parent / 'execution.lock'
    if lock_path.is_symlink() or not lock_path.is_file():
        raise ValueError('campaign execution lock missing or symlinked')
    with lock_path.open('rb') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        catalog = _make_bundle_locked(root, output)
        return catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(make_bundle(args.root, args.output), sort_keys=True))


if __name__ == '__main__':
    main()
