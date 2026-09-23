# Installation and lifecycle

## Requirements

- Codex with plugin and hooks support
- Python 3.9 or newer: `python3` on macOS/Linux; `py -3` or `python` on Windows
- macOS, Linux, or Windows

Windows hooks use `msvcrt` file locking and a bundled launcher for all seven lifecycle events. The launcher tries Python 3.9+ through `py -3`, then falls back to `python` on `PATH`. Use the marketplace commands below on Windows. The portable ZIP's `install.sh` is for macOS/Linux only.

The hook contract was validated against Codex `0.155.0-alpha.9.2`. Runtime compatibility is capability-based: a large plain-string `PostToolUse` result can be packed with `exit_code=unknown`, while a recognized top-level structured status can be used directly. On macOS/Linux in `bypassPermissions` mode, `PreToolUse` also captures actual Bash verifier status in a private sidecar. On Windows and in approval-capable modes, the hook does not rewrite commands. Host-side truncation before `PostToolUse` may keep a result below the packing threshold.

## Install from the marketplace

```bash
codex plugin marketplace add DmitrL-dev/SoLCodex
codex plugin add sol-codex@sol-codex
```

Open `/hooks` after installation. Review the commands resolved from `hooks/hooks.json`, then approve the trust prompt. Create a new task to load `SessionStart` and the other lifecycle hooks.

## Update

From a checkout of this repository, use the cache-preserving updater:

```bash
python3 scripts/upgrade_preserve_cache.py
```

It saves the exact existing SoL Codex cache entries, refreshes the marketplace, reinstalls only when the version changed, and restores old paths without overwriting the new installation. This keeps hook commands already bound by open tasks pointing at their original files. The tool refuses unexpected cache entries or links outside this plugin's cache. Keep its backup directory if a restore fails.

For a manual update, refresh the marketplace checkout first:

```bash
codex plugin marketplace upgrade sol-codex
```

If the installed plugin remains on an old cachebuster version, reinstall it:

```bash
codex plugin remove sol-codex@sol-codex
codex plugin add sol-codex@sol-codex
```

Open `/hooks` again. Changed hook files produce a new trust hash and must be reviewed. A live task retains its loaded hook definitions; the updater keeps its old script paths available so work can continue. In a controlled CLI check with hook trust bypassed, a **new Codex process** running `codex exec resume <session-id>` reloaded a changed project hook while keeping the same session ID and history. This has not been established for updated plugin hooks in Desktop. A [reported Desktop bug](https://github.com/openai/codex/issues/36605) shows that disabling and re-enabling a plugin in an open task can leave its old hook engine active. If you restart Desktop and reopen the same task, verify the resolved hooks in `/hooks` and a real hook event before relying on the new release. A manual remove/add deletes old cache paths; use the updater while old tasks are open. See [troubleshooting](troubleshooting.md) if an old task is already blocked.

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
