# Changelog

All notable changes to this project are documented here.

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
