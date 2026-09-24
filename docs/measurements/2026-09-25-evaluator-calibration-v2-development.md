# Evaluator calibration v2: adaptive no-model development result

**The revised calibration passed 7/7 frozen controls. This does not authorize a model run or establish SoL Codex savings.** The [v2 expectations](../research/data/2026-09-25-evaluator-calibration-expectations-v2.json) were pushed in commit `177bec6` before this run. They pinned the runner, evaluator dependencies, candidate trees, exact behavior vectors, upstream inventory hashes, and hashes of the expected failing-test sets. The [first frozen run](2026-09-25-evaluator-calibration-first-development.md) remains failed at 3/7; its results were used to revise the upstream expectations and diagnose cleanup, so v2 is adaptive development evidence rather than independent blind validation.

Both gold checkpoints passed all external behavior cases and their frozen upstream suites: Click 9/9 and packaging 14/14. Parent and wrong checkpoints produced the predeclared failing behavior vectors and complete upstream reports with expected test-failure sets. The wrong packaging fix scored 6/14 behavior cases and had 13 upstream call failures. The import-time fake `PASS` emitted no valid case frames and stayed `unknown`; it was not accepted as a repair. All isolation canaries and source-integrity checks passed in the seven controls.

The existing fail-closed cleanup policy was retained. A local no-model unit test confirmed that the evaluator accepts a temporary external open handle only after it closes and rejects a persistent handle. The full v2 run completed without a cleanup rejection. Eleven evaluator unit tests passed under Python 3.12.14. These unit results and the private runner are not independently replayable from this repository.

The [sanitized observations](data/2026-09-25-evaluator-calibration-v2-result.json) include every control, exact case vectors, upstream counts and hashes, source hashes, and hashes of private full reports. Recompute the aggregate decision with:

```sh
python3 scripts/reduce_evaluator_calibration.py \
  --expected docs/research/data/2026-09-25-evaluator-calibration-expectations-v2.json \
  --result docs/measurements/data/2026-09-25-evaluator-calibration-v2-result.json
```

The reducer returns `development_calibration_pass=true`, `full_measurement_path_qualified=false`, and `model_run_authorized=false`. It checks the published observations against the frozen specification; private candidate snapshots, complete evaluator reports, and the host execution cannot be replayed externally. One wrong fix per exposed task does not satisfy the six independently reviewed plausible wrong fixes required for future held-out tasks. Candidate code and verifier code still share an interpreter, so this is not a hostile-code isolation proof. Parser, transport, retry accounting, source checkpoints, complete provider usage and billing, and a plugin ON/OFF A/B remain separate gates. The runner's zero model-call and authentication-read fields are constants, not independently instrumented counters; inspection of the calibration path found no model invocation.

A separate [publication audit](../../scripts/audit_evaluator_calibration_publication.py), written **after** the run, checks the frozen reducer's actual file hash, the published host Python and evaluator pins, the reported cleanup unit-check outcomes, strict behavior-vector types, and score arithmetic. Run `python3 scripts/audit_evaluator_calibration_publication.py`; it reports `publication_audit_pass=true`. These checks validate the public record, not the private execution. The evaluator checked its copied venv against the pinned runtime digest internally, but that digest has no independently observed public runtime receipt.
