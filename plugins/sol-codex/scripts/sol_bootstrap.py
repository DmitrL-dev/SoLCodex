#!/usr/bin/env python3
"""Stable hook loader protocol. Keep this file byte-for-byte across runtime releases."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import stat
import sys
import time
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise RuntimeError(f"unsafe runtime directory: {path}")
    if os.name != "nt":
        os.chmod(path, 0o700)
    return path


def read_file(path: Path) -> bytes:
    if stat.S_ISLNK(path.lstat().st_mode):
        raise RuntimeError(f"unsafe runtime file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError(f"unsafe runtime file: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def publish(path: Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
        if read_file(path) != data:
            raise RuntimeError(f"runtime collision or corruption: {path}")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(temporary), flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


@contextlib.contextmanager
def locked(root: Path):
    path = root / "loader.lock"
    descriptor = os.open(str(path), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError("unsafe loader lock")
        deadline = time.monotonic() + 2
        while True:
            try:
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def normalized_root(value: str) -> str:
    # Lexical normalization must still identify the root after its cache was removed.
    return os.path.normcase(os.path.normpath(os.path.abspath(value)))


def choose_runtime(root: Path, event: dict, plugin_root: str) -> bytes:
    source = Path(plugin_root) / "scripts" / "sol_hook.py"
    root_id = digest(normalized_root(plugin_root).encode("utf-8", "surrogatepass"))
    session = str(event.get("session_id") or "unknown-session")
    binding_id = digest((session + "\0" + root_id).encode("utf-8", "surrogatepass"))
    binding_path = directory(root / "bindings") / f"{binding_id}.json"
    snapshots = directory(root / "snapshots")
    index = directory(directory(root / "roots") / root_id)

    with locked(root):
        try:
            current = read_file(source)
        except FileNotFoundError:
            current = None
        current_digest = digest(current) if current is not None else None

        if binding_path.exists() or binding_path.is_symlink():
            binding = json.loads(read_file(binding_path))
            selected = binding.get("digest") if isinstance(binding, dict) else None
            if not isinstance(selected, str) or len(selected) != 64 or any(c not in "0123456789abcdef" for c in selected):
                raise RuntimeError("invalid runtime binding")
            if current_digest is not None and current_digest != selected:
                raise RuntimeError("plugin root reused with different runtime in the same task")
        elif current_digest is not None:
            selected = current_digest
        else:
            candidates = [p.name for p in index.iterdir() if p.is_file() and len(p.name) == 64]
            if len(candidates) != 1:
                raise RuntimeError("runtime unavailable or ambiguous for removed plugin cache")
            selected = candidates[0]

        snapshot = snapshots / f"{selected}.py"
        if current is not None and current_digest == selected:
            publish(snapshot, current)
        runtime = read_file(snapshot)
        if digest(runtime) != selected:
            raise RuntimeError("runtime snapshot digest mismatch")
        if not (binding_path.exists() or binding_path.is_symlink()):
            publish(binding_path, (json.dumps({"digest": selected}) + "\n").encode("utf-8"))
        publish(index / selected, b"")
        return runtime


def main() -> int:
    try:
        data_path = os.environ["PLUGIN_DATA"]
        plugin_root = os.environ["PLUGIN_ROOT"]
        root = directory(directory(Path(data_path)) / "runtime-v1")
        # The inline command verifies these bytes before executing this loader.
        publish(root / "bootstrap.py", BOOTSTRAP_BYTES)
        incoming = sys.stdin.read()
        decoded = json.loads(incoming or "{}")
        event = decoded if isinstance(decoded, dict) else {}
        runtime = choose_runtime(root, event, plugin_root)
        sys.stdin = io.StringIO(incoming)
        exec(compile(runtime, "sol_hook.py", "exec"), {"__name__": "__main__", "__file__": "sol_hook.py"})
    except Exception as error:
        sys.stderr.write(f"SoL Codex hook degraded safely: {type(error).__name__}: {error}\n")
        if "event" in locals() and event.get("hook_event_name") == "Stop":
            sys.stdout.write('{"continue":true}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
