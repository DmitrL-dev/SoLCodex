# Byte-savings methodology

SoL Codex locally counts the serialized bytes of eligible tool observations before and after they are replaced with bounded receipts. These counters describe model-visible tool-result bytes saved for packing events only.

They are not token counts, API costs, quota, time, or cache measurements, or evidence that task quality is unchanged.

## Snapshot

The aggregate-only snapshot in [`2026-09-22-packing-report.json`](measurements/2026-09-22-packing-report.json) was generated from 25 local state files. No observation text, session identifier, transcript, command, local artifact path, credential, or user path is included.

| Group | Packed observations | Source bytes | Receipt bytes | Saved bytes | Saved | Receipt |
|---|---:|---:|---:|---:|---:|---:|
| Total | 355 | 8,158,596 | 735,540 | 7,423,056 | 90.98% | 9.02% |
| `gpt-6-astra` | 7 | 47,428 | 9,065 | 38,363 | 80.89% | 19.11% |
| `gpt-5.6-sol` | 1 | 40,112 | 1,986 | 38,126 | 95.05% | 4.95% |
| Other attributed models | 9 | 237,450 | 26,955 | 210,495 | 88.65% | 11.35% |
| Legacy unattributed | 338 | 7,833,606 | 697,534 | 7,136,072 | 91.10% | 8.90% |

`other_models` contains attributed model slugs other than `gpt-6-astra` and `gpt-5.6-sol`. In this snapshot it consists of nine `chatgpt-web/extra-high` packing events. `legacy_unattributed` contains events recorded before per-model accounting was added.

## Formulas

For each group:

```text
saved_bytes = source_bytes - receipt_bytes
saved_percent = saved_bytes / source_bytes × 100
receipt_percent = receipt_bytes / source_bytes × 100
```

All displayed percentages are rounded to two decimal places. The JSON keeps integer byte counts as the reproducible source values.

## Generate your own report

Find the resolved `PLUGIN_DATA` and `PLUGIN_ROOT` paths in `/hooks`. Run the installed hook with those exact paths:

```bash
PLUGIN_DATA=/confirmed/plugin/data/path \
  python3 /confirmed/plugin/root/scripts/sol_hook.py --report
```

Marketplace names and cachebuster versions affect concrete paths; do not infer them when reading or deleting data.

The report has three scopes:

- `totals`: all packing events found in valid local state files;
- `by_model`: events recorded with an exact model slug;
- `unattributed`: the difference between totals and attributed model counters.

## Deterministic mechanism benchmark

The repository includes a local benchmark that exercises both thresholds, exact artifact hashing, credential redaction, plain-string unknown status, the structured-response status guard, and the net-savings guard:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/benchmark_receipts.py
```

It uses fixed synthetic output in a temporary private data directory. Its output is suitable for regression testing the packing mechanism. It is not an end-to-end agent benchmark and does not establish token, cost, latency, quota, or task-quality improvements.

## Population and interpretation limits

The snapshot includes only observations that crossed the active byte threshold and produced a smaller receipt under the hook version that recorded them. It predates plain-string packing; it is not a forecast for the current release. Small outputs, unknown-status `pytest` and `unittest` results, and outputs rejected by the net-savings guard are absent from these counters. Current reports may also include other packed plain-string observations with unknown status.

The source and receipt sizes are serialized UTF-8 byte lengths measured by the hook. A tokenizer may map the same text to a different ratio, providers may cache or bill content differently, and Codex may apply additional context processing outside the plugin.

The model split is observational. Different models were used on different work, with different output distributions and sample sizes. The percentages must not be interpreted as a causal comparison between models.

The results from NVIDIA's SoL-Pi paper use another harness, mechanisms, workloads, and accounting methodology. They do not apply to SoL Codex.

## Future A/B measurement

A [five-pair local pilot](measurements/2026-09-23-ab-threshold.md) tested a 4,096-byte override on two synthetic Python tasks. Both arms passed every verifier, and ON used fewer provider-reported tokens overall. The new 6,144-byte default was not itself measured end to end. The pilot is too small and narrow to support a general savings claim.

A credible end-to-end comparison should be designed before runs begin:

1. Freeze a task set, repository revisions, toolchain, model, reasoning effort, and time limits.
2. Randomize or concurrently schedule control runs with SoL Codex disabled and treatment runs with it enabled.
3. Start every run from clean state and prevent observations or solutions from crossing arms.
4. Record provider token categories and cost separately from plugin byte counters.
5. Score completion and task quality with executable verifiers that are hidden from the agent where appropriate.
6. Predeclare capability floors, exclusion rules, stopping rules, and the primary efficiency metric.
7. Report paired results, failures, confidence intervals, and all deviations from the protocol.

Until that experiment exists, the public claim is intentionally narrow: SoL Codex reduced the serialized model-visible byte size of the 355 packing events in this local snapshot by 7,423,056 bytes, or 90.98%.
