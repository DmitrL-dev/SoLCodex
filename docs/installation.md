# Installation and lifecycle

## Requirements

- Codex with plugin and hooks support
- Python 3.9 or newer: `python3` on macOS/Linux; `py -3` or `python` on Windows
- macOS, Linux, or Windows

Windows hooks use `msvcrt` file locking. The hook command tries Python 3.9+ through `py -3`, then falls back to `python` on `PATH`. Use the marketplace commands below on Windows. The portable ZIP's `install.sh` is for macOS/Linux only.

The hook contract was validated against Codex `0.155.0-alpha.9.2`; a controlled CLI probe on `0.155.0-alpha.16.3` found that `continue: false` did not guarantee a bounded nested code-mode result. Runtime compatibility is capability-based: a large plain-string `PostToolUse` result can receive feedback with `exit_code=unknown`, while a recognized top-level structured status can be used directly. On macOS/Linux in `bypassPermissions` mode, `PreToolUse` also captures actual Bash verifier status in a private sidecar. On Windows and in approval-capable modes, the hook does not rewrite commands. Host-side truncation before `PostToolUse` may keep a result below the packing threshold.

## Install from the marketplace

```bash
codex plugin marketplace add DmitrL-dev/SoLCodex
codex plugin add sol-codex@sol-codex
```

Open `/hooks` after installation. Review the commands resolved from `hooks/hooks.json`, then approve the trust prompt. Confirm a real hook event. If the host has not refreshed the active task's hook engine, reopen the task after installing.

## Update

From a checkout of this repository, use the cache-preserving updater:

```bash
python3 scripts/upgrade_preserve_cache.py
```

It saves the exact existing SoL Codex cache entries, refreshes the marketplace, reinstalls only when the version changed, and restores old paths without overwriting the new installation. This also protects tasks whose loaded hook commands predate the new bootstrap. The tool refuses unexpected cache entries or links outside this plugin's cache. Keep its backup directory if a restore fails.

For a manual update, refresh the marketplace checkout first:

```bash
codex plugin marketplace upgrade sol-codex
```

If the installed plugin remains on an old cachebuster version, reinstall it:

```bash
codex plugin remove sol-codex@sol-codex
codex plugin add sol-codex@sol-codex
```

Open `/hooks` again. Review any changed trust hash. Codex [added plugin hook refresh](https://github.com/openai/codex/pull/42990); a controlled app-server check on `0.155.0-alpha.16` observed a new plugin hook in the same process and session ID after a plugin update. The Desktop UI's enable/disable path is a separate behavior; verify a real hook event after updating. Since `0.1.8`, SoL Codex keeps an immutable copy of each invoked runtime in `PLUGIN_DATA/runtime-v1` and binds it to the task and original `PLUGIN_ROOT`. If a later plugin operation prunes that cache path, the old command still runs its bound runtime. A new root selects its new runtime when the host refreshes hooks. The loader protocol and its pinned bootstrap command are intended to stay unchanged across runtime-only releases. Tasks that loaded `0.1.7` or older commands cannot gain recovery retroactively. A stale host engine may keep invoking an already loaded hook after a plugin is disabled; the loader has no host signal to distinguish that from cache pruning. See [troubleshooting](troubleshooting.md) for edge cases.

## Environment overrides

Launch Codex from an environment containing any overrides you need:

```bash
export SOL_CODEX_ASTRA_PACK_THRESHOLD_BYTES=4096
export SOL_CODEX_PACK_THRESHOLD_BYTES=6144
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
