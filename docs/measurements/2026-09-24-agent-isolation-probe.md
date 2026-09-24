# macOS agent-isolation development probe (2026-09-24)

The [historical CRLF pair](2026-09-24-historical-crlf-development.md) exported a pre-fix Git tree, but its ON prompt contained absolute paths to research tools inside the live repository that also held the future fix. A fresh one-commit fixture therefore removed Git-history leakage without enforcing filesystem isolation. This probe tested whether a macOS Seatbelt policy could close that particular path before more historical tasks are run.

On macOS 27.0 with Codex CLI `0.155.0-alpha.16.3`:

| Probe | Observation |
| --- | --- |
| Direct `sandbox-exec` child reading a denied live-repository file | `Operation not permitted`, exit 1 |
| Codex under an outer `sandbox-exec` policy with its own `workspace-write` sandbox | No command execution item; nested sandbox violation was recorded |
| Codex under the outer policy with its inner sandbox disabled | A harmless `pwd` command executed and produced a command result |
| The same setup, with a read deny on the live repository | An attempted file read produced a command result with exit 1 and `Operation not permitted`; no file content was returned |
| Direct file creation under a denied Documents subtree | `Operation not permitted`; the test file was not created |

The working *development* configuration used an outer `sandbox-exec` profile with default allow rules plus explicit read and write denies, and Codex's `danger-full-access` mode so its nested Seatbelt sandbox did not conflict. Copied authentication was removed after each CLI probe. Raw traces and temporary homes stayed private; no credential or repository content appears in this note.

This is **not** a general sandbox or a completed confirmation harness. Default-allow leaves host paths open unless explicitly denied, and the probes exercised only one read target and one write subtree. A later campaign must stage the adapter/search files outside the fixed checkout, deny the golden patch, hidden verifier, other arm and live repository, and verify those denials with canary reads before each run. It must also constrain writes beyond the test subtree and check whether the policy still works after Codex or macOS changes. This macOS mechanism does not establish Windows or Linux behavior and does not change the installed plugin.
