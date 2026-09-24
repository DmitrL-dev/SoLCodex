# Click #2447 diagnostic-selector repair pair: exposed development result (2026-09-24)

This pair followed the [post-outcome diagnostic-selector change](receipt-adapter.md) with a full repair on a second, previously exposed repository. It compared the prior receipt adapter at `5079223` with the revised selector at `254ac77`; both were delivered by an exact-command temporary `PreToolUse` rewrite. It does **not** compare the installed plugin with ordinary Codex work or provide an independent estimate of savings.

The parent and historical fix for [Click #2447](https://github.com/pallets/click/issues/2447) were `16fe802a3f96c4c8fa3cd382f1a7577fda0c5321` and `36deba8a95a2585de1a2aa4475b7f054f52830ac`. The [visible regression patch](../../experiments/click_2447/visible_regression.patch) adds an exception-information test to the parent fixture. The exact [nine-case external verifier](../../experiments/click_2447/verify_click_frozen.py), SHA-256 `12e844a5a801e6d1cc233c893472b5ac1a24d59beb1d313be2e91bc132b1b111`, scored the parent 3/9 and historical fix 9/9 before the runs. The parent verbose suite had one failed test; the historical fix passed 1,285 tests with 22 skips and one expected failure. The fixture and verifier were fixed before either repair arm.

The old arm ran first, then the new arm. Each fresh `gpt-6-luna` low-effort session received the same root-normalized prompt and ran the same failing verbose pytest command first. Both used the same fixture, Python environment, search helper, temporary hook, host-only credential broker, and macOS Seatbelt profile. The [pre-repair protocol](../../experiments/click_2447/protocol.json) hash is `2d169ef2d4bb923e74d404881756bf85b2381305f1df7b2ebbd13878a5e0e576`. The reducer pins this exact hash; the protocol was privately frozen before both arms but was not independently timestamped or registered. Both no-edit live smoke runs and both repair runs passed the prospective audit for exact first command, hook match, complete hashed receipt, canaries, bounded process cleanup, allowed source-only diff, completed requests, and observed usage. A clean-copy, read-only, network-denied evaluator checked each saved patch. These are development controls, not a confirmatory isolation attestation.

| Observation | Old selector | Revised selector |
| --- | ---: | ---: |
| First model-visible receipt | 2,201 bytes | 1,789 bytes |
| First complete saved output | 121,684 bytes | 121,684 bytes |
| First selected diagnostic lines | 64–69 | 1337–1340, 1344, 64 |
| Command executions; exact diagnostic executions | 17; 2 | 16; 2 |
| Completed model requests, all with observed usage | 15 | 17 |
| Proxy-observed input + output tokens | 224,879 | 250,614 |
| Cached / uncached input tokens | 98,048 / 123,724 | 140,032 / 105,874 |
| Output tokens | 3,107 | 4,708 |
| Agent wall time | 113.191 s | 168.660 s |
| Full upstream suite | 1,283 passed, 22 skipped, 1 xfailed | 1,283 passed, 22 skipped, 1 xfailed |
| Frozen external behavior checks | **9/9, accepted** | **7/9, rejected** |

The revised selector included the decisive pytest failure in the first receipt and made that receipt 412 bytes smaller (18.7%). The complete revised run used **25,735 more observed input-plus-output tokens (+11.4%)** and **55.469 more seconds (+49.0%)**. Its patch failed `nested_inner_suppresses` and `nested_outer_suppresses`: it supplied the active exception to resource cleanup but did not propagate suppression through the Click context exit. The old patch passed all nine external checks. Both patches modified only `src/click/core.py`, passed `git diff --check`, and passed the full upstream suite. Thus upstream success alone would have hidden the regression. The revised arm has no accepted repair, so this pair supplies **no quality-adjusted cost saving**.

The [path-free aggregate](data/2026-09-24-click-selector-repair-dev.json) records per-case outcomes, usage categories, design and evidence hashes, first receipt hashes, and run order. Run the [fixed-field reducer](../../experiments/click_2447/reduce_selector_dev.py) from the repository root as `python3 -m experiments.click_2447.reduce_selector_dev OLD_HOST_ARTIFACTS NEW_HOST_ARTIFACTS experiments/click_2447/protocol.json` when the private run directories are available. It checks the pinned protocol and arm hashes, prospective audits, response IDs and usage against the request ledger, accepted-connection closure, source overlays, and external case reports. Its [report tests](../../experiments/click_2447/test_reduce_selector_dev.py) reject a missing case or a total inconsistent with case outcomes. Raw model traces, credentials, request bodies, workspaces, and patches remain private; source and verifier fixtures needed to replay the acceptance check are public.

One exposed issue family, fixed order, different agent trajectories, possible provider-cache effects, a temporary hook, and no provider billing reconciliation preclude a causal or general efficiency claim. The local process monitor cannot prove the absence of a detached descendant that escaped observation. The selector was designed after the packaging pair and tested here once; a shorter, more relevant first preview did not guarantee a better repair. This negative quality result belongs in development only and must not enter the held-out confirmation estimate.
