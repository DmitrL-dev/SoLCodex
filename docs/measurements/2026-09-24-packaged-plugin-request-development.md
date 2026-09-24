# Packaged plugin next-request probe under workspace-write (2026-09-24)

This development probe exercised a copy of the public SoL Codex `0.1.9+codex.20260924` package from the repository in an isolated Codex CLI home. A no-plugin control used the same driver, artificial verifier fixture, command, prompt, `gpt-6-sol` at medium effort, code mode, and requested `workspace-write` sandbox. The plugin arm enabled lifecycle hooks with `--dangerously-bypass-hook-trust`. Neither final arm used `--dangerously-bypass-approvals-and-sandbox`. This is not an attestation of the desktop installation, normal hook trust, the event's actual `permission_mode`, or independent provider billing.

Each verifier printed 8,000 ordinary bytes and a fresh output-only `HIDDEN_` marker, then passed one unittest. An instrumented local HTTP route forwarded model requests and checked marker membership without retaining request bodies or authorization data. Both runs completed two model responses with a one-attempt-per-request journal. Raw traces, fixture markers, and authentication copies were kept private; authentication copies were removed after each run.

| Observed field | No plugin | Packaged plugin |
| --- | ---: | ---: |
| Verifier child executions | 1 | 1 |
| CLI tool-result bytes | 8,180 | 8,180 |
| Marker in second model request | Yes | Yes |
| Second request body bytes | 64,646 | 65,676 |
| Second response input tokens | 11,787 | 12,018 |
| Proxy-observed total input tokens | 22,395 | 22,831 |
| Local plugin `saved_bytes` | N/A | 6,967 |

The packaged hook's `PreToolUse` status wrapper and its verified exit status were observed. Its `PostToolUse` state recorded 8,180 source bytes, a 1,213-byte receipt, and 6,967 local `saved_bytes`. The CLI trace held the original large result; its output-only marker appeared in the second model request. Marker membership does not prove every original byte reached that request. The local counter did **not** measure model-input reduction in this run. The plugin arm's second request and observed token count were larger; a single sequential pair with different plugin context and uncontrolled cache/trajectories is not a causal cost comparison. The model did no repository repair, so quality was not tested.

The [fixed-field aggregate](data/2026-09-24-packaged-plugin-request-dev.json) records private artifact hashes, matching fixture/command/prompt/driver hashes, the packaged source hash, request sizes, observed usage, and negative qualification flags. [`scripts/audit_packaged_plugin_request_probe.py`](../../scripts/audit_packaged_plugin_request_probe.py) recomputes it from private traces, proxy journal, harness manifest, run count, and packaged hook state. Its synthetic tests reject altered marker flags, missing usage, duplicate execution, conflicting feature switches, malformed status wrappers, impossible local counters, mismatched package files, forbidden bypass flags, and cross-arm fixture changes. The auditor emits no marker, prompt, raw output, credential, or private path. The published aggregate cannot be independently replayed without the private inputs; manifest flags and CLI version are not independently attested by the proxy.

Three preliminary runs with an outer macOS Seatbelt produced no command execution. A read of the private CLI stderr showed `exec_command failed: CreateProcess ... Operation not permitted (os error 1)` when Codex tried to create its own sandbox. Those invalid runs were excluded. The final two runs used Codex's requested `workspace-write` sandbox without the outer Seatbelt. The plugin arm still produced the existing status wrapper; the hook source enters that branch only when it sees `permission_mode == "bypassPermissions"`. This implication from source and trace does not establish how the host mapped sandbox and approval settings, or whether an approval-capable user flow behaves the same way. Four non-model connections were blocked by the proxy in the plugin arm; the two completed POSTs do not constitute a complete network-attempt inventory.

**Decision:** no installed-plugin token-saving claim. A future opt-in pre-execution capture profile needs separate literal-command admission, preserved permission decisions, one child execution, terminal status provenance, artifact completeness, and actual next-request checks under normal hook trust and nested `await`. External repair verifiers and complete provider usage remain required before any quality-adjusted saving claim.
