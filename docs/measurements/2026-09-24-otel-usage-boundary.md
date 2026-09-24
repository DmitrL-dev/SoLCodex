# Codex OTel usage boundary: development probes (2026-09-24)

The independent A/B protocol requires every attempted model request to be accounted for, including interruption and retries. `codex exec --json` alone cannot establish that. [Official Codex monitoring documentation](https://learn.chatgpt.com/docs/agent-approvals-security#monitoring-and-telemetry) says OTel emits request and stream events, with token counts on `response.completed`, and batches events for export with a flush on shutdown. This note tests what that gives us on one host. It is a development telemetry probe, **not** an efficiency A/B or a bill.

## Setup and observed counts

All runs used `codex-cli 0.155.0-alpha.16.3`, `gpt-6-luna` at low effort, isolated ephemeral Codex homes, disabled hooks/plugins, an allowlisted model proxy, and a local OTLP/HTTP JSON collector. The two normal runs each asked the agent to execute `pwd` once. The HTTP run selected a custom provider with `requires_openai_auth=true` and `supports_websockets=false`; the WebSocket run used the built-in provider. They were sequential development probes, with uncontrolled cache state, and do not compare task efficiency. The interruption run used HTTP, requested `sleep 30`, and sent `SIGKILL` to the CLI's process group as soon as the command item started. The collector remained running.

| Run | Model-request events | OTel `response.completed` with usage | OTel input / output | CLI `turn.completed` input / output | Observation |
| --- | ---: | ---: | ---: | ---: | --- |
| [WebSocket](data/2026-09-24-otel-websocket-dev.json) | 3 | 3 | 35,533 / 53 | 24,747 / 53 | One OTel completion with 10,786 input, zero output and zero cached input occurred immediately before `startup_prewarm_websocket_warmup`; its input is absent from CLI turn usage. |
| [HTTP](data/2026-09-24-otel-http-dev.json) | 2 | 2 | 24,740 / 52 | 24,740 / 52 | OTel and CLI totals matched for this completed run. Two additional `response.completed`-kind OTel records had no usage fields and were not counted as zero-token requests. |
| [HTTP, killed](data/2026-09-24-otel-kill-http-dev.json) | 1 | 0 | Unknown | Unknown | The collector received a `/responses` request event and stream-start events; neither an OTel usage completion nor CLI turn usage arrived before `SIGKILL`. |

The WebSocket observation matches the mechanism described in [upstream issue #46975](https://github.com/openai/codex/issues/46975): a `generate=false` startup prewarm can yield its own usage-bearing `response.completed` outside `turn.completed`. Neither that issue nor this probe establishes whether these warmup tokens are billed or charged against a subscription limit. The 10,786 figure is **provider-reported event usage**, not measured money. Changing transport altered cache behavior, so the two normal runs are not a price comparison.

The killed run demonstrates why a local OTLP collector is not a complete ledger: even a request that reached the provider can lose its token event when the CLI is terminated. The observed OTel records had no request/response identity attribute suitable for a reliable request-to-completion join or deduplication. Equality of two aggregates in the normal HTTP run does not prove every accepted request was counted. The collector receives only events exported by the CLI; it cannot recover a completion still buffered in the killed process. An independent provider-side ledger, or an equivalently validated final-usage mechanism, is still required for a complete claim. Missing usage remains unknown, never zero. A conservative finite bound would support a **bounded** result, not complete telemetry.

## Reproduction and privacy boundary

[`scripts/audit_otel_usage.py`](../../scripts/audit_otel_usage.py) reads OTLP JSON batches and an optional CLI JSONL trace, emits only aggregate request/event counts and token categories, and always sets `end_to_end_usage_complete=false`. Its [synthetic tests](../../scripts/test_audit_otel_usage.py) cover WebSocket warmup mismatch, an HTTP completion record without usage, abrupt termination, cache-category validation, and removal of sample private fields. Example:

```sh
python3 scripts/audit_otel_usage.py --cli-trace PRIVATE_TRACE.jsonl \
  --process-exit-code 0 PRIVATE_OTLP_BATCHES.json > aggregate.json
```

The raw OTLP batches are intentionally private: even with `log_user_prompt=false`, this host's events included account identifiers and tool arguments/output. The public JSON files are derived aggregates, and private traces, exact prompt/tool data, and authentication material are not published. The probe's local collector used a private workspace and kept raw logs outside the repository. This audit does not authenticate a provider bill, classify unmetered completions as free, or prove model-input compression.
