# Quiet diagnostic measurement path: no-model unit probe

The [expected matrix](../research/data/2026-09-25-quiet-measurement-qualification-expectations.json) was pushed in commit `cc1edfc` before the final run. The [raw observations](data/2026-09-25-quiet-measurement-unit-probe-result.json) match **14/14** trace classes and **5/5** ledger classes; fake `PASS` text was rejected. This is a direct, synthetic no-model check. **It does not authorize held-out model runs or establish token savings.**

The exploratory probe exposed two defects in the future measurement code: an `error` item was ignored by the first-command auditor, and a usage object missing output/cached counts was accepted as complete reconciliation. Both now classify as unknown/incomplete. A byte-identical duplicate CLI completion is unknown; a known `-9` signal exit violates the expected baseline exit. The previously frozen exposed four-run result remains failed and has not been relabeled.

The [published probe](../../scripts/qualification_probe.py) has the same SHA-256 as the probe recorded in the result. It calls private parser, reconciliation and verifier-wire functions directly and records their source hashes. Recompute the public decision with:

```sh
python3 scripts/reduce_quiet_measurement_qualification.py
```

The reducer reports `direct_probe_pass=true`, `model_run_authorized=false`, and the exact expectation/result hashes. A second run of the published probe against the same local private harness produced byte-identical JSON. The private runner, transport and evaluator assets are not public; external readers can reproduce the decision from the published records, but cannot independently replay the private implementations from this repository alone. Provider billing, end-to-end disconnect/retry handling, process cleanup, host isolation and independent behavioral repair acceptance remain unqualified for the proposed held-out screen.
