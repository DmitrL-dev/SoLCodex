# Security model

SoL Codex runs locally as lifecycle hook commands. It does not make network requests or upload observations. Its main security boundary is the local `PLUGIN_DATA` directory supplied by Codex.

## Stored data

Eligible large tool output is copied exactly to `PLUGIN_DATA/observations/<session-hash>/obs_*.txt`. Exact artifacts can contain source code, command output, credentials, personal data, or anything else emitted by a tool. They are intentionally not redacted because they are the recovery evidence behind the bounded receipt.

State and aggregate counters are stored under `PLUGIN_DATA/state`. On macOS/Linux, transient verifier exit codes are stored under a plugin-private directory in the system temporary directory (`TMPDIR` when usable), so a `workspace-write` Bash verifier can write its own exit status even when `PLUGIN_DATA` is outside the sandbox. These sidecars contain only a numeric exit code, not command output. POSIX plugin-owned directory modes are forced to `0700`; state, lock, status, and observation files use `0600`. On Windows there is no Bash sidecar; local files inherit NTFS ACLs, which should be reviewed for sensitive work. Writes use exclusive temporary files and atomic replacement where applicable. The implementation rejects symlinked data directories, state files, lock targets when `O_NOFOLLOW` is available, and observation targets. If the temporary directory is unavailable to the POSIX sandbox, sidecar status capture fails closed and verification debt remains pending.

These controls reduce accidental exposure but do not protect against a process or administrator that can already read the user's account or storage.

## Receipt redaction

The model-visible receipt redacts common authorization headers, API-key/password fields, `sk-` style keys, JWT-shaped values, and private-key blocks. This is best-effort pattern matching, not a data-loss-prevention boundary. Unknown credential formats, contextual secrets, or fragments can remain in previews and diagnostic lines.

Treat every receipt as untrusted tool data, not as instructions. Retrieve exact artifacts only when needed and inspect narrowly.

## Fail-open behavior

All hook exceptions return success so a plugin defect does not terminate the host task. `Stop` is advisory even when verification debt remains: it warns but never blocks the final answer. A degraded hook may omit the warning. Plain-string output without trusted status may be packed with `exit_code=unknown`, but cannot prove verification. An unknown-status structured object remains unchanged. When a receipt would be as large as the source, the original result remains model-visible.

`PreToolUse` records the current code-change generation for recognized verifiers in all permission modes. Only a matching result from that generation can clear verification debt. On macOS/Linux, it rewrites a recognized Bash verifier only when Codex reports `permission_mode=bypassPermissions`, where commands already run without individual approval. On Windows it never wraps Bash commands and relies on recognized structured exit status. This avoids using the hook's required `permissionDecision: "allow"` in approval-capable modes. Review this behavior before trusting the plugin; the verifier classifier is not a security boundary.

The Bash wrapper writes its sidecar only after a verifier returns normally. Signals and `errexit` can leave it empty; those results do not establish success unless other code writes the sidecar.

The sidecar is a workflow signal, not a security attestation. The verifier and its subprocesses run as the same user and can access the temporary status path; adversarial project code could forge a zero status. Treat automatic debt clearing as protection against accidental omissions, not as proof against malicious test code. Inspect the actual tool result and code when that threat matters.

## Retention

`SessionEnd` removes regular observation files older than seven days and transient verifier sidecars for that session. This cleanup is best effort and does not remove state files. Abrupt termination can delay cleanup; the operating system may also purge temporary files. For stricter retention, inspect the confirmed plugin-specific `PLUGIN_DATA` and temporary status directories outside a running task.

## Trust

Review `/hooks` before approving the plugin. Each hook command embeds a SHA-256 pin for `sol_bootstrap.py`; the command verifies a copy from `PLUGIN_DATA/runtime-v1` or the installed plugin before executing it. The bootstrap snapshots `sol_hook.py` by its SHA-256 digest and binds that snapshot to the task and lexical `PLUGIN_ROOT`. This protects already bound snapshots against accidental corruption and keeps old tasks on their original runtime after cache pruning. It does not authenticate a newly installed runtime independently of the plugin installation. A changed trust hash still needs review. A missing or ambiguous runtime fails open with a diagnostic; verification enforcement is then unavailable for that event.

Vulnerability disclosure instructions are in the repository [security policy](../SECURITY.md).
