# Exposed evaluator calibration: first frozen no-model run

**Result: failed, 3/7 controls matched the predeclared expectations. No model run is authorized.** The [expectations](../research/data/2026-09-25-evaluator-calibration-expectations.json) were pushed in commit `a7641d0` before the evaluator ran. They pinned source hashes, candidate tree hashes, exact case vectors, canaries and expected statuses. The [sanitized observations](data/2026-09-25-evaluator-calibration-first-result.json) retain all seven controls and the original `frozen_acceptance_pass=false`. The runner and complete evaluator reports remain private; published hashes anchor those records but do not permit external replay.

The parent checkpoints produced the expected behavior scores: Click 3/9 and packaging 8/14. Both gold checkpoints passed all behavior cases (9/9 and 14/14), their frozen upstream inventories, and the isolation canaries. The wrong Click fix failed the expected six exception cases and passed three lifecycle cases. The import-time `PASS` spoof produced no valid case frames and remained `unknown` rather than an accepted repair.

The frozen expectation incorrectly required upstream exit `0` and `upstream_passed=true` for every ordinary control. Both parents and the wrong Click fix instead produced valid, complete upstream reports with one failed test and exit `1`; those are legitimate behavioral failures, not a malformed evaluator report. The wrong packaging fix produced its expected 6/14 case vector, but the evaluator rejected the run when a process held the candidate source directory open during the upstream cleanup check. The sampled handle belonged to `cmdworker_shared` and was gone at the next inspection; its origin is unestablished. The fail-closed rejection means this negative control cannot be counted as a qualified behavioral kill in this run.

Recompute the decision from the frozen specification and published observations:

```sh
python3 scripts/reduce_evaluator_calibration.py
```

The command intentionally exits `1` and reports the four mismatching controls. A revised expectation must be published before a new run; the first failure remains a separate result. This calibration covers two exposed source tasks and one wrong fix per task. It does not qualify the full parser/transport/checkpoint path, six independent wrong fixes per held-out task, hostile candidate isolation, provider billing, or SoL Codex savings. The runner's `model_calls=0` and `auth_reads=0` fields are constants, not independently measured counters; no model was invoked by the inspected calibration code.
