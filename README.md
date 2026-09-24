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
- The hook can emit a bounded receipt with status (or `unknown`), hashes, diagnostic lines, and a small preview. On the tested host, the original result remained visible in both direct and code-mode probes despite non-blocking feedback. Receipt redaction is best effort; the exact local artifact is not redacted.
- Successful code patches create verification debt. A recognized verifier clears it only when its matching `PreToolUse` ran after the latest code patch and a structured exit code or private status sidecar reports exit code zero. Sidecars are a workflow aid, not a security attestation against malicious project code.
- `PreCompact`, `PostCompact`, and `SessionStart` keep the debt reminder across Codex compaction. `Stop` warns about pending or failed verification without blocking the final answer; the agent must report checks accurately.
- A pinned loader saves the invoked hook runtime under `PLUGIN_DATA` so an initialized task can keep working after Codex prunes its old plugin cache. A refreshed hook root can select a new runtime in the same task.
- Aggregate source, receipt, and candidate byte differences are recorded per model. These byte counts do not measure model input, tokens, cost, quota, latency, or quality.

Plain-string `PostToolUse` results can receive feedback if the hook sees enough bytes, but their receipt says `exit_code=unknown` unless a verifier sidecar supplies status. `PreToolUse` records the current code-change generation for recognized verifiers. On macOS/Linux in `bypassPermissions` mode only, it also wraps recognized Bash verifiers to capture status; on Windows or in approval-capable modes, it does not rewrite commands. Structured responses can supply status directly. Host-side truncation before `PostToolUse` limits what the plugin can archive or count. Since 0.1.9 the hook uses `continue: false` instead of `decision: block`, so it does not intentionally reject a code-mode Promise after execution. The tested host still exposed the original result in direct and code-mode probes; use explicit capture for a guaranteed bounded result.

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

The [Unreal Agent review](docs/research/2026-09-24-unreal-agent.md) checks its reported 39.23% Terminal-Bench cost difference against the linked Harbor run and separates asynchronous harness orchestration from this plugin's receipt mechanism.

The [RRSI research review](docs/research/2026-09-24-rrsi.md) adds an adaptive-overfitting gate for harness experiments: record failed hypotheses, freeze held-out tasks, and compare full-task cost with the unevolved baseline. The paper's lower token use is relative to another evolved harness, not evidence of SoL Codex savings.

The [retrieval-economics research note](docs/research/2026-09-24-retrieval-economics.md) reviews newer agent-memory evidence and tests whether an agent can recover a decisive line hidden by a bounded receipt. It keeps quality, provider tokens, retrieval work, and elapsed time separate.

A separate [bounded artifact-search prototype](docs/measurements/receipt-adapter.md#bounded-artifact-search) prevents broad literal queries from replaying an entire saved command result. It is opt-in research code, outside the installed plugin.

The [bounded-search development pair](docs/measurements/2026-09-24-bounded-search-development.md) passed both repair checks with fewer total provider tokens and less time on one previously used fixture; a [cost-frontier protocol](docs/research/2026-09-24-cost-frontier.md) defines the held-out tests needed before claiming reliable savings.

A [cache-boundary development pair](docs/measurements/2026-09-24-cache-boundary-development.md) passed both repair checks on a different defect with 10.3% fewer provider tokens, while taking 19.7% longer. It is exploratory evidence, not a billing or general-efficiency claim.

A [historical CRLF development pair](docs/measurements/2026-09-24-historical-crlf-development.md) exposed a verifier gap: both repairs failed the complete source/runtime newline matrix, even though ON passed the initial narrow check. ON used 31.8% more provider tokens. The full matrix now informs the regression tests and future task acceptance.

A [macOS isolation probe](docs/measurements/2026-09-24-agent-isolation-probe.md) found a development path for denying agents access to a live fixed checkout during historical-task experiments; broader filesystem isolation still needs validation before a confirmatory campaign.

The [hook result boundary study](docs/research/2026-09-24-hook-result-boundary.md) records the direct and code-mode limitations, related upstream work, and the safe control-flow change in 0.1.9.

A [next-request probe](docs/measurements/2026-09-24-next-request-boundary-development.md) observed that a temporary `PreToolUse` rewrite kept an output-only marker out of the next model request in both direct and code mode. It was one simple command, not installed-plugin savings or a transparent wrapper.

An [exposed packaging repair pair](docs/measurements/2026-09-24-packaging-prehook-repair-development.md) tested the temporary rewrite in a complete repair. Both patches matched the historical fix's AST and passed external checks, but the receipt run used 45.8% more proxy-observed input/output tokens and took 88.7% longer. The original adherence gate was revised after both runs, so this is development evidence, not a confirmatory saving.

An [exposed Click selector repair pair](docs/measurements/2026-09-24-click-selector-repair-development.md) found that a revised receipt preview showed the decisive pytest failure but its patch passed only 7/9 external behavior checks, versus 9/9 for the old selector. The revised run used 11.4% more observed tokens and took 49.0% longer. This is a negative development result, not a general efficiency estimate.

A separate [packaged-plugin request probe](docs/measurements/2026-09-24-packaged-plugin-request-development.md) found that the public 0.1.9 hook recorded 6,967 local `saved_bytes` while an output-only marker from the verifier result still reached the next model request. Local packing counters must not be read as model-input savings.

The [independent confirmation draft](docs/research/2026-09-24-confirmation-campaign-draft.md) specifies a once-only 360-task planning target, stronger verifier qualification, isolation, and request accounting. It is a design, not completed evidence.

A [disposable Linux containment preflight](docs/measurements/2026-09-24-linux-worker-preflight.md) passed 25 synthetic checks in CI, with a path-free aggregate and a pinned-log reducer. It used no model requests and does not qualify the confirmation worker or its provider accounting.

The [metadata-only candidate ranking gate](docs/research/2026-09-24-candidate-ranking.md) published prior development exposures and a strict ranking script before the pinned metadata export. No candidate task has been selected, and no remaining candidate's gold or test patch has been inspected for this gate.

The pinned metadata export produced [an aggregate of 32,079 rows](docs/measurements/data/2026-09-24-candidate-metadata-export.json); 282 directly exposed rows were excluded. Repository lineage and task eligibility remain unreviewed.

A [phase-reset development pilot](docs/measurements/2026-09-24-phase-reset-development.md) tested a fresh edit session against resuming after a shared diagnosis on one exposed packaging task. Both repairs passed external checks; the smaller reset CLI token total is exploratory and does not measure the installed plugin or provider billing.

The [OTel usage probes](docs/measurements/2026-09-24-otel-usage-boundary.md) found WebSocket startup usage outside CLI turn totals and missing usage after abrupt termination. Published aggregates and a sanitized parser preserve these limits; they are not provider billing records.

An [HTTP proxy development probe](docs/measurements/2026-09-24-http-proxy-usage-development.md) observed final response usage after the CLI was killed. A live SQLite journal preserved that case, while killing the proxy after upstream HTTP 200 left a pending request with unknown usage. None of these results validates provider billing or general savings.

The [provider-reconciliation note](docs/research/2026-09-24-provider-reconciliation.md) separates ChatGPT-plan accounting from API Platform usage and lists the missing access and joins for a complete claim.

The [existing-task hook refresh study](docs/research/2026-09-24-live-hook-refresh.md) records a successful same-task, next-turn refresh after upgrade, without restarting the app. The code-mode script still received the original result.

The [real-repository receipt pilot](docs/measurements/2026-09-24-real-repo-pilot.md) records two paired repairs, their aggregate trace data, and invalid exploratory attempts. Both pairs passed independent checks; token and time effects differed by task.

[Natural historical development runs](docs/measurements/2026-09-24-natural-history-development.md) found that Click and packaging ON agents never invoked the opt-in adapter and used more tokens despite passing external checks. A first SQLGlot pair captured a large traceback but lost OFF usage to a timeout. A second, fully metered SQLGlot pair passed 13/13 in OFF while ON stopped without a repair. None establishes a compression or cost saving.

A separate [SymPy development pair](docs/measurements/2026-09-24-natural-history-development.md#sympy-arraysymbol-apparent-token-reduction-post-hoc-regression) used the adapter and passed 35/35 frozen checks and 48/48 selected upstream tests in both arms. ON used 31.8% fewer CLI-reported tokens while taking 5.4% longer. Later checks found the same `ZeroArray`/`OneArray` behavior change in both repairs, but the [API-contract reassessment](docs/measurements/2026-09-24-sympy-contract-reassessment.md) could not establish whether that change is unacceptable. Repair acceptance and quality-adjusted savings remain unresolved.

An [exposed source-level verifier probe](docs/measurements/2026-09-24-sympy-verifier-known-miss-development.md) reproduces the score difference: the shared-superclass variant passes the original 35/35 and selected upstream 48/48, but fails two later compatibility checks whose acceptance status is disputed. The verifiers and source-variant generator are public.

A [diagnostic mutation pilot](docs/measurements/2026-09-24-sympy-diagnostic-mutants-development.md) records six distinct v2 failure vectors with source and report hashes. Several variants were tailored to known checks or share one failure mechanism, so the six-plausible-wrong-fix qualification gate remains unmet.

An [action-fusion mechanism pair](docs/measurements/2026-09-24-action-fusion-mechanism-development.md) passed 34/34 external checks in both arms, but ON used 98.7% more proxy-observed tokens and its combined shell edit failed before the verifier ran on the unchanged parent. Published candidate sources and a Linux behavior replay make the negative result inspectable. No general saving is established.

A [nested-tool gate probe](docs/measurements/2026-09-24-nested-tool-gate-development.md) then confirmed that this Codex host can run a patch tool and verifier tool sequentially in one programmatic call, and skip the verifier when the patch throws. It did not exercise separate approvals or measure model savings. The skill and startup guidance now exclude shell edit-plus-test bundling.

Two [instruction-blinded repair attempts](docs/measurements/2026-09-24-sympy-blind-repairs-development.md) later produced the same shared-superclass patch. Its clean replay passes v1 35/35 and upstream 48/48 but scores 35/37 on v2. The repeated patch is plausible on this exposed task; its acceptability remains unresolved.

The [A/B trace accounting guide](docs/measurements/ab-trace.md) documents an aggregate-only `codex exec --json` parser, private manifest format, quality checks, and limits of the earlier five-pair pilot.

An [explicit command receipt prototype](docs/measurements/receipt-adapter.md) captures output before tool return and preserves code-mode control flow in a local test. Use it for expected large or noisy output; short checks can cost more bytes as receipts. It is opt-in, POSIX-only, and separate from the installed plugin while its task-level value is measured.

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
