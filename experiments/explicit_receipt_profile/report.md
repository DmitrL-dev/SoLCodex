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
