"""Run a pinned Codex CLI and Code Mode host from a private local bundle."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

import pilot


BUNDLE = pilot.WORK / 'quiet-cli-bundle-16-4'
PINNED = {
    'codex': '93169e745735930598e867ad837abf3fdc50774a3ad7e7aa89c0d0c51b0189a5',
    'codex-code-mode-host': '48c61488ebc34342a71c8cd70b7b46947b86e31f5c616a3160a93a3407e8067b',
}
VERSION = 'codex-cli 0.155.0-alpha.16.4'
APP_CLI = '/Applications/ChatGPT.app/Contents/Resources/codex'
SOURCE_SNAPSHOT_NAME = 'quiet-diagnostic-pilot-bundle-v2'


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def validate_bundle() -> None:
    if BUNDLE.is_symlink() or not BUNDLE.is_dir():
        raise ValueError('CLI bundle directory is missing or a symlink')
    if {item.name for item in BUNDLE.iterdir()} != set(PINNED):
        raise ValueError('CLI bundle contains unexpected files')
    for name, expected in PINNED.items():
        path = BUNDLE / name
        if path.is_symlink() or not path.is_file() or sha(path) != expected:
            raise ValueError('CLI bundle differs: ' + name)
    version = subprocess.check_output([str(BUNDLE / 'codex'), '--version'],
                                      text=True, timeout=10).strip()
    if version != VERSION:
        raise ValueError('CLI bundle version differs')


def validate_source_snapshot(entrypoint: str, source_files: set[str] | frozenset[str]) -> None:
    """Require a clean source import path with no preexisting bytecode cache."""
    here = pilot.HERE
    cache = here / 'empty-pycache'
    if (here.name != SOURCE_SNAPSHOT_NAME or not sys.dont_write_bytecode
            or sys.pycache_prefix != str(cache) or not cache.is_dir()
            or any(cache.iterdir())):
        raise ValueError('runner did not start from a clean source snapshot')
    main = sys.modules['__main__']
    if Path(main.__file__).resolve() != here / entrypoint:
        raise ValueError('runner entrypoint differs from source snapshot')
    for name in source_files:
        path = here / name
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o222:
            raise ValueError('source snapshot file is mutable or missing: ' + name)
        if not name.endswith('.py') or name == entrypoint:
            continue
        module = sys.modules.get(name[:-3])
        if module is not None and Path(module.__file__).resolve() != path:
            raise ValueError('import escaped source snapshot: ' + name)


def model_plan(root: Path, profile: str, message: str) -> list[str]:
    """Copy the pinned pair into one run's sandbox before constructing its command."""
    validate_bundle()
    tools = root / 'tools'
    if not tools.is_dir() or not (root / 'workspace').is_dir():
        raise ValueError('run directories are missing')
    for name, expected in PINNED.items():
        target = tools / name
        if target.exists() or target.is_symlink():
            raise ValueError('CLI destination already exists: ' + name)
        shutil.copy2(BUNDLE / name, target)
        if sha(target) != expected:
            raise ValueError('copied CLI differs: ' + name)
    argv = pilot.model_plan(profile, message)
    if argv[3] != APP_CLI:
        raise ValueError('unexpected base CLI command')
    argv[3] = str(tools / 'codex')
    argv[-1:-1] = ['-c', 'suppress_unstable_features_warning=true']
    return argv
