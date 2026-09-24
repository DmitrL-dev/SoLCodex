# Frozen synthetic checkpoint integration: development plan

**Status: prospective four-case development probe; no model call authorized.** The [expectation matrix](data/2026-09-25-integrated-checkpoint-expectations.json) fixes case order, exact outcomes, candidate trees, source and runtime hashes before execution. An Astra reviewer inspected the draft controller and expectations and identified provenance, evaluator-call, and blocked-checkpoint evidence gaps; those checks were added before this freeze. The reviewer saw the implementation, so this is not a blinded independent validation.

The controlled child runs under the pilot's actual macOS sandbox and process supervisor. It executes the assigned failing packaging diagnostic on the frozen parent, makes two requests through the actual loopback Broker/HostBridge and attempt ledgers, then writes a pinned gold or wrong source file. The first client disconnects before its upstream completion; the second completes normally. The runner's JSON trace is **synthetic**, and the provider usage is supplied by a stub. Host checkpoint and the real external evaluator run after the child exits. Source and runtime pins are checked from the actual files before the first case; every result is reduced against this matrix.

| Case | Evaluator | Provider accounting | Required gate |
| --- | --- | --- | --- |
| Gold, complete usage | 14/14, pass | Both attempts counted once | Technical pass |
| Wrong, complete usage | 6/14, fail; 13 upstream failures | Both attempts counted once | Repair rejected, technical path complete |
| Gold, missing first usage | 14/14, pass | Whole-run usage null | Technical failure |
| Gold, injected cleanup uncertainty | Evaluator called zero times | Both attempts still reconciled | Checkpoint blocked, quality unknown |

For complete accounting, input is `320` including `190` cached, output is `80`, and input plus output is `400`. With missing first usage, the observed subtotal is input `200` including `150` cached plus output `50`, while complete-run usage stays null. The cleanup case injects uncertainty **after** the actual supervisor has verified cleanup and measured the candidate tree; it tests the fail-closed gate rather than simulating an uncontrolled surviving process.

A pass would establish only integration of these synthetic cases. It would not prove authentic Codex CLI event handling, real provider usage or billing, model repair quality, a SoL Codex ON/OFF effect, general token savings, or hostile-code isolation. The private runner, source snapshots and evaluator reports limit external replay; the public reducer can recompute the decision from published observations.
