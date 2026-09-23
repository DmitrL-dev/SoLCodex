#!/usr/bin/env python3
"""Pin the stable hook loader in the seven portable hook commands."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shlex
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins" / "sol-codex"
BOOTSTRAP = PLUGIN / "scripts" / "sol_bootstrap.py"
HOOKS = PLUGIN / "hooks" / "hooks.json"


def commands() -> tuple[str, str]:
    pin = hashlib.sha256(BOOTSTRAP.read_bytes()).hexdigest()
    inline = f'''import hashlib, os, sys
from pathlib import Path
try:
    if sys.version_info < (3, 9):
        sys.exit(3)
    data = Path(os.environ["PLUGIN_DATA"]) / "runtime-v1" / "bootstrap.py"
    source = Path(os.environ["PLUGIN_ROOT"]) / "scripts" / "sol_bootstrap.py"
    for path in (data, source):
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            continue
        if hashlib.sha256(raw).hexdigest() == "{pin}":
            exec(compile(raw, str(path), "exec"), {{"__name__": "__main__", "__file__": str(path), "BOOTSTRAP_BYTES": raw}})
            break
    else:
        raise RuntimeError("verified hook bootstrap unavailable")
except Exception as error:
    sys.stderr.write("SoL Codex hook degraded safely: %s: %s\\n" % (type(error).__name__, error))
'''
    encoded = base64.b64encode(inline.encode("utf-8")).decode("ascii")
    expression = f"import base64;exec(base64.b64decode('{encoded}'))"
    posix = "python3 -c " + shlex.quote(expression)
    windows = f'cmd.exe /d /c py -3 -c "{expression}" || python -c "{expression}"'
    return posix, windows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    posix, windows = commands()
    config = json.loads(HOOKS.read_text(encoding="utf-8"))
    changed = False
    for groups in config["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                if hook.get("command") != posix or hook.get("commandWindows") != windows:
                    changed = True
                hook["command"] = posix
                hook["commandWindows"] = windows
    if options.check:
        if changed:
            parser.error("hooks.json loader pin or commands differ; regenerate")
        return 0
    HOOKS.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
