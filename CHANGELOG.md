# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Research and documentation

- Reviewed RRSI's held-out transfer and token-cost evidence, audited its public selection code, and added a frozen-task/complete-telemetry gate to the research protocol; no plugin runtime change.
- Reviewed Unreal Agent's Terminal-Bench claim against its public Harbor job and source; documented the benchmark's cost-audit limits and a separate harness-level A/B design.
- Added a source-linked retrieval-economics review and a pilot that makes an agent recover evidence omitted from a bounded receipt; the pilot passed verification but used more total tokens and time with the adapter.
- Added an opt-in bounded artifact-search research prototype, focused tests, and CI coverage after broad artifact queries replayed the full diagnostic twice.
- Increased the existing adapter timeout-test window so child startup under a busy CI host does not erase the expected partial-output fixture.
- Recorded a same-fixture bounded-search development pair with equal verification and lower observed provider tokens/time, while keeping it separate from held-out evidence.
- Recorded a separate cache-boundary repair pair with equal verification, fewer observed provider tokens, and longer elapsed time; expanded the research review with prompt-cache economics.
- Recorded a historical CRLF loader pair whose apparent ON success failed a broader behavior matrix; added regression tests for source-line-ending invariance and bootstrap pin integrity.
- Probed macOS filesystem isolation for historical-task agents and documented the remaining protection work before a confirmatory campaign.
- Added a cost-frontier research protocol based on provider-billed coding-agent studies and independent Astra review.
- Extended the opt-in artifact search with bounded line-range retrieval and explicit reporting when long matched lines are omitted.
- Recorded a two-pair real-repository receipt pilot, aggregate-only evidence, and invalid exploratory attempts.
- Verified and documented that a new turn in the same task adopted the updated hook after a cache-preserving CLI upgrade; the original code-mode result remained visible.
- Confirmed that a nonzero tool exit preserved its status without rejecting a nested Promise, and documented command-selection guidance for the opt-in adapter.

## [0.1.9] - 2026-09-24

### Fixed

- Large `PostToolUse` receipts now use non-blocking feedback. The hook no longer intentionally rejects a code-mode tool promise after the tool has run. Current code-mode hosts may still return the original nested result to JavaScript, so receipt byte counters do not prove reduced model input.

### Documentation

- Added a source-linked research map and ranked A/B plan for context efficiency, provider caching, and retrieval cost.
- Clarified that structured artifacts contain extracted text and documented the observed code-mode Promise rejection after packed tool results.
- Documented existing upstream hook-result proposals and a capability-based path to future transparent code-mode replacement.

### Added

- Aggregate-only parser for paired `codex exec --json` traces, with private-manifest methodology, focused tests, and CI coverage on Python 3.9–3.12.
- Opt-in POSIX command receipt research prototype with bounded output, private artifacts, explicit exit status, timeout, and focused tests. It is not packaged into the installed plugin.

### Research

- Recorded a frozen three-pair explicit-adapter pilot with equal verifier success, smaller first results, fewer aggregate total tokens, and longer aggregate elapsed time. No general savings claim follows from this pilot.

## [0.1.8] - 2026-09-23

### Added

- A pinned, stable hook bootstrap stores immutable runtime snapshots in `PLUGIN_DATA` and binds them to a task and its lexical `PLUGIN_ROOT`. Previously loaded hooks can recover after a cache path is pruned, while a newly resolved root selects the new runtime in the same task.
- Explicit diagnostics for unavailable or ambiguous snapshots and for a cache path reused with different runtime bytes.

### Fixed

- Unsupported or unreadable state schemas no longer silently clear pending verification debt.

## [0.1.7] - 2026-09-23

### Changed

- The default packing threshold changed from 12 KiB to 6 KiB to fit observed host-truncated hook output. The Astra-specific threshold remains 4 KiB. The released threshold was not measured end to end.

## [0.1.6] - 2026-09-23

### Changed

- Large `pytest` and `unittest` output stays inline when the tool response has no trustworthy exit code. This avoids sending agents to saved artifacts merely to determine whether a test passed. Known-status test output and non-test output can still be packed.

## [0.1.5] - 2026-09-23

### Fixed

- Lifecycle launchers now exit successfully if the plugin script disappears between the cache check and Python loading it on macOS, Linux, or Windows.

## [0.1.4] - 2026-09-23

### Fixed

- Lifecycle commands now exit successfully when an already-running task points to a plugin cache removed by a later update. New tasks still run the hook normally; already-loaded commands from older releases cannot be changed retroactively.

## [0.1.2] - 2026-09-23

### Fixed

- The root plugin manifest hid all lifecycle hooks in Codex `0.155.0-alpha.16`. The plugin now declares its hooks in the sole `.codex-plugin/plugin.json` manifest.

## [0.1.1] - 2026-09-23

### Fixed

- Pending or failed verification now produces a non-blocking `Stop` warning, so the hook cannot trap the task or hide its final answer. Verification debt remains recorded and must be reported accurately.

## [0.1.0] - 2026-09-23

### Added

- Portable Codex marketplace and compatibility manifests.
- Local hooks for bounded large-output receipts and exact observation artifacts.
- Model-aware byte thresholds with an Astra-specific profile.
- Verification-debt tracking across task compaction.
- Generation-bound verifier results so stale checks cannot clear newer code changes.
- Permission-safe verifier status sidecars for plain-string Bash results in `bypassPermissions` mode.
- Conservative verifier handling that rejects ambiguous shell commands, preserves `set -e` behavior, and fails closed on interruption.
- Aggregate per-model byte reporting.
- Ownership-safe portable installation and bundled MIT notices.
- Repository validation, deterministic receipt benchmarks, architecture diagrams, and public documentation.
