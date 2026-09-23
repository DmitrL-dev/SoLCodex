# Real-repository receipt pilot (2026-09-24)

Two matched pairs used seeded defects in the public SoL Codex `v0.1.9` repository. This is a small feasibility check, not an estimate of general savings. The [aggregate-only JSON report](data/2026-09-24-real-repo-pilot.json) contains the paired trace accounting; raw JSONL traces and command artifacts remain private.

Both arms used Codex CLI `0.155.0-alpha.16.3`, `gpt-6-luna` at low effort, code mode, `workspace-write`, ephemeral sessions, and disabled hooks and installed plugins. ON used a frozen copy of the [explicit adapter](receipt-adapter.md) with SHA-256 `067ee9df409b84f52da3646df69c693253d9de2e46c8d9da0ed9df3507f80f1d` for the initial diagnostic and subsequent large test commands. OFF ran commands directly. The adapter path and instructions add prompt content to ON; this overhead is included in the measured result. Each arm began from a fresh copy of the same seeded fixture. External hidden checks and public tests ran after the model completed and are excluded from elapsed task time.

| Fixture and order | OFF provider tokens | ON provider tokens | OFF seconds | ON seconds | First result bytes OFF → ON | Hidden / public verification |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Missing token field, OFF → ON | 62,061 | 64,699 | 28.18 | 67.22 | 640 → 1,066 | both arms pass |
| ZIP symlink type, ON → OFF | 161,518 | 104,107 | 45.43 | 35.77 | 13,104 → 1,151 | both arms pass |
| **Total** | **223,579** | **168,806 (−24.5%)** | **73.61** | **102.99 (+39.9%)** | — | **2 / 2 pairs pass** |

The first fixture changed `scripts/ab_trace.py` so a missing token field became zero. Its initial command was the existing `test_ab_trace.py` suite; an external verifier checked missing, actual zero, boolean, and negative values. Fixture SHA-256: `920320e5ed6782ba8d53f53aadd37db2d78b92b1372c3560ab33678b02cbb326`.

The second fixture removed the ZIP member-type rejection in `scripts/validate_repository.py`. A diagnostic scanned 128 controlled archives and reported the symlink case in the middle. The external verifier rebuilt regular, symlink, and FIFO archives independently; the public release-tool suite also passed after each repair. Fixture SHA-256, excluding Git metadata: `d1146f2b4b9be87a362b96edd4796ffc331e635d49000c1b5cba7d72b9845dce`.

The ZIP diagnostic printed an `ERROR` line but exited zero even when it found the seeded defect. The hidden verifier checked the member-type condition; its deliberately incomplete ZIPs do not prove that a complete portable package is accepted. The public suite supplied broader regression coverage after the model run. During the task, OFF ran the full release suite, encountered a sandbox denial on a local Git clone, then searched for a test name. ON repeated the diagnostic instead. OFF used eight shell commands versus four ON; that trajectory difference is a major confound for the token and time difference. Neither valid run reopened an adapter artifact, so recovery from missing receipt details remains untested.

## Invalid exploratory ZIP attempts

Two earlier attempts used the same code fixture but had harness errors and are excluded from the table and aggregate JSON. The first working copies lacked `.git`, so the public release-tool tests could not build an archive; ON also produced a wrong diagnostic string and failed the hidden check. The second attempt added Git history, but stored raw traces inside the working copies. The ON trace contained an absolute adapter path, causing the repository's own validation to fail after the model had finished. Both failures were caused or confounded by the harness. The final ZIP pair kept Git metadata but put traces and adapter artifacts outside both working copies; its seeded baseline failed the intended public test before model runs, and both completed repairs passed hidden and public checks.

Cached input totaled 194,560 OFF and 146,944 ON; uncached input was 27,744 OFF and 20,488 ON. Output tokens were 1,275 OFF and 1,374 ON. The short task's receipt was larger than the direct result and increased both provider tokens and time. The noisy task moved in the opposite direction. Tool counts and trajectories differed, so smaller first results alone do not establish a causal token reduction. Provider cache reuse, prompt differences, and one run per fixture prevent a cost or general quality claim. Keep the adapter opt-in pending repeated balanced pairs with preflight checks of the experimental fixtures.
