# Quiet diagnostic: exposed model screen protocol

Superseded for subsequent requests by [revision 2](2026-09-25-quiet-diagnostic-model-screen-protocol-v2.md) after a provider compatibility check. No four-run screen was launched under this revision.

Status: frozen before model requests under this screen. This is an exposed
development check on two known Python issues, not a held-out estimate of token
savings or quality noninferiority. The machine-readable schedule, thresholds,
source, runtime, CLI, evaluator, regression-patch and runner hashes are in
[the frozen protocol](../measurements/data/2026-09-25-quiet-diagnostic-model-screen-development.json).
No screen outcome is reported in this document.

## Assignment and treatment

Four runs use fresh, separate source trees and environments. The manually
counterbalanced order is Click #2447 verbose, Click #2447 quiet, packaging
#928 quiet, packaging #928 verbose. The seed recorded in the JSON existed
before outcomes but did not generate the assignment; this is not randomized.
There is no automatic retry, optional exclusion, or replacement run. A
technical accounting or isolation failure stops subsequent launches; every
assigned and unstarted run remains in the report.

Each run uses Codex CLI `0.155.0-alpha.16.3`, `gpt-6-luna`, low reasoning,
code mode, a 600-second shared deadline and at most 32 model requests. The
first tool action must run the corresponding isolated `tools/venv/bin/python`
pytest command. The verbose arm uses `-v`; the quiet arm uses
`-q --tb=short`. Both use `-p no:cacheprovider -o addopts=`. Click targets
`tests/`; packaging targets `tests/test_metadata.py`. The full prompt
template is pinned by the runner hash. The question and all instructions
after the diagnostic are identical within a task. The baseline test command
must finish with exit code 1 before any inspection or edit; a complete CLI
trace must confirm this sequence.

The visible issue texts and regression patches are public. The Click source
parent is `16fe802a3f96c4c8fa3cd382f1a7577fda0c5321`; the packaging
source parent is `3f83dea9b60e660e464535a1019d2de62723884f`.
Each run copies a pinned local Python runtime. The host fixes CLI path and
source hashes before launch.

## Boundaries and measurements

The CLI runs in a macOS Seatbelt profile. It can read and edit only its
isolated workspace and temporary environment, and it can connect only to a
loopback sidecar. A separate host bridge owns the provider credential. Before
credential access, it checks the route, capability, run and attempt IDs,
request framing, model, reasoning effort and streaming mode. The two host
ledgers join on attempt ID. A complete upstream `response.completed` event
with input, output and cached-token counts is required for observed usage.
An unmatched, incomplete, or invalid attempt makes cost unknown. The bridge
continues to account for a provider response after the CLI disconnects.
Observed usage is not a provider billing reconciliation.

The reported wall clock starts at CLI launch and ends when its upstream
attempts finish or the shared deadline closes them. CLI completion time is
reported separately. Fixture copying, canaries and independent evaluation
are excluded from the wall-time comparison. The independent, pinned evaluator
checks the complete source checkpoint, protected tests and configuration,
external behavior cases and frozen upstream tests. The quality evaluator is
private in this development run; its hash and trusted-assets hash are public.
Its parent, historical-fix, wrong-fix and fake-pass controls were exercised
before freeze. This is a source-checkpoint check, not a wheel/install or
hostile-code security guarantee.

## Decision rule

The screen passes only if all four assigned runs complete cleanly, all four
pass the independent quality evaluator, all four first diagnostics satisfy
the exact command, completed trace and expected failure exit, and every
accepted model attempt has observed complete usage. Summed quiet
input-plus-output tokens must be at most 0.85 times summed verbose tokens;
quiet tokens must be no greater than verbose for either task. Summed quiet
wall time must be at most 1.25 times summed verbose wall time. An unknown or
unmet gate fails the screen. Cached input is recorded separately and remains
part of input tokens, not an extra amount added to the total.

Before the four assigned runs, a separate one-request compatibility smoke
may verify that the frozen CLI and host bridge can reach the provider and
parse its response. It is not part of the screen and cannot be substituted
for a run. If compatibility requires changes, a new public protocol revision
and hash must be frozen before starting the four runs.

Even a passing four-run screen would only justify a separately frozen,
expanded development study. A general savings claim needs held-out A/B
evidence, external behavioral quality checks, and complete telemetry. See
the [held-out design proposal](2026-09-25-heldout-quiet-screen-proposal.md)
for that later stage.
