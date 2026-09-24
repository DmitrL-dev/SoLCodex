# Late missing-usage control v2: prospective comparator correction

**Status: revised gate and unchanged behavioral expectations prepared before a fresh scored run.** The [v1 result](../measurements/2026-09-25-real-cli-late-missing-usage-development.md) remains a failed control. Its gate compared in-memory tuples for `trace_event_shapes` with JSON lists; the saved values matched, but the frozen decision was false.

The [v2 expectation matrix](data/2026-09-25-real-cli-late-missing-usage-v2-expectations.json) preserves the same 29 expected public observations and the same [synthetic controller](../measurements/fixtures/2026-09-25-real-cli-late-missing-usage-probe.py). The [v2 gate](../measurements/fixtures/2026-09-25-real-cli-late-missing-usage-v2-gate.py) converts observations to canonical JSON before comparison. It still requires the pushed plan, source/CLI/runtime/quality/ledger pins, published controller and [v2 reducer](../../scripts/reduce_real_cli_late_missing_usage_v2_probe.py). It runs on the same exposed packaging #928 task with a synthetic provider and no model request.

The third upstream response must arrive after CLI cancellation, show HTTP 200 and received bytes, and fail usage parsing with `invalid_sse`. The independently evaluated gold checkpoint must pass while full-run usage remains `null`, reconciliation stays incomplete, and technical admission stays false. All 29 observations must match prospectively. A fresh run is required; the saved v1 artifact is not rescored as a v2 pass.

This is a no-model accounting control. It cannot qualify the entire A/B measurement path, prove provider billing, or support a SoL Codex savings claim.
