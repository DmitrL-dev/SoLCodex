# SoL Codex

SoL Codex is a local Codex Desktop plugin that archives eligible large tool output, requests bounded non-blocking receipt feedback, and tracks whether code changes have been verified. It is an independent, SoL-inspired implementation for Codex hooks.

The plugin can change direct model-visible tool output and, in `bypassPermissions` mode, wraps recognized verifier commands to capture their exit status. Code-mode scripts may still receive the original nested result. The plugin does not select a model, initiate compaction, call another model, or claim changes to token use, billing, quota, or answer quality.

[Русская версия](README.ru.md) · [Installation](docs/installation.md) · [Security](docs/security.md) · [Troubleshooting](docs/troubleshooting.md)

![SoL Codex runtime and verification architecture](assets/architecture.svg)

## Quick start

Prerequisites: Codex with plugin and hooks support and Python 3.9 or newer (`python3` on macOS/Linux, `py -3` or `python` on Windows). The hook contract was validated against Codex `0.155.0-alpha.9.2`.

```bash
codex plugin marketplace add DmitrL-dev/SoLCodex
codex plugin add sol-codex@sol-codex
```

Open `/hooks`, review the resolved commands, and trust the plugin when Codex asks. Hooks can refresh in the same task on a Codex build with plugin hook hot reload; confirm the new release with a real hook event. The stable loader also recovers a previously initialized task after its old plugin cache is pruned. See [installation](docs/installation.md) for updates, removal, and limits.

## What it does

- `PostToolUse` watches shell and patch results. Eligible extracted text larger than 6 KiB is written under `PLUGIN_DATA`; `gpt-6-astra` uses a 4 KiB threshold. For structured results, the artifact is extracted text, not a byte-for-byte copy of the response object.
- Direct tool results can use a bounded receipt with status (or `unknown`), hashes, diagnostic lines, and a small preview. In code mode the original nested result may remain available. Receipt redaction is best effort; the exact local artifact is not redacted.
- Successful code patches create verification debt. A recognized verifier clears it only when its matching `PreToolUse` ran after the latest code patch and a structured exit code or private status sidecar reports exit code zero. Sidecars are a workflow aid, not a security attestation against malicious project code.
- `PreCompact`, `PostCompact`, and `SessionStart` keep the debt reminder across Codex compaction. `Stop` warns about pending or failed verification without blocking the final answer; the agent must report checks accurately.
- A pinned loader saves the invoked hook runtime under `PLUGIN_DATA` so an initialized task can keep working after Codex prunes its old plugin cache. A refreshed hook root can select a new runtime in the same task.
- Aggregate source, receipt, and candidate byte differences are recorded per model. These byte counts do not measure code-mode model input, tokens, cost, quota, latency, or quality.

Plain-string `PostToolUse` results can receive feedback if the hook sees enough bytes, but their receipt says `exit_code=unknown` unless a verifier sidecar supplies status. `PreToolUse` records the current code-change generation for recognized verifiers. On macOS/Linux in `bypassPermissions` mode only, it also wraps recognized Bash verifiers to capture status; on Windows or in approval-capable modes, it does not rewrite commands. Structured responses can supply status directly. Host-side truncation before `PostToolUse` limits what the plugin can archive or count. Since 0.1.9 the hook uses `continue: false` instead of `decision: block`, so it does not intentionally reject a code-mode Promise after execution. Current code-mode hosts may still pass the original result to the running script; use explicit capture for a guaranteed bounded nested result.

The full flow is documented in [architecture](docs/architecture.md).

## Configuration

Set environment variables before launching Codex:

```bash
export SOL_CODEX_ASTRA_PACK_THRESHOLD_BYTES=4096
export SOL_CODEX_PACK_THRESHOLD_BYTES=6144
```

`SOL_CODEX_ASTRA_PACK_THRESHOLD_BYTES` applies only when the current model is `gpt-6-astra`. `SOL_CODEX_PACK_THRESHOLD_BYTES` is a global override and takes precedence for every model. Values are bytes and are clamped to at least 256. The plugin does not select a model; the Astra profile is active only when Codex already reports `gpt-6-astra` as the current model.

## Reports

The [context-efficiency research map](docs/research/2026-09-23-context-efficiency.md) compares related work and ranks experiments. It does not change the released plugin or claim that published results transfer to SoL Codex.

The [hook result boundary study](docs/research/2026-09-24-hook-result-boundary.md) records the code-mode limitation, related upstream work, and the safe control-flow change in 0.1.9.

The [existing-task hook refresh study](docs/research/2026-09-24-live-hook-refresh.md) records why the current turn retained an old hook after upgrade and why the next turn in the same task is the first refresh attempt to verify.

The [real-repository receipt pilot](docs/measurements/2026-09-24-real-repo-pilot.md) records two paired repairs, their aggregate trace data, and invalid exploratory attempts. Both pairs passed independent checks; token and time effects differed by task.

The [A/B trace accounting guide](docs/measurements/ab-trace.md) documents an aggregate-only `codex exec --json` parser, private manifest format, quality checks, and limits of the earlier five-pair pilot.

An [explicit command receipt prototype](docs/measurements/receipt-adapter.md) captures output before tool return and preserves code-mode control flow in a local test. It is opt-in, POSIX-only, and separate from the installed plugin while its task-level value is measured.

Its [frozen three-pair pilot](docs/measurements/2026-09-24-explicit-adapter-pilot.md) passed all task verifiers and used fewer aggregate total tokens, but took longer overall. Per-task results varied; a general efficiency gain is not established.

Run the hook script with the same `PLUGIN_DATA` directory shown by the installed hook environment:

```bash
PLUGIN_DATA=/path/to/your/sol-codex-data \
  python3 /path/to/installed/sol-codex/scripts/sol_hook.py --report
```

The JSON report contains aggregate byte counters only. Marketplace names affect installed cache and data paths, so copy the resolved locations from `/hooks` rather than guessing them. The [measurement methodology](docs/savings.md) explains the formulas and limitations.

In a [local historical snapshot](docs/savings.md), 355 receipt events contained 8,158,596 extracted source bytes and emitted 735,540 receipt bytes: a local difference of 7,423,056 bytes (90.98%). This is not a measurement of model input, tokens, money, quota, time, or unchanged task quality. The snapshot predates plain-string packing and the 0.1.9 delivery change; it includes only outputs selected by the active byte threshold and net-savings guard, with 338 legacy events lacking model attribution. SoL-Pi's results do not apply to this plugin.

## Security and limits

Exact local artifacts can contain credentials, source code, or personal data. The plugin never uploads them. It uses private filesystem modes on macOS/Linux and inherited NTFS ACLs on Windows; anyone with access to the account or storage may still read them. Review [docs/security.md](docs/security.md) before enabling hooks on sensitive work.

On Windows, hooks use `msvcrt` for file locking and inherit NTFS permissions for local artifacts. Bash verifier wrapping and its Unix signal tests apply only on macOS and Linux; Windows verification can use a recognized structured exit status from `exec_command`. Hook failures are fail-open so a plugin error does not take down Codex; verification enforcement is advisory when the hook is degraded. See [docs/troubleshooting.md](docs/troubleshooting.md) for hook conflicts, trust prompts, cache versions, and missing data paths.

## Remove

```bash
codex plugin remove sol-codex@sol-codex
```

Removing the plugin does not delete local artifacts. Remove the confirmed `PLUGIN_DATA` directory separately. Optional marketplace removal and safe cleanup are covered in [installation](docs/installation.md).

## Upstream and license

The design is conceptually inspired by [NVlabs/SoL-Pi](https://github.com/NVlabs/SoL-Pi), but this repository is an independent Codex-specific implementation with no copied SoL-Pi code, no fork lineage, and no endorsement by NVIDIA or OpenAI. See [upstream attribution](docs/upstream.md).

SoL Codex is licensed under the [MIT License](LICENSE).
