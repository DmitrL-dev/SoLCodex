"""Reduce private packaged-plugin workspace-write probes to fixed-field evidence.

No prompt, output, marker, credential, or local path is emitted. This audits
observations and harness declarations, not independent provider billing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex

try:
    from scripts.audit_prehook_request_probe import tokens
except ModuleNotFoundError:
    from audit_prehook_request_probe import tokens


MARKER = re.compile(rb"HIDDEN_[0-9a-f]{24}")
VERSION = "0.1.9+codex.20260924"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def command_payload(raw: str) -> str:
    if not isinstance(raw, str):
        raise ValueError("CLI command missing")
    parts = shlex.split(raw)
    if len(parts) != 3 or parts[:2] != ["/bin/zsh", "-lc"]:
        raise ValueError("unexpected shell wrapper")
    return parts[2]


def required_pair(argv: list[str], option: str, value: str) -> bool:
    return sum(argv[index:index + 2] == [option, value]
               for index in range(len(argv) - 1)) == 1


def feature_mode(argv: list[str], name: str, enabled: bool) -> bool:
    values = argv[:-1]
    on = sum(values[index:index + 2] == ["--enable", name]
             for index in range(len(values) - 1))
    off = sum(values[index:index + 2] == ["--disable", name]
              for index in range(len(values) - 1))
    return (on, off) == ((1, 0) if enabled else (0, 1))


def inspect(root: Path, plugin: bool) -> dict:
    root = Path(root)
    artifacts = root / "artifacts"
    fixture = (root / "work/test_probe.py").read_bytes()
    matches = MARKER.findall(fixture)
    if len(matches) != 1:
        raise ValueError("fixture marker missing or duplicated")
    marker = matches[0]
    template_hash = digest(fixture.replace(marker, b"<MARKER>"))
    if (root / "work/run-count.log").read_text().splitlines() != ["run"]:
        raise ValueError("verifier child did not run exactly once")
    if (root / "home/auth.json").exists():
        raise ValueError("private authentication copy remains")

    manifest_bytes = (artifacts / "harness-manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    argv = manifest.get("argv")
    if (not isinstance(argv, list) or any(not isinstance(item, str) for item in argv)
            or len(argv) < 20 or manifest.get("plugin_enabled") is not plugin
            or argv[0] != "/Applications/ChatGPT.app/Contents/Resources/codex"
            or argv[1:4] != ["exec", "--json", "--ephemeral"]
            or not required_pair(argv, "-s", "workspace-write")
            or not required_pair(argv, "-m", "gpt-6-sol")
            or not required_pair(argv, "-c", "model_reasoning_effort=medium")
            or not feature_mode(argv, "code_mode", True)
            or not feature_mode(argv, "hooks", plugin)
            or not feature_mode(argv, "plugins", plugin)
            or ("--dangerously-bypass-hook-trust" in argv) != plugin
            or "--dangerously-bypass-approvals-and-sandbox" in argv
            or not isinstance(argv[-1], str)
            or not isinstance(manifest.get("driver_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", manifest["driver_sha256"])):
        raise ValueError("harness command is outside workspace-write comparison")
    prompt = argv[-1]
    if marker.decode() in prompt:
        raise ValueError("marker appeared in prompt")
    if plugin:
        if manifest.get("plugin_version") != VERSION:
            raise ValueError("wrong packaged plugin version")
        package = root / "market/plugins/sol-codex/.codex-plugin/plugin.json"
        cached_package = (root / "home/plugins/cache/sol-codex/sol-codex" /
                          VERSION / ".codex-plugin/plugin.json")
        if (package.read_bytes() != cached_package.read_bytes() or
                json.loads(package.read_text()).get("version") != VERSION):
            raise ValueError("packaged plugin identity differs from manifest")
        for relative, field in (
            ("scripts/sol_hook.py", "plugin_hook_sha256"),
            ("hooks/hooks.json", "plugin_config_sha256"),
        ):
            source = (root / "market/plugins/sol-codex" / relative).read_bytes()
            cached = (root / "home/plugins/cache/sol-codex/sol-codex" /
                      VERSION / relative).read_bytes()
            if digest(source) != digest(cached) or manifest.get(field) != digest(source):
                raise ValueError("plugin source/cache/manifest mismatch")
        snapshot = (root / "home/plugins/data/sol-codex-sol-codex/runtime-v1/snapshots" /
                    f"{manifest['plugin_hook_sha256']}.py")
        if digest(snapshot.read_bytes()) != manifest["plugin_hook_sha256"]:
            raise ValueError("pinned runtime snapshot differs from packaged source")
    elif (manifest.get("plugin_version") is not None or
          manifest.get("plugin_hook_sha256") is not None or
          manifest.get("plugin_config_sha256") is not None):
        raise ValueError("control unexpectedly declares a plugin")

    summary_bytes = (artifacts / "proxy-summary.json").read_bytes()
    summary = json.loads(summary_bytes)
    requests = summary.get("sink_requests")
    if (summary.get("exit") != 0 or not isinstance(requests, list) or
            len(requests) != 2 or type(summary.get("proxy_blocked")) is not int or
            summary["proxy_blocked"] < 0):
        raise ValueError("CLI or proxy observation incomplete")
    for request in requests:
        if (request.get("upstream_status") != 200 or request.get("done") is not True or
                request.get("client_disconnected") is not False or
                request.get("upstream_error") is not None or
                type(request.get("body_bytes")) is not int or
                request["body_bytes"] <= len(marker) or
                type(request.get("request_has_probe_marker")) is not bool or
                not isinstance(request.get("completions"), list) or
                len(request["completions"]) != 1):
            raise ValueError("model request incomplete")
        tokens(request)
    flags = [request["request_has_probe_marker"] for request in requests]
    if flags != [False, True]:
        raise ValueError("next-request marker control failed")
    usage = {"input_tokens": sum(tokens(item)["input_tokens"] for item in requests),
             "output_tokens": sum(tokens(item)["output_tokens"] for item in requests),
             "cached_input_tokens": sum(tokens(item)["cached_tokens"] for item in requests)}
    journal = summary.get("journal") or {}
    if (journal.get("attempts") != 2 or
            journal.get("states") != {"pending": 0, "completed": 2, "unknown": 0} or
            journal.get("all_attempts_have_observed_usage") is not True or
            journal.get("observed_completed_usage") != usage or
            journal.get("provider_billing_complete") is not False):
        raise ValueError("request journal or usage incomplete")

    trace_bytes = (artifacts / "trace.jsonl").read_bytes()
    events = [json.loads(line) for line in trace_bytes.splitlines() if line.strip()]
    if sum(event.get("type") == "turn.completed" for event in events) != 1:
        raise ValueError("CLI turn did not complete once")
    items = [event.get("item", {}) for event in events if event.get("type") == "item.completed"]
    commands = [item for item in items if item.get("type") == "command_execution"]
    answers = [item.get("text") for item in items if item.get("type") == "agent_message"]
    if (len(commands) != 1 or commands[0].get("exit_code") != 0 or not answers or
            any(not isinstance(answer, str) for answer in answers) or
            answers[-1].strip() != marker.decode()):
        raise ValueError("verifier command or final marker control failed")
    raw = commands[0].get("aggregated_output")
    if not isinstance(raw, str) or marker.decode() not in raw:
        raise ValueError("tool result did not contain marker")
    output_bytes = len(raw.encode())
    if output_bytes < 8000 or b"A" * 8000 not in raw.encode():
        raise ValueError("large verifier output missing")
    payload = command_payload(commands[0].get("command"))
    original = payload.split("\n", 1)[0]
    arguments = shlex.split(original)
    if (len(arguments) != 5 or not Path(arguments[0]).is_absolute() or
            arguments[1:] != ["-m", "unittest", "-v", "test_probe"] or
            original not in prompt):
        raise ValueError("verifier command differs from prompt")
    if plugin:
        lines = payload.splitlines()
        prefix = "printf '%s\\n' \"$_sol_codex_exit\" > "
        if (len(lines) != 4 or lines[0] != original or
                lines[1] != "_sol_codex_exit=$?" or
                not lines[2].startswith(prefix) or
                lines[3] != 'exit "$_sol_codex_exit"'):
            raise ValueError("packaged hook rewrite was not observed")
        status_parts = shlex.split(lines[2][len(prefix):])
        status_path = Path(status_parts[0]) if len(status_parts) == 1 else Path(".")
        if (not status_path.is_absolute() or
                not status_path.resolve().is_relative_to((root / "tmp").resolve()) or
                len(status_path.relative_to(root / "tmp").parts) != 3 or
                not re.fullmatch(r"sol-codex-verifier-status-[0-9]+-[0-9a-f]{16}",
                                 status_path.relative_to(root / "tmp").parts[0]) or
                not re.fullmatch(r"[0-9a-f]{24}",
                                 status_path.relative_to(root / "tmp").parts[1]) or
                not re.fullmatch(r"[0-9a-f]{32}\.status", status_path.name)):
            raise ValueError("status sidecar path is outside the private run")
        state_paths = list((root / "home/plugins/data/sol-codex-sol-codex/state").glob("*.json"))
        if len(state_paths) != 1:
            raise ValueError("installed hook state missing")
        state = json.loads(state_paths[0].read_text())
        record = state.get("last_verification") or {}
        metrics = state.get("metrics") or {}
        if (record.get("command_sha256") != digest(original.encode()) or
                record.get("exit_code") != 0 or record.get("passed") is not True or
                metrics.get("verification_pass") != 1 or
                metrics.get("packed_observations") != 1 or
                metrics.get("source_bytes") != output_bytes or
                type(metrics.get("receipt_bytes")) is not int or
                type(metrics.get("saved_bytes")) is not int or
                not 0 < metrics["receipt_bytes"] < output_bytes or
                not 0 < metrics["saved_bytes"] < output_bytes or
                metrics["receipt_bytes"] + metrics["saved_bytes"] != output_bytes or
                state.get("pending_verifiers")):
            raise ValueError("hook status or local byte counters disagree")
        local = {key: metrics[key] for key in
                 ("source_bytes", "receipt_bytes", "saved_bytes")}
    else:
        if payload != original:
            raise ValueError("control command was rewritten")
        local = None
    return {"fixture_template_sha256": template_hash,
            "verifier_command_sha256": digest(original.encode()),
            "prompt_sha256": digest(prompt.encode()),
            "driver_sha256": manifest.get("driver_sha256"),
            "plugin_hook_sha256": manifest.get("plugin_hook_sha256"),
            "plugin_config_sha256": manifest.get("plugin_config_sha256"),
            "packaged_hook_rewrite_observed": plugin,
            "child_executions": 1,
            "tool_result_bytes": output_bytes,
            "request_marker_flags": flags,
            "next_request_body_bytes": requests[1]["body_bytes"],
            "next_request_usage": tokens(requests[1]),
            "proxy_observed_total_usage": usage,
            "local_plugin_byte_counters": local,
            "blocked_non_model_connections": summary["proxy_blocked"],
            "harness_manifest_sha256": digest(manifest_bytes),
            "proxy_summary_sha256": digest(summary_bytes),
            "cli_trace_sha256": digest(trace_bytes)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--plugin", type=Path, required=True)
    args = parser.parse_args()
    control = inspect(args.control, False)
    plugin = inspect(args.plugin, True)
    for key in ("fixture_template_sha256", "verifier_command_sha256",
                "prompt_sha256", "driver_sha256"):
        if control[key] != plugin[key]:
            raise ValueError(f"control and packaged plugin differ: {key}")
    print(json.dumps({
        "schema": "solcodex.packaged-plugin-request-development.v1",
        "harness_declared_plugin_version": VERSION,
        "harness_declared_code_mode": True,
        "harness_declared_workspace_write": True,
        "hook_trust_bypass_for_plugin": True,
        "normal_hook_trust_qualified": False,
        "hook_permission_mode_independently_attested": False,
        "host_cli_version_independently_verified": False,
        "provider_billing_complete": False,
        "external_repair_quality_measured": False,
        "runs": {"control": control, "plugin": plugin},
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
