#!/usr/bin/env python3
"""Upgrade SoL Codex while retaining cache paths used by open tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PLUGIN_ID = "sol-codex@sol-codex"
MARKETPLACE = "sol-codex"


def installed_entry(codex: str) -> dict:
    result = subprocess.run([codex, "plugin", "list", "--json"], check=True, capture_output=True, text=True)
    entries = json.loads(result.stdout)["installed"]
    matches = [item for item in entries if item.get("pluginId") == PLUGIN_ID]
    if len(matches) != 1:
        raise RuntimeError(f"expected one installed {PLUGIN_ID} plugin")
    return matches[0]


def validate_cache_entry(path: Path, cache_root: Path) -> None:
    if path.is_symlink():
        target = path.resolve(strict=True)
        if target.parent != cache_root.resolve() or not target.is_dir():
            raise RuntimeError(f"unsafe cache link: {path}")
        return
    if not path.is_dir():
        raise RuntimeError(f"unexpected cache entry: {path}")
    manifest = path / ".codex-plugin" / "plugin.json"
    identity = json.loads(manifest.read_text(encoding="utf-8"))
    if identity.get("name") != "sol-codex" or identity.get("version") != path.name:
        raise RuntimeError(f"cache identity mismatch: {path}")
    for nested in path.rglob("*"):
        if nested.is_symlink():
            raise RuntimeError(f"unexpected nested cache link: {nested}")


def entry_digest(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_symlink():
        digest.update(b"link\0" + os.fsencode(os.readlink(path)))
        return digest.hexdigest()
    for child in sorted(path.rglob("*")):
        digest.update(os.fsencode(str(child.relative_to(path))) + b"\0")
        if child.is_dir():
            digest.update(b"directory\0")
        elif child.is_file():
            digest.update(b"file\0" + child.read_bytes())
        else:
            raise RuntimeError(f"unexpected cache file: {child}")
    return digest.hexdigest()


def upgrade(codex: str, cache_root: Path) -> None:
    old_version = str(installed_entry(codex)["version"])
    if not cache_root.is_dir() or cache_root.is_symlink():
        raise RuntimeError(f"expected plugin cache directory: {cache_root}")
    entries = list(cache_root.iterdir())
    if not entries or not (cache_root / old_version).exists():
        raise RuntimeError("installed version is missing from plugin cache")
    for entry in entries:
        validate_cache_entry(entry, cache_root)

    backup = Path(tempfile.mkdtemp(prefix="sol-codex-cache-backup-"))
    restored = False
    try:
        for entry in entries:
            if entry.is_symlink():
                (backup / entry.name).symlink_to(os.readlink(entry))
            else:
                shutil.copytree(entry, backup / entry.name, symlinks=True)
        subprocess.run([codex, "plugin", "marketplace", "upgrade", MARKETPLACE], check=True)
        marketplace_path = Path(installed_entry(codex)["source"]["path"])
        manifest = json.loads((marketplace_path / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        if manifest.get("name") != "sol-codex":
            raise RuntimeError("marketplace plugin identity mismatch")
        new_version = str(manifest["version"])
        if new_version == old_version:
            print(f"No newer version found ({old_version}); cache unchanged.")
            return
        subprocess.run([codex, "plugin", "remove", PLUGIN_ID], check=True)
        subprocess.run([codex, "plugin", "add", PLUGIN_ID], check=True)
        if str(installed_entry(codex)["version"]) != new_version:
            raise RuntimeError(f"installed version does not match marketplace version {new_version}")
    finally:
        try:
            cache_root.mkdir(parents=True, exist_ok=True)
            for entry in entries:
                destination = cache_root / entry.name
                source = backup / entry.name
                if destination.exists() or destination.is_symlink():
                    if entry_digest(destination) != entry_digest(source):
                        raise RuntimeError(f"cache path changed during upgrade: {destination}")
                    continue
                if source.is_symlink():
                    destination.symlink_to(os.readlink(source))
                else:
                    shutil.copytree(source, destination, symlinks=True)
            restored = True
        finally:
            if restored:
                shutil.rmtree(backup)
            else:
                print(f"Cache backup retained at {backup}; restore it before closing old tasks.", file=sys.stderr)
    print(f"Installed {new_version}; retained {len(entries)} previous cache paths.")
    print("Review /hooks and trust changed definitions. Verify a new hook event in the open task.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex", help="Codex CLI executable")
    parser.add_argument("--cache-root", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "plugins/cache/sol-codex/sol-codex")
    args = parser.parse_args()
    try:
        upgrade(args.codex, args.cache_root)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"Upgrade failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
