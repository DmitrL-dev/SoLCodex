# Real CLI cancellation after repair: frozen no-model control

**Result: the one frozen control matched all declared observations.** The [plan, parser, fixtures and exact expectations](../research/2026-09-25-real-cli-late-checkpoint-plan.md) were published in commit `e09f982` before the scored run. The [aggregate](data/2026-09-25-real-cli-late-checkpoint-result.json) and [reducer](../../scripts/reduce_real_cli_late_checkpoint_probe.py) report `no_model_late_checkpoint_pass=true`, while `full_measurement_path_qualified=false` and `plugin_savings_established=false`.

The actual pinned Codex CLI executed the assigned packaging diagnostic first. The diagnostic exited `1` with `1 failed, 290 passed`; the CLI then applied the fixed gold patch through Code Mode. The supervisor stopped the CLI with `SIGTERM` while its third provider request was open. After runner completion, the synthetic provider finished that request. All three attempts reconciled exactly once: 390 synthetic input tokens (195 cached) and 90 synthetic output tokens. Cleanup and both service shutdowns were verified. The checkpoint matched the predeclared gold tree, and the real external evaluator passed.

The corrected parser reported `first_command.status="observed"` with `first_command_exact=true`, while the whole `trace_complete=false`. This separates evidence about the finished diagnostic from evidence about the interrupted turn. The aggregate's `technical_ok=true` means the accounting, checkpoint and evaluator path completed; the CLI itself timed out and exited `-15`. A production A/B gate must use both facts. The controller's scope string retains `exploratory_no_model_real_cli_late_checkpoint` from prefreeze development; the scored run is identified by its prior public freeze and matching expectation hash.

Recompute the published decision with:

```sh
python3 scripts/reduce_real_cli_late_checkpoint_probe.py
```

Provider replies and usage values were synthetic. This single controlled case does not qualify live billing, multiple task families, model repair behavior, a SoL Codex ON/OFF intervention, or token savings. The private sandbox execution and evaluator artifacts cannot be independently replayed from the public repository.

A separate **exploratory, unscored** real-CLI probe removed `usage` from the first synthetic provider completion. The bridge recorded one unknown attempt and forwarded no completed response body; the CLI failed before any diagnostic command, with `usage=null`, incomplete reconciliation and `technical_ok=false`. This is evidence of fail-closed behavior for that one malformed response, not a frozen negative-case qualification. Its private controller SHA-256 is `203f6f0101b91225c30dd391160025acb33baf629ca01d542dac96d596014c98`; the CLI trace SHA-256 is `af8567e5e0d17b9ba5082aef56072f3cc277195498b227118bc36079ea1d15fa`.
