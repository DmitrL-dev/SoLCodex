# Real Codex CLI with a synthetic provider: one frozen diagnostic control

**Result: the one frozen no-model control matched all declared observations.** The [plan and exact expectations](../research/2026-09-25-real-cli-synthetic-provider-plan.md) were published in commit `5893014` before the scored rerun. The [aggregate](data/2026-09-25-real-cli-synthetic-provider-result.json) and [reducer](../../scripts/reduce_real_cli_synthetic_provider_probe.py) record a passing control decision with `full_measurement_path_qualified=false` and `plugin_savings_established=false`.

The pinned Codex CLI emitted the diagnostic as its first tool action, with no prior actions or malformed trace lines. The command exited `1` and reported `1 failed, 290 passed`, matching the known packaging regression. The second provider request contained both `custom_tool_call` and `custom_tool_call_output` for `call_synthetic_1`. Two attempts reconciled across the actual Broker/HostBridge ledgers: 320 input tokens, of which 190 were cached, and 80 output tokens. These values were **supplied by the synthetic provider**, not measured by billing. The supervised CLI exited normally; cleanup and service shutdown were verified. The source tree was unchanged, checkpoint captured, and the real external evaluator returned `fail`, as expected for a control with no repair.

The controller still labels its own scope `exploratory_synthetic_provider_real_cli`; that string was frozen with the code after exploratory wire-format work. The scored observation is distinguished by the preflight gate, published expectation hash, and prior commit. One earlier exploratory response used a flat `functions.exec` name and the CLI rejected it as an unsupported custom tool call; the scored fixture uses `namespace="functions"` and `name="exec"`.

Recompute the published decision with:

```sh
python3 scripts/reduce_real_cli_synthetic_provider_probe.py
```

This demonstrates this CLI build's handling of one synthetic Code Mode tool call and the observed path through accounting, checkpoint, and evaluator. It does not validate real provider billing, model repair quality, other tool shapes, an ON/OFF intervention, or token savings. The private harness, full traces, and evaluator artifacts are not independently replayable from the public repository.
