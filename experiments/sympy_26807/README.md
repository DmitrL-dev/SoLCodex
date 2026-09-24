# Exposed SymPy #26807 verifier probe

This directory reproduces the known superclass-regression miss described in the [measurement](../../docs/measurements/2026-09-24-sympy-verifier-known-miss-development.md). It is development material. Do not reuse this issue as a held-out confirmation task.

Fetch source exports for parent [`530149cc7256a98c5963bcccc43cec19a9d04d09`](https://github.com/sympy/sympy/commit/530149cc7256a98c5963bcccc43cec19a9d04d09) and historical fix [`6760acef2209c9538a3f1b3a286b0275e9420b98`](https://github.com/sympy/sympy/commit/6760acef2209c9538a3f1b3a286b0275e9420b98). Their regular-file export digests must be `bd900fce6a20f1a4fa15991ee179d1c7ff362743ac9435eb9c503972f6f71132` and `31b282f6b5c61f241f7126ceadf6d54ca31848d9888aa8978d502bda890c4c36`, using `SHA256(JSON([[relative POSIX path, file SHA256], ...]))`, sorted by path and compact JSON separators. Each export has 2,061 regular files. `materialize_variants.py` rejects a mismatched parent.

The exact v1 verifier hash is `12e01e759417210f3d9168a97e909911e81aaee1eb564c7e8a3c76842b97edd7`; v2 is `7ee8cb3a6b7ddf15257a551ac4f8a8855b96757e4e05c73dce76d1383b0017a2`. V2 only imports the neighboring classes and adds controls C19/C20. `upstream_selection.json` lists 24 node specifications; `upstream_inventory.json` records the 48 resolved tests. Use Python 3.12.13 with NumPy 1.26.4, mpmath 1.3.0, pytest 8.2.2, and Hypothesis 6.108.8 for the recorded environment.

Example commands, with all checkouts and reports **outside** this repository:

```sh
mkdir -p "$REPORTS" "$UPSTREAM_REPORTS"
python experiments/sympy_26807/materialize_variants.py --parent "$PARENT_EXPORT" --output "$M1_EXPORT" --kind superclass_noniterable
python experiments/sympy_26807/materialize_variants.py --parent "$PARENT_EXPORT" --output "$ALTERNATIVE_EXPORT" --kind array_symbol_property
"$PYTHON" -I -B experiments/sympy_26807/verifier_v1.py "$M1_EXPORT" --json "$REPORTS/m1_v1_public.json"
"$PYTHON" -I -B experiments/sympy_26807/verifier_v2.py "$M1_EXPORT" --json "$REPORTS/m1_v2_public.json"
python experiments/sympy_26807/run_upstream.py --source "$M1_EXPORT" --python "$PYTHON" --report-dir "$UPSTREAM_REPORTS" --name m1
```

Repeat both verifiers and `run_upstream.py` for parent, gold, and alternative, using the fixed report names in `reduce_qualification.py`. A failing verifier returns exit 1 but still writes its behavioral JSON; retain that report. Reduce the eight JSON reports and four upstream runs with:

```sh
python experiments/sympy_26807/reduce_qualification.py --parent "$PARENT_EXPORT" --gold "$GOLD_EXPORT" --superclass "$M1_EXPORT" --alternative "$ALTERNATIVE_EXPORT" --reports "$REPORTS" --upstream-reports "$UPSTREAM_REPORTS"
```

The `PARENT_EXPORT`, `GOLD_EXPORT`, `M1_EXPORT`, `ALTERNATIVE_EXPORT`, `PYTHON`, `REPORTS`, and `UPSTREAM_REPORTS` shell variables above are user-provided absolute paths, with the latter two report directories outside this repository. The scripts do not download code or install dependencies. The report reducer emits only fixed-field aggregates; it does not turn this exposed task into independent quality evidence.

## Diagnostic mutation pilot

The [follow-up pilot](../../docs/measurements/2026-09-24-sympy-diagnostic-mutants-development.md) adds five synthetic edits to M1. They were designed with v2 visible and do **not** satisfy a six-plausible-wrong-fix qualification gate. `materialize_variants.py --kind` accepts `rank_one_only`, `concrete_extent_only`, `identifier_only`, `zero_sibling`, and `one_sibling`; give each an output outside this repository. For each source tree, run `verifier_v2.py` with `--json` to save `m2-v2.json` through `m6-v2.json` in a private report directory. The verifier should exit 1 for a behavioral rejection, while still writing its JSON. Run `run_upstream.py --name m2` through `--name m6` with the corresponding source trees and a separate private upstream report directory. The upstream runner should exit 0. The fixed names let the reducer audit the complete assertion and upstream inventories.

Reduce with the four baseline source exports and their prior private reports, plus the five new source exports and reports:

```sh
python experiments/sympy_26807/reduce_mutant_pilot.py \
  --parent "$PARENT_EXPORT" --gold "$GOLD_EXPORT" \
  --superclass "$M1_EXPORT" --alternative "$ALTERNATIVE_EXPORT" \
  --baseline-reports "$BASELINE_REPORTS" --baseline-upstream "$BASELINE_UPSTREAM" \
  --m2 "$M2_EXPORT" --m3 "$M3_EXPORT" --m4 "$M4_EXPORT" \
  --m5 "$M5_EXPORT" --m6 "$M6_EXPORT" \
  --reports "$MUTANT_REPORTS" --upstream-reports "$MUTANT_UPSTREAM"
```

All referenced variables are user-provided absolute paths outside this repository. The reducer first recomputes the earlier published baseline aggregate and refuses a mismatch. It then verifies each exact one-file edit, v2 result vector and failure reason, matched environment, source import, upstream runner manifest, and 48-case JUnit inventory. Its output is a path-free fixed-field aggregate; the stored private reports are unavailable for independent audit.
