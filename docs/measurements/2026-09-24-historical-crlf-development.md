# Historical CRLF loader development pair (2026-09-24)

This exploratory pair used the real pre-fix tree of commit `7970ce8` (parent `15583dd84b3e0c35e5ffaec84508c35fce709c2d`). A `git archive` export was initialized as a new single-commit repository, removing the future fix from the fixture's Git history. The fixture checksum was `3cc86181946b8c673ebae6e411871740fb97b2e2a9719d1353ff7007560bce34`.

The historical defect was that generated hook commands pinned raw bootstrap bytes, so an LF source and CRLF runtime copy could disagree. A task-specific 128-case diagnostic placed the only expected/observed mismatch at case 064. ON ran first with the frozen [command adapter](receipt-adapter.md) and bounded search; OFF ran second with the diagnostic directly. Both used Codex CLI `0.155.0-alpha.16.3`, `gpt-6-luna` at low effort, code mode, ephemeral sessions, isolated Git-backed copies and Codex homes, disabled hooks, and no installed plugins. The adapter SHA-256 was `067ee9df409b84f52da3646df69c693253d9de2e46c8d9da0ed9df3507f80f1d`; bounded search SHA-256 was `1cdbe1278e59e20655b66dc366f1e962bc2418907c4d3d5e8011d45b937abb4c`.

| Measure | Direct OFF | Bounded-search ON |
| --- | ---: | ---: |
| First diagnostic result | 7,369 bytes | 894 bytes |
| All command results | 46,846 bytes | 47,589 bytes |
| Provider input + output tokens | 168,476 | 222,117 (+31.8%) |
| Cached / uncached input tokens | 135,680 / 32,097 | 196,096 / 24,254 |
| Output tokens | 699 | 1,767 |
| Elapsed agent time | 40.12 s | 63.10 s (+57.3%) |
| Tool calls / artifact reads | 5 / 0 | 17 / 2 |
| Initial narrow verifier / existing tests | fail / pass | pass / pass |
| Full behavior matrix | fail | fail |

Both agents found case 064 before editing. ON searched its exact artifact twice, changed the generator and regenerated `hooks.json`; OFF changed only the generator despite an explicit regeneration request. The initial hidden verifier tested the generated LF-source command against a CRLF runtime and checked generator/config synchronization. It passed ON and failed OFF. Existing eight hook-command tests passed for both.

Reviewing ON's diff revealed that it normalized runtime bytes before checking the pin but still computed the pin from unnormalized source bytes. We therefore ran a **posthoc** matrix: generate commands from an LF or CRLF bootstrap, then execute them against an LF or CRLF runtime copy. The historical fixed code passed all four cells. Neither experimental arm did:

| Generator source / runtime copy | Historical fixed code | Direct OFF | Bounded-search ON |
| --- | --- | --- | --- |
| LF / LF | pass | pass | pass |
| LF / CRLF | pass | fail | pass |
| CRLF / LF | pass | fail | fail |
| CRLF / CRLF | pass | pass | fail |

The [aggregate-only JSON](data/2026-09-24-historical-crlf-dev.json) records both the initial narrow result and the posthoc full matrix. The matrix changed task acceptance for ON from apparent success to failure; **neither arm is an accepted repair**. The two new repository tests cover source-line-ending invariance and rejection of changed bootstrap content. The test matrix was discovered after seeing agent output, so this pair cannot become confirmatory evidence retroactively. ON's absolute adapter/search paths also pointed to the live repository containing the historical fix; its trace shows only tool execution through those paths, but filesystem-level isolation from that fix was not enforced.

The shorter first result did not shorten the trajectory: ON returned slightly more command-result bytes in total and used 31.8% more provider tokens. Since both repairs failed the full behavior check, this is a development failure and a verifier-design lesson, not an estimate of tokens per accepted task. The task also repeats the same artificial 128-case/one-middle-mismatch retrieval pattern as the first two pilots. Future confirmation must use natural issue diagnostics and independently accepted behavior, not count this as a third independent success. Raw traces and complete command output remained private; copied credentials were removed.
