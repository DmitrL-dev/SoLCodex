"""Bounded POSIX snapshots of stopped development workers, not a sandbox.

Modes are normalized to 0644 plus the source's executable bits, and 0755 for
directories. Ownership, timestamps and non-executable permission bits are not
part of the manifest. Tree limits count regular-file bytes, including each name
of a hard-linked file. Symlinks are recorded, never opened or traversed by I/O.
Concurrent mutation is unsupported; observed identity/content races fail closed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

FILE_LIMIT = 5 * 1024 * 1024
TREE_LIMIT = 32 * 1024 * 1024
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _directory(path: Path) -> int:
    """Open every component without following even ancestor symlinks."""
    path = Path(os.path.abspath(path))
    descriptor = os.open(path.anchor, DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _identity(value: os.stat_result) -> tuple:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)


def _same(before: os.stat_result, after: os.stat_result) -> None:
    if _identity(before) != _identity(after):
        raise ValueError("tree changed during snapshot")


def _links(entries: list[dict]) -> None:
    nodes = {entry['path']: entry for entry in entries}
    nodes[''] = {'type': 'directory'}

    def resolve(parts: list[str], active: frozenset[str] = frozenset()) -> str:
        current: list[str] = []
        for index, part in enumerate(parts):
            if part in ('', '.'):
                continue
            if part == '..':
                if not current:
                    raise ValueError("symlink escapes tree")
                current.pop()
                continue
            current.append(part)
            name = '/'.join(current)
            node = nodes.get(name)
            if node is None:
                raise ValueError("dangling symlink")
            if node['type'] == 'symlink':
                if name in active:
                    raise ValueError("cyclic symlink")
                target = node['target']
                if target.startswith('/'):
                    raise ValueError("absolute symlink")
                name = resolve(current[:-1] + target.split('/'), active | {name})
                current = name.split('/') if name else []
            # A following path component, including '..', requires a directory.
            if nodes[name]['type'] != 'directory' and index != len(parts) - 1:
                raise ValueError("symlink traverses non-directory")
        return '/'.join(current)

    edges = {name: [] for name, node in nodes.items() if node['type'] == 'directory'}
    for name, node in nodes.items():
        if not name:
            continue
        parent = name.rpartition('/')[0]
        if node['type'] == 'directory':
            edges[parent].append(name)
        elif node['type'] == 'symlink':
            target = resolve(name.split('/'))
            if nodes[target]['type'] == 'directory':
                edges[parent].append(target)
    done: set[str] = set()

    def visit(name: str, active: set[str]) -> None:
        if name in active:
            raise ValueError("directory symlink cycle")
        if name in done:
            return
        for target in edges[name]:
            visit(target, active | {name})
        done.add(name)

    visit('', set())


def _collect(root: Path, max_file_bytes: int, max_tree_bytes: int) -> tuple[dict, dict]:
    if any(type(limit) is not int or limit < 0 for limit in (max_file_bytes, max_tree_bytes)):
        raise ValueError("limits must be nonnegative integers")
    entries: list[dict] = []
    contents: dict[str, bytes] = {}
    total = 0

    def walk(descriptor: int, prefix: str) -> None:
        nonlocal total
        initial = os.fstat(descriptor)
        names = sorted(os.listdir(descriptor))
        for name in names:
            if name == '.git':
                raise ValueError("Git metadata forbidden")
            relative = prefix + name
            before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(before.st_mode):
                child = os.open(name, DIRECTORY, dir_fd=descriptor)
                try:
                    _same(before, os.fstat(child))
                    entries.append({'path': relative, 'type': 'directory', 'mode': 0o755})
                    walk(child, relative + '/')
                finally:
                    os.close(child)
            elif stat.S_ISLNK(before.st_mode):
                entries.append({'path': relative, 'type': 'symlink',
                                'target': os.readlink(name, dir_fd=descriptor)})
            elif stat.S_ISREG(before.st_mode):
                if before.st_size > max_file_bytes or total + before.st_size > max_tree_bytes:
                    raise ValueError("snapshot size limit exceeded")
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                                os.O_CLOEXEC, dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    _same(before, opened)
                    if not stat.S_ISREG(opened.st_mode):
                        raise ValueError("nonregular file")
                    chunks = []
                    remaining = before.st_size + 1
                    while remaining:
                        chunk = os.read(child, min(remaining, 65536))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    raw = b''.join(chunks)
                    _same(before, os.fstat(child))
                    if len(raw) != before.st_size:
                        raise ValueError("file size changed")
                finally:
                    os.close(child)
                total += len(raw)
                contents[relative] = raw
                entries.append({'path': relative, 'type': 'file', 'size': len(raw),
                                'mode': 0o644 | (before.st_mode & 0o111),
                                'sha256': hashlib.sha256(raw).hexdigest()})
            else:
                raise ValueError("special files forbidden")
            _same(before, os.stat(name, dir_fd=descriptor, follow_symlinks=False))
        if names != sorted(os.listdir(descriptor)):
            raise ValueError("directory entries changed")
        _same(initial, os.fstat(descriptor))

    descriptor = _directory(root)
    try:
        walk(descriptor, '')
    finally:
        os.close(descriptor)
    entries.sort(key=lambda entry: entry['path'])
    _links(entries)
    encoded = json.dumps(entries, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return {'entries': entries, 'tree_sha256': hashlib.sha256(encoded).hexdigest()}, contents


def manifest(root: Path, max_file_bytes: int = FILE_LIMIT,
             max_tree_bytes: int = TREE_LIMIT) -> dict:
    """Read and validate the tree without modifying it."""
    return _collect(root, max_file_bytes, max_tree_bytes)[0]


def _remove(parent: int, name: str) -> None:
    """Remove our partial directory using anchored, no-follow operations."""
    descriptor = os.open(name, DIRECTORY, dir_fd=parent)
    try:
        for child in os.listdir(descriptor):
            info = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                _remove(descriptor, child)
            else:
                os.unlink(child, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent)


def capture(source: Path, destination: Path, max_file_bytes: int = FILE_LIMIT,
            max_tree_bytes: int = TREE_LIMIT) -> dict:
    """Create a new tree; any failure removes only our partial destination."""
    source, destination = Path(os.path.abspath(source)), Path(os.path.abspath(destination))
    if destination == source or source in destination.parents:
        raise ValueError("destination must be outside source")
    parent = _directory(destination.parent)
    created = False
    root_fd = None
    try:
        # Refuse an existing destination before reading source; mkdir also checks races.
        try:
            os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(destination)
        result, contents = _collect(source, max_file_bytes, max_tree_bytes)
        os.mkdir(destination.name, 0o700, dir_fd=parent)
        created = True
        root_fd = os.open(destination.name, DIRECTORY, dir_fd=parent)
        for entry in result['entries']:
            parts = entry['path'].split('/')
            directory = os.dup(root_fd)
            try:
                for part in parts[:-1]:
                    child = os.open(part, DIRECTORY, dir_fd=directory)
                    os.close(directory)
                    directory = child
                name = parts[-1]
                if entry['type'] == 'directory':
                    os.mkdir(name, 0o755, dir_fd=directory)
                    child = os.open(name, DIRECTORY, dir_fd=directory)
                    try:
                        os.fchmod(child, 0o755)
                    finally:
                        os.close(child)
                elif entry['type'] == 'symlink':
                    os.symlink(entry['target'], name, dir_fd=directory)
                else:
                    child = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                    os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
                    with os.fdopen(child, 'wb') as stream:
                        stream.write(contents[entry['path']])
                        stream.flush()
                        os.fchmod(stream.fileno(), entry['mode'])
                        os.fsync(stream.fileno())
            finally:
                os.close(directory)
        return result
    except BaseException:
        if created:
            _remove(parent, destination.name)
        raise
    finally:
        if root_fd is not None:
            os.close(root_fd)
        os.close(parent)
