# Pinned-CLI integration control result (v3)

Both prospectively frozen no-model cases matched the [plan](../research/2026-09-25-quiet-variance-bundle-v3-integrated-plan.md). The plan and exact source pins were pushed in commit `851b0f2` before execution. The [result](data/2026-09-25-quiet-variance-bundle-v3-integrated-result.json) records the frozen commit, private artifact fingerprints, copied CLI hashes, complete synthetic usage, and case outcomes. Two raw CLI traces and two redacted ledger projections are published alongside it. Recompute the public decision with `python3 scripts/reduce_quiet_variance_bundle_v3_integrated.py` from the repository root; it returns `control_pass=true`.

| Case | External behavior | Accounting | Gate |
| --- | --- | --- | --- |
| `wrong_complete` | Wrong tree captured; 6/14 behavior cases pass; upstream tests fail | Three completed attempts, 390 input tokens (195 cached), 90 output tokens | `technical_ok=true`, `admissible=true`, `quality=false` |
| `wrong_cleanup_uncertain` | Actual supervisor verified cleanup, then an injected uncertain receipt blocked checkpoint; evaluator called zero times | Same complete synthetic usage | `technical_ok=false`, `admissible=false`, quality unknown |

The failed repair remains in the denominator of any later repair-success rate, and its tokens remain in the spend totals. The cleanup-negative case demonstrates fail-closed behavior after an injected receipt change; it does not establish detection of every escaped process. The provider and token counts are synthetic, and the model was not called. Full evaluator reports, original SQLite files, and host Python bytes remain private. The public reducer verifies the published traces and ledger projections but cannot independently verify those private bytes or authentic provider billing. The evaluator reports `external_runtime_frozen=false`.

The candidate now has five of six qualification evidence slots filled. Independent review remains pending, `model_run_authorized=false`, and no SoL savings claim is established.
