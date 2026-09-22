# Changelog

All notable changes to this project are documented here.

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
