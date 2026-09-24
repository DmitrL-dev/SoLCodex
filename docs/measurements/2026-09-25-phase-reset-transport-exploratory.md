# Phase-reset session transport probe (2026-09-25)

A [no-model controller](fixtures/2026-09-25-phase-reset-transport-probe.py) exercised the real Codex CLI with a synthetic loopback provider. It started a diagnosis session, resumed that session, then started a reset session with an explicit handoff and a **new empty `CODEX_HOME`**. The workspace content was identical. Each invocation ran under a separate macOS Seatbelt profile where applicable; the reset profile denied reading an actual file in the diagnosis session directory with `PermissionError`.

The [aggregate result](data/2026-09-25-phase-reset-transport-exploratory.json) passed these checks:

| Check | Observation |
| --- | --- |
| Resume session ID | Same as diagnosis |
| Reset session ID | New; reset home contained no sessions before launch |
| Diagnosis-only marker in resume provider request | Present |
| Diagnosis-only marker in reset provider request | Absent |
| Explicit handoff in reset provider request | Present |
| Provider accounting | One completed request per turn, three of three reconciled, no provider errors |

The provider returned scripted assistant messages and fixed synthetic usage of 120 input plus 20 output tokens per turn. Those token counts cannot measure savings. This probe establishes that the CLI and current transport can produce the intended session boundary. It does not test model behavior, repair quality, billing, or whether a compact handoff preserves enough information for real tasks.

This was an exploratory run: the controller was not committed before execution, and its imported host bridge, sandbox, process manager, and synthetic provider remain in the retained private harness. The published controller is byte-identical to the executed file (SHA-256 in the aggregate), but it is not independently executable from this repository alone. Markers, raw provider bodies, session files, and local paths are withheld. The next model experiment needs a prospectively frozen controller and handoff rule, multiple independent diagnosis tasks, external quality checks, and complete request accounting for retries and late responses.
