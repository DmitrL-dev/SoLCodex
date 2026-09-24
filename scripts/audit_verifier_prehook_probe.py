"""Reduce private verifier-shaped PreToolUse probes to fixed-field evidence.

The private request bodies, authorization, random marker, command output, and
local paths are never emitted. This establishes only an observed host boundary.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shlex
import sys

try:
    from scripts.audit_prehook_request_probe import tokens
except ModuleNotFoundError:
    from audit_prehook_request_probe import tokens


MARKER = re.compile(rb"HIDDEN_[0-9a-f]{24}")
STATUS = re.compile(rb"CAPTURED_BYTES=([0-9]+) STATUS=([01])\n?\Z")
SUCCESS = re.compile(rb"test_output \(test_probe\.Probe\.test_output\) \.\.\. ok\r?\n.*Ran 1 test in [0-9.]+s\r?\n\r?\nOK\r?\n?\Z", re.S)
FAILURE_FIXTURE_TEMPLATE_SHA256 = "a3e057b8502a9cc289586dfbac7caa4c88f0acdfd60afac7129ce46178482438"


def failure_pattern(root: Path, merged_streams: bool = False) -> re.Pattern[bytes]:
    source = re.escape(str(root / "work/test_probe.py").encode())
    first = (rb"FAIL: test_output \(test_probe\.Probe\.test_output\)\r?\n"
             if merged_streams else
             rb"test_output \(test_probe\.Probe\.test_output\) \.\.\. FAIL\r?\n")
    return re.compile(
        first +
        rb".*Traceback \(most recent call last\):\r?\n"
        rb".*File \"" + source + rb"\", line 8, in test_output\r?\n"
        rb"\s*self\.fail\('expected diagnostic failure'\)\r?\n"
        rb"AssertionError: expected diagnostic failure\r?\n"
        rb".*Ran 1 test in [0-9.]+s\r?\n\r?\nFAILED \(failures=1\)\r?\n?\Z", re.S)


def shell_payload(command):
    if not isinstance(command, str):
        raise ValueError("CLI command text is missing")
    wrapper = shlex.split(command)
    if len(wrapper) != 3 or wrapper[:2] != ["/bin/zsh", "-lc"]:
        raise ValueError("unexpected shell wrapper")
    return shlex.split(wrapper[2])


def captured_argv(source):
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise ValueError("invalid private capture helper syntax") from None
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and
             isinstance(node.func, ast.Attribute) and
             isinstance(node.func.value, ast.Name) and
             node.func.value.id == "subprocess" and node.func.attr == "run"]
    if len(calls) != 1 or not calls[0].args:
        raise ValueError("expected one literal subprocess child call")
    try:
        argv = ast.literal_eval(calls[0].args[0])
    except (ValueError, TypeError, SyntaxError) as error:
        raise ValueError("capture child argv is not literal") from error
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        raise ValueError("capture child argv is invalid")
    return argv


def expected_capture_source(artifacts: Path, python: str) -> str:
    argv_source = "[" + repr(python) + ",'-m','unittest','-v','test_probe']"
    return (
        "import pathlib,subprocess,sys\n"
        "root=pathlib.Path(" + repr(str(artifacts)) + ")\n"
        "with (root/'stdout.bin').open('wb') as out, (root/'stderr.bin').open('wb') as err:\n"
        " code=subprocess.run(" + argv_source + ",stdout=out,stderr=err).returncode\n"
        "size=(root/'stdout.bin').stat().st_size+(root/'stderr.bin').stat().st_size\n"
        "print(f'CAPTURED_BYTES={size} STATUS={code}')\n"
        "raise SystemExit(code)\n"
    )


def inspect(root: Path, prehook: bool, expected_exit: int = 0) -> dict:
    if expected_exit not in (0, 1):
        raise ValueError("unsupported verifier exit status")
    root = Path(root)
    artifacts = root / "artifacts"
    fixture = (root / "work" / "test_probe.py").read_bytes()
    matches = MARKER.findall(fixture)
    if len(matches) != 1:
        raise ValueError("expected exactly one private marker")
    marker = matches[0]
    fixture_template_sha256 = hashlib.sha256(fixture.replace(marker, b"<MARKER>")).hexdigest()
    if expected_exit and fixture_template_sha256 != FAILURE_FIXTURE_TEMPLATE_SHA256:
        raise ValueError("nonzero probe fixture differs from pinned failing test")
    count = (root / "work" / "run-count.log").read_text().splitlines()
    if count != ["run"]:
        raise ValueError("verifier child did not execute exactly once")

    summary_bytes = (artifacts / "proxy-summary.json").read_bytes()
    summary = json.loads(summary_bytes)
    requests = summary.get("sink_requests")
    blocked = summary.get("proxy_blocked")
    if (summary.get("exit") != 0 or type(blocked) is not int or blocked < 0 or
            not isinstance(requests, list) or len(requests) != 2):
        raise ValueError("expected a completed two-request CLI run")
    for request in requests:
        if (request.get("upstream_status") != 200 or request.get("done") is not True or
                request.get("client_disconnected") is not False or
                request.get("upstream_error") is not None or
                type(request.get("body_bytes")) is not int or request["body_bytes"] < 0 or
                type(request.get("request_has_probe_marker")) is not bool or
                not isinstance(request.get("completions"), list) or
                len(request["completions"]) != 1):
            raise ValueError("model request observation is incomplete")
        tokens(request)
    flags = [request["request_has_probe_marker"] for request in requests]
    if flags != ([False, False] if prehook else [False, True]):
        raise ValueError("next-request marker control failed")
    journal = summary.get("journal") or {}
    expected_usage = {
        "input_tokens": sum(tokens(request)["input_tokens"] for request in requests),
        "output_tokens": sum(tokens(request)["output_tokens"] for request in requests),
        "cached_input_tokens": sum(tokens(request)["cached_tokens"] for request in requests),
    }
    if (journal.get("attempts") != 2 or
            (journal.get("states") or {}).get("completed") != 2 or
            journal.get("all_attempts_have_observed_usage") is not True or
            journal.get("observed_completed_usage") != expected_usage or
            journal.get("provider_billing_complete") is not False):
        raise ValueError("attempt journal is incomplete or disagrees with responses")

    trace_bytes = (artifacts / "trace.jsonl").read_bytes()
    events = [json.loads(line) for line in trace_bytes.splitlines() if line.strip()]
    if sum(event.get("type") == "turn.completed" for event in events) != 1:
        raise ValueError("CLI turn did not complete once")
    items = [event.get("item", {}) for event in events if event.get("type") == "item.completed"]
    commands = [item for item in items if item.get("type") == "command_execution"]
    finals = [item.get("text", "") for item in items if item.get("type") == "agent_message"]
    if len(commands) != 1 or commands[0].get("exit_code") != expected_exit or not finals or \
            any(not isinstance(answer, str) for answer in finals):
        raise ValueError("verifier command or final answer is missing")
    raw_output = commands[0].get("aggregated_output")
    if not isinstance(raw_output, str):
        raise ValueError("CLI command result is missing")
    output = raw_output.encode()
    if (marker in output) == prehook or \
            ((marker.decode() in finals[-1]) == prehook) or \
            (prehook and any(marker.decode() in answer for answer in finals)):
        raise ValueError("tool result or final answer does not match marker control")
    if prehook and finals[-1].strip() != "UNKNOWN":
        raise ValueError("prehook model answer differs from withheld marker control")
    events_path = artifacts / "hook-events.jsonl"
    hook_events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
    expected_hook = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                     "tool_input_keys": ["command"], "command_matches_exact": True}
    if hook_events != ([expected_hook] if prehook else []):
        raise ValueError("hook event did not match the exact Bash command")
    executed = shell_payload(commands[0].get("command"))
    stdout = artifacts / "stdout.bin"
    stderr = artifacts / "stderr.bin"
    if prehook:
        if (len(executed) != 2 or not Path(executed[0]).is_absolute() or
                executed[1] != "capture.py"):
            raise ValueError("prehook did not run the expected capture command")
        capture_source = (root / "work" / "capture.py").read_text()
        verifier_argv = captured_argv(capture_source)
        expected = [executed[0], "-m", "unittest", "-v", "test_probe"]
        hook_source = (root / "market/plugins/verifier-event-probe/scripts/hook.py").read_text()
        if (verifier_argv != expected or repr(" ".join(expected)) not in hook_source or
                repr(" ".join(executed)) not in hook_source or
                capture_source != expected_capture_source(artifacts, executed[0])):
            raise ValueError("hook admission and capture child differ from direct verifier")
        if (not stdout.is_file() or not stderr.is_file() or
                stdout.is_symlink() or stderr.is_symlink() or stdout.samefile(stderr)):
            raise ValueError("separate regular output artifacts are missing")
        expected_stdout = b"A" * 8000 + b"\n" + marker + b"\n"
        stdout_bytes, stderr_bytes = stdout.read_bytes(), stderr.read_bytes()
        match = STATUS.fullmatch(output)
        expected_stderr = SUCCESS if expected_exit == 0 else failure_pattern(root)
        if (stdout_bytes != expected_stdout or not expected_stderr.fullmatch(stderr_bytes) or
                match is None or int(match.group(1)) != len(stdout_bytes) + len(stderr_bytes) or
                int(match.group(2)) != expected_exit):
            raise ValueError("prehook artifact or bounded result missing")
    else:
        verifier_argv = executed
        if (len(executed) != 5 or not Path(executed[0]).is_absolute() or
                executed[1:] != ["-m", "unittest", "-v", "test_probe"] or
                stdout.exists() or stderr.exists() or len(output) <= 6000):
            raise ValueError("direct command did not run the pinned verifier")
        if expected_exit and not failure_pattern(root, merged_streams=True).search(output):
            raise ValueError("direct failure diagnosis is missing")

    result = {"request_marker_flags": flags,
            "tool_name": "Bash" if prehook else None,
            "hook_event_name": "PreToolUse" if prehook else None,
            "exact_command_matched": prehook,
            "verifier_child_executions": 1,
            "command_output_bytes": len(output),
            "artifact_contains_marker": prehook,
            "separate_stream_artifacts": prehook,
            "fixture_template_sha256": fixture_template_sha256,
            "verifier_command_sha256": hashlib.sha256("\0".join(verifier_argv).encode()).hexdigest(),
            "next_request_body_bytes": requests[1]["body_bytes"],
            "next_request_usage": tokens(requests[1]),
            "proxy_observed_total_usage": expected_usage,
            "blocked_non_model_connections": blocked,
            "proxy_summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
            "cli_trace_sha256": hashlib.sha256(trace_bytes).hexdigest()}
    if expected_exit:
        result["verifier_exit_code"] = expected_exit
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct", required=True, type=Path)
    parser.add_argument("--prehook", required=True, type=Path)
    parser.add_argument("--expected-exit", type=int, choices=(0, 1), default=0)
    args = parser.parse_args()
    try:
        direct = inspect(args.direct, False, args.expected_exit)
        prehook = inspect(args.prehook, True, args.expected_exit)
        if (direct["fixture_template_sha256"] != prehook["fixture_template_sha256"] or
                direct["verifier_command_sha256"] != prehook["verifier_command_sha256"]):
            raise ValueError("direct and prehook runs used different verifier fixtures or commands")
    except Exception:
        print("Audit failed: private input rejected", file=sys.stderr)
        raise SystemExit(1) from None
    report = {"schema": ("solcodex.verifier-prehook-nonzero-development.v1"
                         if args.expected_exit else
                         "solcodex.verifier-prehook-boundary-development.v1"),
              "harness_declared_codex_cli": "0.155.0-alpha.16.3",
              "harness_declared_code_mode": True,
              "host_run_version_independently_verified": False,
              "installed_solcodex_plugin": False,
              "provider_billing_complete": False,
              "full_shell_semantics_qualified": False,
              "repair_quality_measured": False,
              "runs": {"direct": direct, "prehook": prehook}}
    if args.expected_exit:
        report["nonzero_status_preserved_in_cli_result"] = True
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
