#!/usr/bin/env python3
"""Reduce private target-CLI smoke artifacts to fixed public fields."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

from experiments.action_fusion.external_verify import CASES
from experiments.action_fusion.reduce_pilot import check_usage


HERE = Path(__file__).resolve().parent
BASE = HERE.parent
REPO = BASE.parents[1]
FROZEN_COMMIT = "269e38a6709094b3027ce297c447692f1de5a4a2"
PROTOCOL_SHA256 = "c03ceec6bd4d173c403a3011a2abbc3ce02f401f9a5fd19d9b10d81e20217055"
VERIFIER = "python3 -m unittest discover -s tests -v"
CONTROLS = {
    "workspace_readable": True,
    "host_read_denied": True,
    "artifact_write_denied": True,
    "fork_denied": True,
    "posix_spawn_denied": True,
    "loopback_connection_denied_with_host_control": True,
}
CANARIES = {"workspace": True, "reference": False,
            "host_artifact": False, "host_auth": False}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load(host: Path, name: str) -> tuple[dict, str]:
    raw = (host / name).read_bytes()
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("expected object: " + name)
    return obj, sha(raw)


def arm(mode: str, root: Path, protocol: dict, candidate: bytes) -> dict:
    host = root / "host-artifacts"
    design, design_hash = load(host, "design.json")
    process, process_hash = load(host, "process.json")
    proxy, proxy_hash = load(host, "proxy-summary.json")
    response, response_hash = load(host, "response-items.json")
    trace_raw = (host / "trace.jsonl").read_bytes()
    source = (host / "candidate/duration.py").read_bytes()
    prompt = (HERE / (mode + "_prompt.txt")).read_bytes()
    if (design.get("mode") != mode or
            design.get("protocol_sha256") != sha((HERE / "protocol.json").read_bytes()) or
            design.get("prompt_sha256") != protocol[mode + "_prompt_sha256"] or
            design.get("effective_prompt_sha256") != sha(prompt.decode().strip().encode()) or
            design.get("fixture_tree_sha256") != protocol["fixture_tree_sha256"] or
            design.get("dependency_sha256") != protocol["private_dependency_sha256"] or
            design.get("access_canaries") != CANARIES):
        raise ValueError(mode + " design differs from frozen protocol")
    expected_source = (protocol["preflight_gold_sha256"] if mode == "valid"
                       else protocol["parent_source_sha256"])
    if ((mode == "valid" and source != candidate) or
            sha(source) != expected_source or
            process.get("source_sha256") != expected_source or
            process.get("source_bytes") != len(source) or
            process.get("source_shape_qualified") is not True or
            process.get("exit_code") != 0 or process.get("timed_out") is not False or
            process.get("cleanup", {}).get("verified") is not True or
            not 0 < process.get("elapsed_seconds", 0) <= protocol["per_arm_timeout_seconds"]):
        raise ValueError(mode + " source or process qualification failed")
    usage = check_usage(proxy)
    if response.get("instrumentation_errors") != 0:
        raise ValueError(mode + " observer incomplete")
    events = response.get("events")
    if not isinstance(events, list):
        raise ValueError(mode + " observer events missing")
    attempts = {request["attempt_id"] for request in proxy["sink_requests"]}
    if any(event.get("attempt_id") not in attempts for event in events):
        raise ValueError(mode + " observer attempt linkage missing")
    done = [event for event in events if event.get("event_type") == "response.output_item.done"]
    outer = [event for event in done if event.get("item_type") == "custom_tool_call"]
    if len(outer) != 1:
        raise ValueError(mode + " expected one outer tool call")
    outer_id = outer[0]["call_id_sha256"]
    nested = [event for event in done if outer_id and
              event.get("caller_id_sha256") == outer_id and
              event.get("attempt_id") == outer[0].get("attempt_id")]
    names = [event.get("tool_name") for event in nested]
    expected_names = (["apply_patch", "exec_command"] if mode == "valid"
                      else ["apply_patch"])
    caller_linked = (bool(outer_id) and outer[0].get("tool_name") == "functions.exec" and
                     len(nested) == len(expected_names) and names == expected_names and
                     (mode == "valid" or nested[0].get("status") == "failed"))
    events_raw = [json.loads(line) for line in trace_raw.splitlines() if line.strip()]
    turns = [event for event in events_raw if event.get("type") == "turn.completed"]
    if len(turns) != 1 or any(turns[0].get("usage", {}).get(key) != usage[key]
                              for key in ("input_tokens", "output_tokens", "cached_input_tokens")):
        raise ValueError(mode + " CLI and proxy usage disagree")
    activity = [(event.get("type"), event["item"]) for event in events_raw
                if event.get("type") in {"item.started", "item.completed"} and
                isinstance(event.get("item"), dict) and
                event["item"].get("type") in {"file_change", "command_execution"}]
    changes = [item for kind, item in activity
               if kind == "item.completed" and item["type"] == "file_change"]
    commands = [item for kind, item in activity
                if kind == "item.completed" and item["type"] == "command_execution"]
    stderr_raw = (host / "stderr.txt").read_bytes()
    if mode == "valid":
        if ([kind + ":" + item["type"] for kind, item in activity] !=
                ["item.started:file_change", "item.completed:file_change",
                 "item.started:command_execution", "item.completed:command_execution"] or
                activity[0][1].get("id") != activity[1][1].get("id") or
                activity[2][1].get("id") != activity[3][1].get("id") or
                len(changes) != 1 or changes[0].get("status") != "completed" or
                len(changes[0].get("changes", [])) != 1 or
                len(commands) != 1 or commands[0].get("exit_code") != 0 or
                shlex.split(commands[0].get("command", "")) != ["/bin/zsh", "-lc", VERIFIER]):
            raise ValueError("valid trace did not show patch then narrow verifier")
        evaluation, evaluation_hash = load(host, "external-eval.json")
        if (evaluation.get("source_sha256") != expected_source or
                evaluation.get("controls") != CONTROLS or
                evaluation.get("source_unchanged_after") is not True or
                evaluation.get("public", {}).get("exit_code") != 0 or
                evaluation.get("external_accepted") is not True or
                evaluation.get("invalid_wire_cases") != [] or
                set(evaluation.get("external_case_results", {})) != {case for case, _, _ in CASES} or
                set(evaluation.get("external_case_evidence", {})) != {case for case, _, _ in CASES} or
                any(value is not True for value in evaluation["external_case_results"].values()) or
                any(item.get("cleanup_complete") is not True or
                    item.get("timed_out") is not False or item.get("exit_code") != 0
                    for item in evaluation["external_case_evidence"].values())):
            raise ValueError("valid external evaluation failed")
    else:
        if activity or b"apply_patch verification failed" not in stderr_raw:
            raise ValueError("negative control reached edit or verifier item")
        evaluation_hash = None
    return {
        "source_sha256": expected_source,
        "source_bytes": len(source),
        "elapsed_seconds": process["elapsed_seconds"],
        "model_requests": proxy["journal"]["attempts"],
        "proxy_observed_usage": usage,
        "completed_file_change_items": len(changes),
        "completed_command_items": len(commands),
        "outer_tool_calls": len(outer),
        "nested_caller_linked": caller_linked,
        "observer_errors": response["instrumentation_errors"],
        "public_verifier_exit": commands[0]["exit_code"] if commands else None,
        "external_passed": len(CASES) if mode == "valid" else None,
        "external_total": len(CASES) if mode == "valid" else None,
        "artifact_sha256": {"design": design_hash, "process": process_hash,
                            "proxy_summary": proxy_hash, "response_items": response_hash,
                            "trace": sha(trace_raw), "stderr": sha(stderr_raw),
                            "external_evaluation": evaluation_hash},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("valid_root", type=Path)
    parser.add_argument("invalid_root", type=Path)
    args = parser.parse_args()
    protocol_raw = (HERE / "protocol.json").read_bytes()
    frozen_raw = subprocess.check_output(
        ["git", "show", FROZEN_COMMIT + ":experiments/action_fusion/nested_smoke/protocol.json"],
        cwd=REPO, timeout=10)
    if protocol_raw != frozen_raw or sha(protocol_raw) != PROTOCOL_SHA256:
        raise ValueError("protocol differs from frozen commit")
    protocol = json.loads(protocol_raw)
    for mode in ("valid", "invalid"):
        prompt = (HERE / (mode + "_prompt.txt")).read_bytes()
        if sha(prompt) != protocol[mode + "_prompt_sha256"]:
            raise ValueError("prompt differs from frozen protocol: " + mode)
    for filename, key in (("external_verify.py", "external_verifier_sha256"),
                          ("invoke_candidate.py", "candidate_invoker_sha256")):
        if sha((BASE / filename).read_bytes()) != protocol[key]:
            raise ValueError("evaluator differs from frozen protocol: " + filename)
    fixture = BASE / "fixture"
    rows = [(path.relative_to(fixture).as_posix(), sha(path.read_bytes()))
            for path in sorted(fixture.rglob("*")) if path.is_file()]
    if sha(json.dumps(rows, separators=(",", ":")).encode()) != protocol["fixture_tree_sha256"]:
        raise ValueError("fixture differs from frozen protocol")
    candidate = (HERE / "results/valid/duration.py").read_bytes()
    valid = arm("valid", args.valid_root, protocol, candidate)
    invalid = arm("invalid", args.invalid_root, protocol, candidate)
    report = {
        "schema": "solcodex.nested-tool-cli-smoke-result.v1",
        "frozen_protocol_commit": FROZEN_COMMIT,
        "protocol_sha256": sha(protocol_raw),
        "model": protocol["model"],
        "codex_cli": protocol["codex_cli"],
        "arms": {"valid": valid, "invalid": invalid},
        "tool_sequence": "VERIFIED",
        "failed_patch_stops_verifier": "VERIFIED",
        "one_program_nested_attribution": ("VERIFIED" if valid["nested_caller_linked"] and
                                           invalid["nested_caller_linked"] else "INCONCLUSIVE"),
        "separate_approval_decisions": "NOT TESTED",
        "efficiency_effect": "NOT ESTIMATED",
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
