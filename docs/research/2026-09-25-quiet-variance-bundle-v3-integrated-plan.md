# Pinned-CLI integration control (v3)

This prospective, no-model control tests a boundary needed for the planned 16-run SoL Codex comparison: a completed but incorrect repair must remain a measured quality failure, while an uncertain cleanup must stop measurement. The controller runs the pinned Codex CLI and Code Mode host with synthetic local provider responses. It does not authorize model calls.

The [exact expectations](data/2026-09-25-quiet-variance-bundle-v3-integrated-expectations.json), [controller](../measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-integrated-probe.py), [frozen gate](../measurements/fixtures/2026-09-25-quiet-variance-bundle-v3-integrated-gate.py), and [public reducer](../../scripts/reduce_quiet_variance_bundle_v3_integrated.py) are committed before the scored run. Both cases use the same frozen packaging/quiet baseline and the same intentionally wrong source patch. The CLI first executes the assigned failing diagnostic, then applies the patch, then completes. Three synthetic responses carry 390 input tokens (195 cached) and 90 output tokens in total.

| Case | Expected checkpoint and evaluator | Expected measurement decision |
| --- | --- | --- |
| `wrong_complete` | Captured wrong tree; external evaluator rejects it (6/14 behavioral cases pass) | `technical_ok=true`, `admissible=true`; keep the failed repair and its tokens in the comparison |
| `wrong_cleanup_uncertain` | Real supervisor first verifies cleanup and the wrong tree; a wrapper then changes the receipt to unverified before checkpoint; no snapshot or evaluator report | `technical_ok=false`, `admissible=false`; stop subsequent launches |

The wrapper tests the response to cleanup uncertainty. It does not simulate a surviving process or prove that the supervisor detects every escape. The scored gate verifies both SQLite attempt ledgers, actual and returned cleanup receipts, the CLI trace, the candidate tree, and the report's link to the run's evidence. It publishes raw traces, redacted ledger projections, and aggregate outcomes. Full private artifacts and evaluator reports are not independently replayable from this repository.

The control deadline is 90 seconds per case; the planned model comparison allows 600 seconds and at most 32 requests per run. A pass qualifies only this named integration behavior. It does not establish a SoL ON/OFF effect, authentic billing, model repair quality, or general token savings. The candidate remains unauthorized until all six qualification slots, including an independent review, are complete.

The gate also pins the host Python base tree, executable, and origins of its loaded standard-library modules before and after each case. Those private runtime bytes cannot be verified from the public repository. The evaluator reports `external_runtime_frozen=false`; its separately pinned evaluator environment does not imply a fully frozen operating-system toolchain.
