# Installation and lifecycle

## Requirements

- Codex with plugin and hooks support
- Python 3.9 or newer: `python3` on macOS/Linux; `py -3` or `python` on Windows
- macOS, Linux, or Windows

Windows hooks use `msvcrt` file locking and a bundled launcher for all seven lifecycle events. The launcher tries Python 3.9+ through `py -3`, then falls back to `python` on `PATH`. Use the marketplace commands below on Windows. The portable ZIP's `install.sh` is for macOS/Linux only.

The hook contract was validated against Codex `0.155.0-alpha.9.2`. Runtime compatibility is capability-based: a large plain-string `PostToolUse` result can be packed with `exit_code=unknown`, while a recognized top-level structured status can be used directly. In `bypassPermissions` mode, `PreToolUse` also captures actual Bash verifier status in a private sidecar. The hook does not rewrite commands in approval-capable modes. Host-side truncation before `PostToolUse` may keep a result below the packing threshold.

## Install from the marketplace

```bash
codex plugin marketplace add DmitrL-dev/SoLCodex
codex plugin add sol-codex@sol-codex
```

Open `/hooks` after installation. Review the commands resolved from `hooks/hooks.json`, then approve the trust prompt. Create a new task to load `SessionStart` and the other lifecycle hooks.

## Update

Refresh the marketplace checkout first:

```bash
codex plugin marketplace upgrade sol-codex
```

If the installed plugin remains on an old cachebuster version, reinstall it:

```bash
codex plugin remove sol-codex@sol-codex
codex plugin add sol-codex@sol-codex
```

Open `/hooks` again. Changed hook files produce a new trust hash and must be reviewed. Start a new task after an update; running tasks retain the hooks they started with.

## Environment overrides

Launch Codex from an environment containing any overrides you need:

```bash
export SOL_CODEX_ASTRA_PACK_THRESHOLD_BYTES=4096
export SOL_CODEX_PACK_THRESHOLD_BYTES=12288
```

The global setting takes precedence. Both are byte thresholds with a minimum of 256. The plugin reads the current model from each hook event and never changes it.

## Locate data and generate a report

Codex supplies `PLUGIN_ROOT` and `PLUGIN_DATA` to installed hooks. Marketplace and cachebuster names can change their concrete paths. Use the resolved command and environment shown in `/hooks`, then run:

```bash
PLUGIN_DATA=/confirmed/plugin/data/path \
  python3 /confirmed/plugin/root/scripts/sol_hook.py --report
```

Do not infer a data path from examples when cleaning up; first confirm the active installation in `/hooks`.

## Remove

```bash
codex plugin remove sol-codex@sol-codex
```

If the CLI version offers marketplace removal, list configured marketplaces, confirm the exact `sol-codex` entry, and remove it with the command shown by `codex plugin marketplace --help`. CLI syntax can vary by Codex release.

Plugin removal intentionally leaves `PLUGIN_DATA` intact. After confirming the exact directory from `/hooks` or a prior report command, archive anything needed and delete that directory separately. Never delete a broad plugin-data parent directory.

For failures, see [troubleshooting](troubleshooting.md). For artifact sensitivity and retention, see [security](security.md).
