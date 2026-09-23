# Local A/B pilot: frozen explicit command receipts

Three matched pairs tested an explicit command adapter on two small Python repairs and one noisy failure. This is a pilot, not an estimate of general task-level savings. An earlier exploratory two-pair run used a changing prototype and is excluded from these numbers.

Both arms used fresh copies of each fixture, Codex bundled CLI `0.155.0-alpha.16.3`, `gpt-6-luna` at low effort, code mode enabled, workspace-write sandbox, three installed plugins explicitly disabled, and the same independent verifier. OFF ran `python3 inspect_fixture.py` directly. ON ran that command through a frozen copy of `scripts/receipt_command.py` with SHA-256 `067ee9df409b84f52da3646df69c693253d9de2e46c8d9da0ed9df3507f80f1d`. The ON prompt supplied the adapter path and private artifact directory. Both arms then had the same repair request. The noisy fixture printed approximately 239 KB of warnings with one error in the middle. Raw JSONL traces, fixture files, and the manifest remain local outside the repository; the [aggregate parser](ab-trace.md) produced this table.

| Task / execution order | OFF total tokens | ON total tokens | OFF seconds | ON seconds | OFF/ON verifier | First result bytes, OFF → ON |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| Ledger / ON, OFF | 87,117 | 88,515 | 22.99 | 29.52 | pass / pass | 24,729 → 1,105 |
| Config parser / OFF, ON | 77,889 | 73,112 | 24.77 | 34.31 | pass / pass | 25,987 → 1,230 |
| Noisy failure / ON, OFF | 117,170 | 74,114 | 30.23 | 24.37 | pass / pass | 238,994 → 1,373 |
| **Total** | **282,176** | **235,741 (−16.5%)** | **77.99** | **88.20 (+13.1%)** | **3 / 3 pairs pass** | — |

Cached input totaled 241,408 OFF and 209,920 ON; uncached input was 39,542 OFF and 24,299 ON. Output tokens were 1,226 OFF and 1,522 ON; cache writes were zero in these traces. Completed tool calls were 4/5 OFF/ON for ledger, 4/4 for config, and 6/4 for the noisy task. No ON command reopened an adapter artifact, and no exact command repetition was observed. The first-result byte counts come from the CLI JSON trace; they are not a direct measurement of the provider's model input.

The adapter preserved verifier success and its first result was much smaller. The primary total-token measure improved in two pairs and worsened in one; time improved only for the noisy task. Aggregate time rose despite aggregate token traffic falling. The prompts necessarily differed to request the adapter. Provider cache reuse, model trajectories, and host output handling varied, and a single run per fixture cannot separate these effects. Cached and uncached tokens may have different pricing, so these counts are not a cost estimate. The prototype stays opt-in while repeated pairs, real repositories, hidden quality checks, and uncertainty are studied.

Fixture SHA-256 digests, computed over sorted Python filenames and bytes: ledger `7befc82f8db92d200ea1e8e471bb6de33658926a6d8f25ca30d4f165facbdc4c`; config `bbf44f57d2a5650cfd5683d3b3669c4563dcbe70ed298e18d71bbe64d20d8701`; noisy `bc2dd595168c1d8f68d78be8dcd8a6f44a8014f38c47269b2503b9dbab2c9ea3`.
