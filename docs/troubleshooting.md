# Troubleshooting

## Hooks do not run

Open `/hooks` and confirm that SoL Codex is listed and trusted. Review unresolved commands or a changed trust hash. After installation or update, start a new task; existing tasks do not reload lifecycle hooks.

If `/hooks` lists no SoL Codex entries on version `0.1.1`, update to `0.1.2` and reinstall. Codex `0.155.0-alpha.16` ignores plugin hooks when a root `plugin.json` is present alongside `.codex-plugin/plugin.json`.

Confirm that `python3 --version` reports Python 3.9 or newer and that the resolved `PLUGIN_ROOT/scripts/sol_hook.py` exists. Windows is unsupported because Python `fcntl` is unavailable.

## Hooks run twice

The plugin ships hooks in `plugins/sol-codex/hooks/hooks.json`. If the same commands were also copied into a user or project `config.toml`, Codex can invoke both registrations. Remove the manual duplicate, keep the plugin-managed hooks, review `/hooks`, and start a new task.

## Hook trust changed after an update

Codex binds trust to the resolved hook content. Marketplace upgrades and new cachebuster versions can change that hash. Inspect the installed manifest and hook file, then approve the new hash only if they match the reviewed release.

## Marketplace name collision

`sol-codex@sol-codex` means plugin `sol-codex` from marketplace `sol-codex`. If another configured marketplace has the same name, list marketplaces with the commands available under `codex plugin marketplace --help`, remove or rename the conflicting source, then reinstall with the fully qualified name.

## Upgrade still loads an old version

Run:

```bash
codex plugin marketplace upgrade sol-codex
codex plugin remove sol-codex@sol-codex
codex plugin add sol-codex@sol-codex
```

Then inspect `/hooks` for the resolved cache path and start a new task. Do not edit cachebuster directories by hand.

## `PLUGIN_DATA` appears missing

`PLUGIN_DATA` is injected into installed hook commands; it is not required in an ordinary shell. Read the resolved environment in `/hooks`. The hook creates its plugin-specific directory on first invocation. If creation fails, check parent ownership, available disk space, and whether any path component is a symlink.

## Large output was not packed

Packing requires all of the following:

- tool name `Bash` or `exec_command`;
- output above the active byte threshold;
- a plain-string response or a structured response with a recognized exit status;
- a receipt smaller than the source.

`apply_patch` is tracked for verification debt but its response is not packed. The plugin also leaves output untouched when safety or efficiency conditions are not met.

Plain-string responses may be packed with `exit_code=unknown`. If Codex truncated the output before `PostToolUse`, the hook sees only the truncated text and may not reach the threshold. An unknown-status structured object remains unchanged.

## Verification debt remains

Run the smallest relevant test, lint, typecheck, or build command after the latest code patch. Free-form claims and commands outside the recognized verifier patterns do not clear debt. A failed verifier keeps the debt pending. The verifier must have a matching `PreToolUse` event; an older or unmatched result cannot clear newer debt. For plain-string Bash results, automatic status capture requires `bypassPermissions`; the hook will not rewrite a verifier in an approval-capable mode. A structured response with a recognized exit code can clear debt when matched to that start event.

Run the verifier as a single top-level shell command. Compound commands, command substitutions, wrappers, and informational invocations such as `--help` or `--version` are intentionally not trusted to clear debt because their final exit status does not prove that the check itself passed.

If Bash was interrupted by a signal or aborted under `set -e`, the status sidecar stays unset. Run the verifier again; the interrupted result cannot clear debt.

Dry-run, listing, configuration-display, allow-empty, and error-suppression options are also fail-closed across recognized tool families. Run a real check without those modes if debt should be cleared automatically.

`make` is intentionally not trusted for automatic debt clearing because Makefiles, command-line assignments, and inherited flags can suppress execution or errors. Run the underlying test, lint, compiler, or build command directly when you want the hook to recognize its status.

The same fail-closed rule applies to leading environment assignments and broad orchestration CLIs such as Gradle, Maven, Ninja, Xcodebuild, and CMake. Their outer exit status can be changed by environment, option values, or backend arguments that the hook cannot attribute safely.

## Report command

Use the confirmed paths shown in `/hooks`:

```bash
PLUGIN_DATA=/confirmed/plugin/data/path \
  python3 /confirmed/plugin/root/scripts/sol_hook.py --report
```

The report is JSON. It can show `unattributed` bytes from state written before per-model accounting was available.
