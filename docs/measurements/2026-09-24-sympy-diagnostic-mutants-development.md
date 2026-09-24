# SymPy diagnostic mutation pilot (exposed development task, 2026-09-24)

This follows the [known-miss reproduction](2026-09-24-sympy-verifier-known-miss-development.md) on the same exposed SymPy #26807 parent. Five synthetic source edits were constructed **after** the post-hoc v2 verifier was known and added to the observed agent-like superclass mistake M1. Every edit was materialized from the pinned parent; all changed only `sympy/tensor/array/expressions/array_expressions.py`. A fixed-field [aggregate](data/2026-09-24-sympy-diagnostic-mutants-dev.json) binds the source export, verifier, private behavior reports, upstream selection, JUnit files, and runner manifests by SHA-256. The [reducer and materializer](../../experiments/sympy_26807/README.md) permit a fresh run. Raw reports with local paths and exception detail remain private.

| Variant | Source-level mistake | v2 | Failed behavioral checks | Selected upstream |
| --- | --- | ---: | --- | ---: |
| M1 | `_iterable = False` on shared `_ArrayExpr` | 35/37 | C19, C20 | 48/48 |
| M2 | `ArraySymbol` treated as atomic only at rank one | 32/37 | R03, R07, R15, R16, R17 | 48/48 |
| M3 | `ArraySymbol` treated as atomic only for concrete extents | 35/37 | R04, R16 | 48/48 |
| M4 | `ArraySymbol` treated as atomic only for identifier-like names | 36/37 | R13 | 48/48 |
| M5 | Disable iteration on `ArraySymbol` and `ZeroArray` | 36/37 | C19 | 48/48 |
| M6 | Disable iteration on `ArraySymbol` and `OneArray` | 36/37 | C20 | 48/48 |

All six pass R01, the issue's bare-argument smoke case. Their complete failure vectors differ; the reducer requires the expected behavioral exception and message for every failed check and rejects infrastructure failures. The 24 pinned upstream node specifications again resolve to the same 48 passing test cases. The parent, historical fix, and separate `ArraySymbol` property alternative retain the scores in the earlier report: 20/37, 37/37, and 37/37 under v2. This pilot reused and reverified those private baseline runs, rather than silently treating the new five variants as independent tasks.

An internal methodological review found M1 highly plausible because both historical agents made that superclass edit. M2 and M3 illustrate defensible but synthetic incomplete fixes. M4 was likely tailored to a known verifier case. M5 and M6 are diagnostic ablations of the M1 class-scope mistake, not independent repair mechanisms. All five new edits were designed with v2 visible; selecting only killed variants would bias a mutation score. **The proposed gate of six plausible, non-equivalent wrong fixes has not been met.** These observations establish only that this exact v2 verifier rejects these six selected variants in the recorded macOS arm64 / Python 3.12.13 environment. They do not estimate detection probability for future wrong fixes, acceptance of an agent repair, quality preservation, token savings, or Linux-worker reliability.

The `ZeroArray` and `OneArray` controls preserve observed parent and historical-fix behavior, including `flatten([array]) == [2, 3]` for shape `(2, 3)`. That behavior is unusual; whether changing it is acceptable needs independent API-contract review. The upstream selection does not settle the question. A stronger qualification pilot needs wrong fixes generated without seeing v2, preserved rejected candidates, and reviewers who judge behavior and plausibility without a mutation-kill result. No held-out task is opened by this study.
