# HTTP proxy usage boundary: development probes (2026-09-24)

An independent local HTTP process can observe a final model response after the Codex CLI is terminated. This is a narrow development result, not an A/B comparison, a durable production ledger, or evidence of billed cost.

## Setup and observations

Both runs used `codex-cli 0.155.0-alpha.16.3`, `gpt-6-luna` at low effort, ChatGPT authentication in an isolated temporary Codex home, disabled hooks/plugins and selected optional features, and a custom HTTP model provider whose `base_url` pointed to a loopback server. Both prompts requested no tool use. That server forwarded only `POST /backend-api/codex/responses` to the fixed HTTPS upstream `chatgpt.com`; it did not follow redirects or persist request bodies, credentials, response text, or raw SSE. A strict local sandbox kept the CLI's model route on loopback. The proxy process itself remained alive during the CLI interruption.

An initial loopback sink returned `503` and saw one authenticated POST to the custom provider route; Codex failed as expected. In the two successful upstream runs:

| Run | CLI result | Proxy attempts and final responses | Proxy-observed usage (input / output / cached input) | CLI completed-turn usage | Client disconnected |
| --- | --- | --- | ---: | ---: | --- |
| [Normal](data/2026-09-24-proxy-normal-dev.json) | Exit 0; one completed turn | 1 / 1 | 9,873 / 5 / 3,840 | 9,873 / 5 / 3,840 | No |
| [CLI killed](data/2026-09-24-proxy-killed-dev.json) | `SIGKILL`; no completed turn | 1 / 1 | 9,881 / 662 / 3,840 | Unknown | Yes |

The normal prompt requested exactly `OK`. The interrupted prompt requested a 500-word essay. The harness killed the CLI process group as soon as the proxy read its first upstream stream bytes. The proxy observed `response.completed` **12.352 seconds after the kill**. The two prompts and cache states differ; their token counts must not be compared as treatment effects.

The [fixed-field audit script](../../scripts/audit_proxy_probe.py) reduces private proxy metadata and CLI JSONL traces to the published JSON aggregates. The private inputs contain local paths and potentially sensitive trace content; raw requests and responses were not retained. The audit has synthetic checks for normal completion, interruption, missing usage, invalid cache totals, private-field exclusion, and duplicate response hashes. These runs each had exactly one attempt, so their metadata did not include response hashes; the audit treats multi-attempt input without those hashes as ambiguous. `all_attempts_have_observed_usage=true` applies only to the one attempt in each probe, not to an entire production run or a provider bill.

## Ledger boundary

The [SQLite attempt-journal prototype](../../scripts/usage_attempt_ledger.py) writes an attempt intent before completion, commits final usage atomically, leaves interrupted attempts pending, rejects duplicate response IDs, and never infers zero usage from a missing completion. Its [tests](../../scripts/test_usage_attempt_ledger.py) reopen the journal after a forced process kill. **It was not connected to the live forwarding proxy in these probes.**

The proxy's decision to continue reading after the CLI disconnect changes execution: it can allow the upstream model to generate more tokens than an unproxied cancelled request. A future A/B must apply the same policy to both arms. The observed `usage` is an SSE response field, not an independent record of invoice charges or ChatGPT quota debit. Proxy death, upstream interruption without a final usage event, retries, deduplication, and authorization refresh still need end-to-end tests. Those attempts remain unknown until reconciled against an independent provider record. No general savings claim follows from these probes.
