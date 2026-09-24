# Quiet diagnostic model screen: exposed development result

The [revision 2 protocol](../research/2026-09-25-quiet-diagnostic-model-screen-protocol-v2.md)
was frozen and pushed before these four assigned runs. **The screen failed.**
Both Click repairs missed two independent nested-scope cases, and the frozen
first-command parser marked every run unknown. All four runs completed with
observed upstream token usage. There were no replacement runs.

| Run | Task | Diagnostic | Independent cases | Input | Output | Cached input | Total tokens | Wall seconds |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 01 | Click | verbose | 7/9 | 211,359 | 1,220 | 87,296 | 212,579 | 66.131 |
| 02 | Click | quiet | 7/9 | 118,039 | 1,075 | 34,560 | 119,114 | 48.588 |
| 03 | packaging | quiet | 14/14 | 93,450 | 1,196 | 50,432 | 94,646 | 42.114 |
| 04 | packaging | verbose | 14/14 | 95,144 | 749 | 41,472 | 95,893 | 31.482 |

Cached input is included within input tokens, not added again. The Click
quiet and verbose runs produced the same source-tree hash; so did the two
packaging runs. Both Click checkpoints passed their frozen upstream tests but
failed `nested_inner_suppresses` and `nested_outer_suppresses` in the
independent evaluator. Packaging passed all 14 external cases and its frozen
upstream tests in both arms. All four quality checks used complete source
checkpoints; the private quality-report hashes and source-tree hashes are in
the [published run rows](data/2026-09-25-quiet-diagnostic-model-screen-v2-result.json).

Verbose runs used 308,472 total observed input-plus-output tokens and 97.613
wall seconds. Quiet runs used 213,760 tokens and 90.702 seconds: ratios 0.693
and 0.929. These descriptive differences do **not** establish qualified
savings because the predeclared quality gate failed. The packaging quiet run
alone took longer than its verbose pair (42.114 versus 31.482 seconds).

The frozen audit required the first diagnostic's CLI item status to be
`completed`. Codex reports a completed command with nonzero exit as
`status=failed`; all four first diagnostics exited 1. Consequently the frozen
audit classified each complete trace as unknown. A retrospective inspection
found the exact assigned command, exit 1, no inspection or edit before its
completion, and a terminal `turn.completed` in all four traces. That
inspection does not change the frozen gate or the failed screen result. A
future protocol must fix the parser before new runs.

The [published JSON](data/2026-09-25-quiet-diagnostic-model-screen-v2-result.json)
contains the numeric run rows, fixed gate results, private receipt hashes and
limitations. Recompute the published aggregate and check it against the
stored result with:

```sh
python3 scripts/reduce_quiet_diagnostic_screen.py \
  --protocol docs/measurements/data/2026-09-25-quiet-diagnostic-model-screen-development-v2.json \
  --result docs/measurements/data/2026-09-25-quiet-diagnostic-model-screen-v2-result.json
```

The private full receipts and evaluator assets limit external reproduction;
the published rows and reducer reproduce the aggregate and decision. The
provider's billing ledger was not independently reconciled. Two earlier
compatibility attempts had unknown usage and are excluded from these four-run
totals; the successful one-request smoke is also excluded. These tasks were
already exposed during development and do not provide held-out evidence or a
general estimate of savings or quality.
