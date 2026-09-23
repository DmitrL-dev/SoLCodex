# Changelog

All notable changes to this project are documented here.

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
