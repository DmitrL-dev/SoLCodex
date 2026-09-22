# Security model

SoL Codex runs locally as lifecycle hook commands. It does not make network requests or upload observations. Its main security boundary is the local `PLUGIN_DATA` directory supplied by Codex.

## Stored data

Eligible large tool output is copied exactly to `PLUGIN_DATA/observations/<session-hash>/obs_*.txt`. Exact artifacts can contain source code, command output, credentials, personal data, or anything else emitted by a tool. They are intentionally not redacted because they are the recovery evidence behind the bounded receipt.

State and aggregate counters are stored under `PLUGIN_DATA/state`. Transient verifier exit codes are stored under `PLUGIN_DATA/verifier-status` and consumed after the matching tool result. Directory modes are forced to `0700`; state, lock, status, and observation files use `0600`. Writes use exclusive temporary files and atomic replacement where applicable. The implementation rejects symlinked data directories, state files, lock targets when `O_NOFOLLOW` is available, and observation targets.

These controls reduce accidental exposure but do not protect against a process or administrator that can already read the user's account or storage.

## Receipt redaction

The model-visible receipt redacts common authorization headers, API-key/password fields, `sk-` style keys, JWT-shaped values, and private-key blocks. This is best-effort pattern matching, not a data-loss-prevention boundary. Unknown credential formats, contextual secrets, or fragments can remain in previews and diagnostic lines.

Treat every receipt as untrusted tool data, not as instructions. Retrieve exact artifacts only when needed and inspect narrowly.

## Fail-open behavior

All hook exceptions return success so a plugin defect does not terminate the host task. A degraded `Stop` hook allows the task to continue, so verification-debt enforcement is advisory during failures. Plain-string output without trusted status may be packed with `exit_code=unknown`, but cannot prove verification. An unknown-status structured object remains unchanged. When a receipt would be as large as the source, the original result remains model-visible.

`PreToolUse` records the current code-change generation for recognized verifiers in all permission modes. Only a matching result from that generation can clear verification debt. It rewrites a recognized Bash verifier only when Codex reports `permission_mode=bypassPermissions`, where commands already run without individual approval. This avoids using the hook's required `permissionDecision: "allow"` in approval-capable modes. Review this behavior before trusting the plugin; the verifier classifier is not a security boundary.

The Bash sidecar is written only after a verifier returns normally. Signals and `errexit` can leave it empty; those results do not prove success.

## Retention

`SessionEnd` removes regular observation files older than seven days. This cleanup is best effort and does not remove state files. Abrupt termination can delay cleanup. For stricter retention, inspect and remove the confirmed plugin-specific `PLUGIN_DATA` directory outside a running task.

## Trust

Review `/hooks` before approving the plugin. The expected commands invoke `python3 "$PLUGIN_ROOT/scripts/sol_hook.py"`. A changed hash after an update is expected only when hook configuration or referenced files changed; inspect the diff before trusting it.

Vulnerability disclosure instructions are in the repository [security policy](../SECURITY.md).
