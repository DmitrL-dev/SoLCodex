# Synthetic transport → checkpoint → evaluator integration

**Result: all four frozen no-model development cases matched their expected outcomes.** The [plan](../research/2026-09-25-integrated-checkpoint-plan.md), [exact expectations](../research/data/2026-09-25-integrated-checkpoint-expectations.json), [controller snapshot](fixtures/2026-09-25-integrated-checkpoint-probe.py), and [reducer](../../scripts/reduce_integrated_checkpoint_probe.py) were pushed in commit `1975c12` before the run. The controller checked 22 actual source hashes, the published controller copy, both runtime digests, baseline and candidate tree hashes before running any case. The [observations](data/2026-09-25-integrated-checkpoint-result.json) record the actual hashes, runtime, four ordered cases, private artifact hashes, and the frozen acceptance decision.

| Controlled case | External evaluator | Provider usage | Technical gate |
| --- | --- | --- | --- |
| Gold, complete usage | 14/14, pass | Input 320 (190 cached), output 80 | Pass |
| Wrong, complete usage | 6/14, fail; 13 upstream failures | Input 320 (190 cached), output 80 | Technical path complete; repair rejected |
| Gold, first usage missing | 14/14, pass | Whole-run usage `null`; second attempt subtotal 200 input (150 cached), 50 output | Fail |
| Gold, injected cleanup uncertainty | Evaluator called 0 times; quality unknown | Input 320 (190 cached), output 80 | Checkpoint blocked; fail |

The controlled child executed the assigned baseline diagnostic and observed exit `1` with the expected failing test and `290` passes before modifying source. It ran under the pilot's sandbox and real process supervisor. Each case sent two requests through the Broker/HostBridge; the first controlled-client delivery failed on disconnect, its upstream completion followed runner exit, and the second completed normally. Both ledgers agreed on two distinct attempt IDs. The real supervisor verified cleanup and measured the pinned gold or wrong tree. For the cleanup-negative case, uncertainty was injected **after** that measurement; the blocked checkpoint did not masquerade as evidence that source was unchanged. The evaluator wrapper counted actual calls as `1, 1, 1, 0` and delegated each permitted call to the real evaluator. Both transport services closed in every case.

Reproduce the public decision with:

```sh
python3 scripts/reduce_integrated_checkpoint_probe.py
```

It returns `synthetic_integration_pass=true`, `full_measurement_path_qualified=false`, and `plugin_savings_established=false`. The provider, usage values and code-mode trace were synthetic; the actual Codex CLI and billing system were not used. The controller, full private reports, candidate snapshots, and sandbox execution cannot be independently replayed from this repository. These four cases establish only the named integration behaviors. They do not estimate SoL Codex token savings, establish model repair quality, qualify authentic CLI telemetry, or meet the independent held-out A/B gate.
