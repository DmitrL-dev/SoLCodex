# Quiet pytest diagnostic: exposed development preflight (2026-09-25)

This no-model preflight compares pytest's own `-q --tb=short` output with `-v` on two previously exposed repair fixtures. It asks whether the shorter result retains the same collected tests, outcomes, and visible failing symptom before any agent-level cost experiment. It does **not** measure SoL Codex, model tokens, billed cost, repair quality, or a task-level saving.

| Source with visible regression | Verbose bytes | Quiet bytes | Collected tests | Failures | Same outcomes |
| --- | ---: | ---: | ---: | ---: | --- |
| packaging #928 parent | 32,577 | 1,303 | 291 | 1 | yes |
| packaging historical fix | 30,321 | 575 | 295 | 0 | yes |
| Click #2447 parent | 121,785 | 2,225 | 1,306 | 1 | yes |
| Click historical fix | 120,765 | 1,730 | 1,308 | 0 | yes |

The parent failures remain visible: `InvalidLicenseExpression` for packaging and `assert None is RuntimeError` for Click. The [fixed-field aggregate](data/2026-09-25-quiet-diagnostic-preflight.json) records output hashes, byte counts, and JUnit inventory checks. The [reducer](../../scripts/reduce_quiet_diagnostic_preflight.py) rejects a changed case result or missing diagnostic; its [focused tests](../../scripts/test_reduce_quiet_diagnostic_preflight.py) exercise those controls. The hashes bind local raw reports, which are not published because they contain local absolute paths. They are not expected to match a replay on another machine.

For Click, the JUnit `skipped: 23` field includes one expected failure: pytest records `tests/test_chain.py::test_group_chaining` as a `<skipped type="pytest.xfail">` element. Terminal output reports the same cases as 22 skipped and 1 xfailed.

The sources were checked out at packaging parent `3f83dea9b60e660e464535a1019d2de62723884f`, fix `a1f705642e50b79da6be83fb0f6149daa32fc7cc`, Click parent `16fe802a3f96c4c8fa3cd382f1a7577fda0c5321`, and fix `36deba8a95a2585de1a2aa4475b7f054f52830ac`. The published [packaging](../../experiments/packaging_928/visible_regression.patch) and [Click](../../experiments/click_2447/visible_regression.patch) regression patches were applied to each corresponding parent and fix checkout. The local run used Python 3.12, offline-installed pytest 8.4.2, iniconfig 2.1.0, pluggy 1.6.0, and Pygments 2.19.2, with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `PYTHONDONTWRITEBYTECODE=1`.

Run the commands **sequentially**, once per checkout, with its `src` directory and the four pytest dependencies on `PYTHONPATH`. For packaging, the target is `tests/test_metadata.py`; for Click, it is `tests/`. The common flags are `-p no:cacheprovider -o addopts= --color=no --junitxml=REPORT.xml`. Use `python -m pytest -v` for one run and `python -m pytest -q --tb=short` for the other, redirect each combined output to the matching `.out` file, and invoke `python -m scripts.reduce_quiet_diagnostic_preflight REPORT_DIRECTORY`. The filenames expected by the reducer are `parent`, `gold`, `click-parent`, and `click-gold`, each suffixed by `-verbose` or `-quiet` and `.xml` or `.out`.

An initial **parallel** full-Click attempt produced 12 and 13 failures, mostly in pager tests, with different failing parameterizations. A sequential retry on the same source produced the single expected regression failure in both formats; only those sequential results enter the aggregate. This observation makes shared-workspace parallel execution unsuitable for the next pilot. Shorter printed output can also change agent behavior, induce re-runs, or shift provider cache usage; none of those effects is measured here.

A second [reverse-order replay](data/2026-09-25-quiet-diagnostic-reverse-replay.json) copied each source into a separate workspace with its own `HOME`, `TMPDIR`, and XDG cache directory. It ran Click gold quiet→verbose, Click parent quiet→verbose, packaging gold quiet→verbose, and packaging parent quiet→verbose, with no concurrent suites. All eight runs matched the first replay's corresponding JUnit inventory and outcome vector. Output sizes differed by a few bytes because report paths changed. This is a finite stability check, not proof that future pager tests cannot be flaky.
