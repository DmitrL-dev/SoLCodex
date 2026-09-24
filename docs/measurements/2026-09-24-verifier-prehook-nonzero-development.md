# Nonzero verifier PreToolUse boundary: development probe (2026-09-24)

A follow-up to the [passing verifier probe](2026-09-24-verifier-prehook-boundary-development.md) used the same temporary `PreToolUse` plugin and exact `python -m unittest -v test_probe` command, but made the test fail with `AssertionError: expected diagnostic failure`. This tests whether a command that detects a defect still runs once and returns its nonzero status when captured before its tool result reaches the model. It does not test the installed SoL Codex plugin or repair quality.

Both isolated runs used fresh random output-only markers, separate Codex homes, the same pinned Python interpreter and failing fixture template, and the local HTTP route described in the earlier probe. The route tested marker membership in memory and discarded request bodies and authorization values. Its SQLite journal recorded two completed model attempts in each run; it does not reconcile provider billing. A fixed-field [aggregate](data/2026-09-24-verifier-prehook-nonzero-dev.json) binds normalized fixture and command hashes, private trace and proxy-summary hashes, status, request observations, and source-artifact properties. The [auditor](../../scripts/audit_verifier_prehook_probe.py) checks the pinned failing fixture template and exact capture helper, including propagation of `subprocess.run(...).returncode`, one child execution, a `unittest` traceback in the saved stderr, matching compact status and CLI exit code, complete response usage observations, and the marker boundary. It emits a fixed error message on invalid private input.

| Observation | Direct | Temporary PreToolUse |
| --- | ---: | ---: |
| Verifier child executions | 1 | 1 |
| Verifier and CLI command exit code | 1 | 1 |
| CLI tool-result bytes | 8,673 | 29 |
| Output-only marker in next model request | Yes | No |
| Marker in private stdout artifact | No artifact | Yes |
| Failure traceback in private stderr artifact | No artifact | Yes |

The direct result included the failure text and marker. The temporary helper saved stdout and stderr separately, returned `CAPTURED_BYTES=8673 STATUS=1`, and exited 1. The model's final `UNKNOWN` in that arm is expected: the 29-byte receipt withheld the diagnostic as well as the marker. This is a mechanism check for nonzero status and next-request visibility, **not** evidence that an agent can repair the failure from this receipt. The two runs' next-response input counts were 11,266 and 10,159, but cache state and model trajectory differed; those counts are not a causal token-saving estimate.

The temporary hook used the CLI's trust and approval/sandbox bypass flags inside a separate macOS sandbox. The audit does not independently attest the CLI binary version or that a nested JavaScript `await` consumed the command result. Shell redirection, stdin, signals, cancellation, concurrent calls, storage failure, cache pruning, competing hooks, WebSocket transport, and an installed-plugin permission path remain unqualified. An opt-in repair experiment needs a bounded, reliable way to retrieve the failure diagnosis and must verify external repair behavior. No provider bill or general SoL Codex saving follows from this probe.
