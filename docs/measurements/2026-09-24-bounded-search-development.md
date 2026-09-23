# Bounded-search development pair (2026-09-24)

This is a **development observation on a previously used ZIP fixture**, not held-out evidence or a general savings estimate. It follows the [negative retrieval-forcing pair](../research/2026-09-24-retrieval-economics.md), in which unrestricted `rg` read the whole saved diagnostic twice.

The two arms used the same Git-backed seeded repository (`fixture_sha256=97c8ebfd21a2c7d8c3824482dfc07fc56da9ea8c7f5b41110ea0d2c89d9b7873`), Codex CLI `0.155.0-alpha.16.3`, `gpt-6-luna` at low effort, code mode, ephemeral sessions, `workspace-write`, and disabled hooks and other plugins. ON ran first, OFF second in separate isolated Codex homes. The ON command used the frozen [command adapter](receipt-adapter.md), SHA-256 `067ee9df409b84f52da3646df69c693253d9de2e46c8d9da0ed9df3507f80f1d`, and the bounded search implementation from commit `db7a1d4` (SHA-256 `ed5e84cfdd38ebe2de0a996076130544da518be6752d42b8996c756ab772b563`). The current search source has since gained line-range and omission reporting; it was **not** the code exercised by this pair.

Each agent was required to identify the one mismatch in a 128-case diagnostic and repair only `scripts/validate_repository.py`. The seeded baseline failed an independent symlink-type verifier before the model runs. The same verifier and the focused public release-tool test passed after each arm. Raw traces, complete command output, per-arm repositories, and copied credentials remained in private temporary directories; the copied credentials were removed. The [aggregate-only JSON report](data/2026-09-24-bounded-search-dev.json) excludes private paths and raw content.

| Measure | Direct OFF | Bounded-search ON |
| --- | ---: | ---: |
| Independent / public verifier | pass / pass | pass / pass |
| First diagnostic result | 13,055 bytes | 1,076 bytes |
| All command results | 38,995 bytes | 12,779 bytes |
| Provider input + output tokens | 212,475 | 147,203 (−30.7%) |
| Cached / uncached input tokens | 190,976 / 20,363 | 130,816 / 15,110 |
| Elapsed task time | 56.34 s | 50.27 s (−10.8%) |
| Tool calls / artifact reads | 7 / 0 | 10 / 3 |

ON did use the search tool without being given the answer literal. Its first query had no matches; its second command failed from shell argument quoting; its third retrieved case 064 exactly. OFF ran the diagnostic twice. ON used more tool calls but returned far fewer command-result bytes. The first output was shortened by design, while the lower total provider tokens and time are *observed whole-trajectory results* for this pair.

The task and diagnostic had already informed the search tool's design, ON was run first, the prompts differ by treatment, and there is only one pair. Provider cache carryover, model variability, and changed trajectories prevent a causal or general cost claim. Codex telemetry here has provider token categories but no actual billed-dollar field. A confirmatory study needs untouched tasks, balanced order, repeated independent defects, and a declared quality floor. Do not package or default-enable the research tools from this result.
