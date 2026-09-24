# Explicit receipt package: frozen no-model CLI control

The experimental package and prospective control were committed and pushed as `98bef1d` before the scored run. The frozen gate accepted both arms. The [plan](expectations.json), [controller](fixtures/explicit_package_probe.py), [gate](fixtures/frozen_explicit_package_control.py), and [aggregate result](result.json) are in this directory. This is a synthetic-provider control through the real Codex CLI, not a model repair experiment.

| Observation | OFF: direct child | ON: installed explicit package |
| --- | ---: | ---: |
| Child executions | 1 | 1 |
| Tool output bytes | 8,461 | 907 |
| Full artifact bytes | — | 8,461; hash valid |
| Output-only marker in second provider request | Yes | No |
| Receipt in second provider request | No | Yes |
| Second provider request bytes | 53,905 | 53,583 |
| Both provider requests, total bytes | 104,013 | 104,747 |

The ON second request was 322 bytes smaller, but its first request carried 1,056 more bytes of package context. Across both requests ON was **734 bytes larger**. These are serialized request bytes, not tokens or billable usage. The provider returned fixed synthetic usage, so this run cannot establish token or cost savings.

Both arms completed one tool call with exit code 0, two provider requests, zero provider errors, complete reconciliation, and verified process cleanup. The ON command used the installed copy, which matched the frozen package. Its receipt reported a complete capture and the full local artifact contained the marker with a matching SHA-256. The packaged adapters passed 36 focused tests; manifest and skill validators passed.

The OFF and ON paths differ in package exposure and explicit command wrapping. The result establishes that this path can keep a selected command's full output out of the next provider request while retaining it locally. It does not establish equal repair quality, end-to-end token efficiency, latency, or behavior for the released automatic hook. Further model A/B work needs independent behavioral checks and complete provider-attempt telemetry. No model run was authorized by this control.

## Local installed-copy negative controls

The [negative control plan](negative_expectations.json) and [script](fixtures/installed_package_negative_control.py) were committed before execution. The first attempt stopped on the control script's macOS `/var` versus `/private/var` path comparison. The canonical-path fix and revised plan were committed as `e97ce2a` before the accepted run. No case result was published from the stopped attempt.

The accepted [aggregate](negative_result.json) shows that a child exiting 7 stayed a failed command with one execution and a verified complete artifact; a timed-out child returned 124 with `capture_complete=false`; and a 0755 artifact directory failed before spawning the child. A necessary line omitted from the receipt was found at line 65 by bounded artifact search (279 response bytes), while a wrong SHA-256 was rejected. These are local subprocess controls against a copied installation, not model behavior or a security audit.

## Exposed model repair pair

The [one-pair plan](model_dev_plan.json) and [controller](fixtures/explicit_model_dev.py) were frozen and pushed as `bcd27fc` before the runs. ON ran first, then OFF, on the previously studied packaging #928 parent. Both used the same `pytest -v` child diagnostic, model, effort, runtime, 600-second limit, and external evaluator. The package was exposed only to ON. This is development evidence from one exposed task, with order and model-run variation unresolved.

| Observation | ON: installed explicit package | OFF: direct command |
| --- | ---: | ---: |
| External behavior cases | 14/14 | 14/14 |
| Provider attempts, all accounted | 10 | 8 |
| Input tokens, cached included | 142,262 | 123,427 |
| Cached input tokens | 59,136 | 53,760 |
| Output tokens | 2,393 | 2,089 |
| Total provider tokens | 144,655 | 125,516 |
| First tool output bytes | 2,147 | 32,007 |
| Wall seconds including accounting | 72.142 | 60.867 |

The [published aggregate](model_dev_result.json) records **19,139 more provider tokens for ON** (15.25%) and 11.275 more seconds. Both repairs passed the frozen independent checks and their upstream suite. ON used the adapter once on the required first command; its 32,536-byte diagnostic artifact was complete and the child exit code 1 was preserved. ON made no bounded-search call and later ran a large test command without the adapter. These observations do not show a token benefit from this package on this repair.

The frozen first-action auditor marked both runs `unknown`: the CLI emitted an `item.completed` warning about `code_mode` before `turn.started`, which its strict parser counted as malformed. A post-run inspection found that each first command's started and completed text matched its assigned prompt and exited 1, but this does **not** change the frozen auditor result. A future series must suppress or explicitly model that warning before freezing. Provider attempt usage was complete in both arms; billing was not independently reconciled. No held-out or general saving claim follows from this pair.

Recompute the token arithmetic and accounting checks from the published rows with `python3 experiments/explicit_receipt_profile/reduce_model_dev_result.py`. The private traces and external evaluator assets are retained outside this repository, so this public reducer cannot independently replay the repairs.

### Retrospective attempt breakdown

A separate [exporter](export_model_dev_attempts.py) reconciled each retained private delivery row with its upstream completion, checked the published trace hashes and totals, then emitted [deidentified per-attempt usage](model_dev_attempts.json). The [public reducer](reduce_model_dev_attempts.py) checks its arithmetic. This breakdown was chosen **after** seeing the pair and does not change its frozen outcome.

ON's first provider request used 352 more input tokens. After the assigned diagnostic, ON's second request used 1,811 fewer input tokens than OFF's; through two attempts ON had used 1,302 fewer input-plus-output tokens. The trajectories then diverged: ON finished with 10 attempts versus OFF's 8 and 19,139 more total tokens. The shorter first tool result therefore produced a local context saving, but it did not lower full-repair usage in this pair. The data do not isolate whether the extra attempts were caused by the receipt, plugin instructions, or ordinary model variation.

## Frozen no-model SHAM decomposition

The [tri-arm plan](sham_expectations.json) and [control](fixtures/frozen_sham_package_control.py) were pushed as `26fe3f5` before execution. All arms used the real Codex CLI and the same synthetic two-request provider sequence. OFF had no package and ran the child directly; SHAM exposed the installed package and ran it directly; ON exposed the same package and wrapped the child with `receipt_command.py`. Each child ran once, and all three request ledgers reconciled. The [aggregate](sham_result.json) is checked by `python3 experiments/explicit_receipt_profile/reduce_sham_result.py`.

| Serialized provider request bytes | OFF | SHAM | ON |
| --- | ---: | ---: | ---: |
| First request | 50,108 | 50,571 | 51,164 |
| Second request | 53,907 | 54,370 | 53,583 |
| Both requests | 104,015 | 104,941 | 104,747 |

Across the two requests, package exposure added **926 bytes** (SHAM minus OFF). The explicit receipt saved **194 bytes** relative to SHAM, leaving ON **732 bytes larger** than OFF. On the second request alone ON was 787 bytes below SHAM, but its longer first command and package context consumed that advantage. The output-only marker appeared in OFF and SHAM's second requests and only in ON's verified full local artifact. These are request bytes under fixed synthetic provider responses, not observed model tokens, billed cost, or repair quality. The previous two-arm run is a separate control and need not have byte-identical dynamic metadata.
