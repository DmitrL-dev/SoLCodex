# Verifier-shaped PreToolUse boundary: development probe (2026-09-24)

A temporary plugin rewrote **one exact** `python -m unittest -v test_probe` command before execution. The driver was configured for `codex-cli 0.155.0-alpha.16.3` in code mode; the audit cannot independently attest the binary version or nested JavaScript `await`. The hook event identified the tool as `Bash`; the verifier child ran once. Its large output was saved in separate private stdout/stderr artifacts, and a 29-byte status reached the model. This tests one host boundary, not the installed SoL Codex plugin, shell equivalence, repair quality, or token savings.

## Method and result

Two isolated runs used the same artificial passing test. Its stdout contained 8,000 ordinary bytes and one freshly generated `HIDDEN_` marker absent from the prompt. The agent was asked to run the exact command once and report the marker only if it appeared in the tool result. Direct execution and a temporary `PreToolUse` plugin each used a new Codex home and an instrumented local HTTP route to the ChatGPT Codex backend. The temporary plugin used CLI hook-trust and approval/sandbox bypass flags inside an outer macOS Seatbelt; this does not qualify the normal installed-plugin permission path. The route checked marker membership in model requests in memory and discarded request bodies and authorization data. The hook copied no marker into its compact result. Both runs had two completed model responses and a one-attempt-per-request journal with observed usage; this is **not** independent provider billing.

| Observation | Direct | Temporary PreToolUse |
| --- | ---: | ---: |
| Verifier child executions | 1 | 1 |
| CLI tool-result bytes | 8,180 | 29 |
| Marker in next model request | Yes | No |
| Marker in saved artifact | No artifact | Yes |
| Hook event and tool | None | `PreToolUse` / `Bash` |
| Final answer | Marker | `UNKNOWN` |

The [fixed-field aggregate](data/2026-09-24-verifier-prehook-boundary-dev.json) includes next-request sizes, per-response usage, private trace hashes, matching hashes of normalized fixtures and verifier commands, and negative qualification flags. [`scripts/audit_verifier_prehook_probe.py`](../../scripts/audit_verifier_prehook_probe.py) recomputes the table from private artifacts. It rejects mismatched fixtures/commands, missing final usage, wrong hook events, duplicate child execution, missing or failed compact status, mismatched capture byte counts, altered fixture stdout, symlinked stream artifacts, and marker leakage in any model answer. Synthetic counterexamples exercise those checks. It emits no marker, prompt, output, credential, or private path. Raw traces and copied authentication were not published; the temporary authentication copy was removed after each run. The aggregate cannot be independently replayed without the private inputs.

An initial direct preflight used an unqualified `python3` from the CLI's PATH. That command hit an Xcode license shim and exited 69 before running the test. It was excluded from the two-run mechanism comparison; its private trace is retained as a setup failure. Both reported runs instead pinned the same absolute Python interpreter in the command. Neither test run repaired a repository, and the artificial 8 KiB output is not evidence of a natural packaging opportunity.

## Limits and next gate

The temporary capture helper buffered stdout and stderr into separate artifacts, changing the visible result and potentially timing. It did not test nonzero verifier exits, stdin, signals, cancellation, shell operators, cache pruning, competing hooks, WebSocket transport, actual nested JavaScript `await` consumption, or installed-plugin fallback. Four non-model connections were blocked in the hook run; the proxy's two completed POSTs are not a complete network-attempt inventory. The direct and hook prompts, cache states, and model trajectories cannot support a causal token or money comparison. A smaller next-request body here is a mechanism observation only.

A candidate installed-plugin treatment would need a fixture-bound allowlist of exact batch verifier commands, a child-exactly-once contract, complete output artifacts, original output on small or failed runs, and explicit incomplete states. Before an exposed-task A/B, test actual installed runtime and permission gates, child exit/signal and cancellation behavior, storage failures, concurrent or nested tool calls, and independent external repair verifiers. Missing provider usage remains unknown, never zero. The existing broad `is_verifier()` classifier is insufficient to authorize command rewriting.
