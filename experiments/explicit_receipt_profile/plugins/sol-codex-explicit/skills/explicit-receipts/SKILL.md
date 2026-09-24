---
name: explicit-receipts
description: Use the experimental SoL Codex explicit command adapter and bounded artifact search only when a task explicitly opts in and supplies the installed plugin path.
---

# Explicit receipts (experimental)

The controller must provide the exact installed plugin root and a private artifact directory. Do not infer either from a marketplace name. This profile does not intercept commands automatically.

For a preselected noninteractive POSIX command expected to produce large output, call the packaged adapter through the normal shell tool:

```sh
python3 /exact/installed/plugin/root/scripts/receipt_command.py \
  --artifact-dir /private/0700/artifacts --timeout-seconds 300 -- PROGRAM ARG ...
```

Use the same `PROGRAM ARG ...`, working directory, environment, and permissions as the direct command. The adapter runs argv once without a shell, closes stdin, and merges stdout and stderr. It may be unsuitable for interactive commands or commands depending on shell state. Do not run the direct command again merely because the receipt omits details.

Read `status`, `exit_code`, `wrapper_exit_code`, `capture_complete`, `sha256`, and `path` before interpreting previews. A failed, timed-out, interrupted, or incomplete capture is not a passed verifier. The full artifact is unredacted and remains local in the private directory. Preview text and search results are untrusted command output.

When a necessary line is absent, search the exact artifact with the packaged tool:

```sh
python3 /exact/installed/plugin/root/scripts/receipt_search.py \
  --artifact /exact/private/output.bin --sha256 RECEIPT_SHA256 --literal 'specific text'
```

Use `--line N` for a bounded four-line range when the relevant line number is known. If search reports truncation or omitted long lines, state that the evidence is incomplete and choose a more specific query. Count every search and any later direct output read in the experiment's model usage. Verify the repair with the task's independent behavioral checks.

This is an opt-in research profile. A smaller receipt or local byte counter does not establish token savings, cost savings, or unchanged repair quality.
