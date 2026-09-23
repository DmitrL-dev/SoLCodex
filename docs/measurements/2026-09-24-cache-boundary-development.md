# Cache-boundary development pair (2026-09-24)

This exploratory pair tests bounded retrieval in a **different repair family** from the earlier ZIP case: preserving safe plugin-cache paths during an upgrade. It is one newly seeded defect, not a held-out confirmation or a general savings estimate.

Both arms started from identical Git-backed copies of commit `017453a8de9de7907fccad9c3ed07f71f4eaeb9a` with one seeded defect in `scripts/upgrade_preserve_cache.py`: external-directory symlink targets were accepted as cache entries. The fixture checksum was `9285469f15bb658d87302325ffeadac60f3be7c7ab9c17eb5ce8c1ff4aab2f04`. An independent verifier failed on the seed before either agent ran. A 128-case diagnostic had one expected/observed mismatch at case 064, in the middle of the output. Each arm had to identify it before changing only the production file.

The arms used Codex CLI `0.155.0-alpha.16.3`, `gpt-6-luna` at low effort, code mode, ephemeral sessions, isolated working copies and Codex homes, disabled hooks, and no other installed plugins. OFF ran first with direct diagnostic output. ON ran second with the opt-in [command adapter](receipt-adapter.md) (SHA-256 `067ee9df409b84f52da3646df69c693253d9de2e46c8d9da0ed9df3507f80f1d`) and bounded search (SHA-256 `1cdbe1278e59e20655b66dc366f1e962bc2418907c4d3d5e8011d45b937abb4c`). ON was instructed how to search the exact local artifact, but not given the mismatch case or answer literal. The first search did not expose the decisive line; the second did. Traces show that both agents recovered case 064 before editing.

| Measure | Direct OFF | Bounded-search ON |
| --- | ---: | ---: |
| Independent / public verifier | pass / pass | pass / pass |
| First diagnostic result | 11,392 bytes | 1,009 bytes |
| All command results | 21,796 bytes | 11,622 bytes |
| Provider input + output tokens | 127,352 | 114,242 (−10.3%) |
| Cached / uncached input tokens | 113,920 / 12,834 | 101,632 / 11,547 |
| Output tokens | 598 | 1,063 |
| Elapsed task time | 37.33 s | 44.67 s (+19.7%) |
| Tool calls / artifact reads | 6 / 0 | 7 / 2 |

Both arms passed the independent external-link verifier and `python3 -m unittest scripts.test_upgrade_cache -q`; each changed only `scripts/upgrade_preserve_cache.py`. The [aggregate-only JSON](data/2026-09-24-cache-boundary-dev.json) contains the measured counters. Raw traces, full output, and worktrees remained in a private temporary directory; copied credentials were removed from both isolated homes.

ON used fewer total provider tokens and returned fewer command-result bytes, but took longer and generated more output tokens. The run order was fixed, prompts differ by treatment, and model trajectories differ. One pair cannot separate the adapter's effect from model variability or prove a quality floor. CLI telemetry has no actual billed-dollar field; provider tokens are not a bill. Keep this result in development evidence and do not default-enable or package the research tools yet.

A version-independent price frontier follows from the observed token categories. If a provider charges linearly per cached-input token (`p_cached`), uncached-input token (`p_uncached`), and output token (`p_output`), with no other charges, ON costs less than OFF exactly when `12,288 p_cached + 1,287 p_uncached > 465 p_output`. Equality is the break-even boundary. Cache-write tokens were zero in both arms. This is a conditional arithmetic statement about these runs, not a measured bill or a claim that a future model will have the same token mix.
