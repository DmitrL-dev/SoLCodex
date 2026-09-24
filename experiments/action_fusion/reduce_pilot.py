#!/usr/bin/env python3
"""Reduce the private action-fusion pair to a fixed-field public aggregate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from experiments.action_fusion.external_verify import CASES


PROTOCOL_SHA256 = "28c253dc5e186a8e58278ee317602fc3eb4d66de6a436f19a0309d8237f26a6e"
FROZEN_COMMIT = "31b518d2c58675700230e98bbaab4a79b331d58b"
VERIFIER_COMMAND = "python3 -m unittest discover -s tests -v"
CONTROLS = frozenset({
    "artifact_write_denied", "fork_denied", "host_read_denied",
    "loopback_connection_denied_with_host_control", "posix_spawn_denied",
    "workspace_readable",
})


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load(root: Path, name: str) -> tuple[dict, str]:
    raw = (root / name).read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object: " + name)
    return value, sha(raw)


def check_usage(proxy: dict) -> dict:
    requests = proxy["sink_requests"]
    journal = proxy["journal"]
    if (not isinstance(requests, list) or not requests or
            journal["attempts"] != len(requests) or
            journal["states"] != {"pending": 0, "completed": len(requests), "unknown": 0} or
            journal["all_attempts_have_observed_usage"] is not True or
            journal["provider_billing_complete"] is not False or
            proxy["auth_file_in_home"] is not False or
            proxy["broker_injected"] != len(requests) or
            proxy["rejected_client_auth"] != 0 or proxy["handler_errors"] != 0 or
            proxy["all_proxy_handlers_done"] is not True or
            proxy["listener_stopped"] is not True or
            proxy["accepted_connections"] != proxy["finished_connections"]):
        raise ValueError("proxy or journal completeness failed")
    totals = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
    attempts = set()
    responses = set()
    for request in requests:
        if (request.get("method") != "POST" or
                request.get("path") != "/backend-api/codex/responses" or
                request.get("upstream_status") != 200 or
                request.get("done") is not True or
                request.get("client_disconnected") is not False or
                request.get("upstream_error") is not None or
                len(request.get("completions", [])) != 1):
            raise ValueError("incomplete proxy request")
        attempt = request.get("attempt_id")
        completion = request["completions"][0]
        response = completion.get("response_id_sha256")
        if (not isinstance(attempt, str) or not attempt or attempt in attempts or
                completion.get("id_present") is not True or
                not isinstance(response, str) or re.fullmatch(r"[0-9a-f]{64}", response) is None or
                response in responses):
            raise ValueError("duplicate or unidentified response")
        attempts.add(attempt)
        responses.add(response)
        values = (completion.get("input_tokens"), completion.get("output_tokens"),
                  completion.get("cached_tokens"))
        if (any(type(value) is not int or value < 0 for value in values) or
                values[2] > values[0]):
            raise ValueError("invalid completion usage")
        for key, value in zip(totals, values):
            totals[key] += value
    if totals != journal["observed_completed_usage"]:
        raise ValueError("proxy completions and journal disagree")
    return {**totals,
            "uncached_input_tokens": totals["input_tokens"] - totals["cached_input_tokens"],
            "input_plus_output_tokens": totals["input_tokens"] + totals["output_tokens"]}


def trace_summary(raw: bytes, usage: dict) -> dict:
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    turns = [event for event in events if event.get("type") == "turn.completed"]
    if len(turns) != 1:
        raise ValueError("expected one completed turn")
    turn_usage = turns[0].get("usage") or {}
    for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
        if turn_usage.get(key) != usage[key]:
            raise ValueError("CLI turn and proxy usage disagree")
    items = [event["item"] for event in events
             if event.get("type") == "item.completed" and isinstance(event.get("item"), dict)]
    commands = [item for item in items if item.get("type") == "command_execution"]
    changes = [item for item in items if item.get("type") == "file_change"]
    verifiers = [item for item in commands if VERIFIER_COMMAND in item.get("command", "")]
    combined = [item for item in verifiers if "write_text" in item.get("command", "")]
    failed_edit_then_test = [item for item in combined if
                             "can't create temp file for here document" in
                             item.get("aggregated_output", "") and
                             "test_existing_integer_seconds" in
                             item.get("aggregated_output", "")]
    return {
        "completed_command_items": len(commands),
        "completed_file_change_items": len(changes),
        "public_verifier_command_exits": [item.get("exit_code") for item in verifiers],
        "combined_shell_edit_test_attempts": len(combined),
        "combined_attempt_edit_failed_but_test_ran": len(failed_edit_then_test),
        "separately_permissioned_fusion_attested": False,
    }


def arm(mode: str, root: Path, protocol: dict, published_candidate: Path) -> dict:
    host = root / "host-artifacts"
    design, design_hash = load(host, "design.json")
    process, process_hash = load(host, "process.json")
    proxy, proxy_hash = load(host, "proxy-summary.json")
    evaluation, evaluation_hash = load(host, "evaluation.json")
    trace_raw = (host / "trace.jsonl").read_bytes()
    candidate = (host / "candidate/duration.py").read_bytes()
    if (design.get("mode") != mode or
            design.get("protocol_sha256") != PROTOCOL_SHA256 or
            design.get("fixture_sha256") != protocol["fixture_tree_sha256"] or
            design.get("external_verifier_sha256") != protocol["external_verifier_sha256"] or
            design.get("base_prompt_sha256") != protocol["base_prompt_sha256"] or
            design.get("on_instruction_sha256") != protocol["on_instruction_sha256"] or
            design.get("runner_sha256") != protocol["private_runner_sha256"] or
            design.get("dependency_sha256") != protocol["private_dependency_sha256"] or
            design.get("access_canaries") != {"workspace": True, "reference": False,
                                               "host_artifact": False, "host_auth": False}):
        raise ValueError("arm design differs from frozen protocol")
    expected_prompt = (Path(__file__).with_name("base_prompt.txt").read_text().strip()
                       if mode == "off" else
                       Path(__file__).with_name("on_instruction.txt").read_text().strip() +
                       "\n\n" + Path(__file__).with_name("base_prompt.txt").read_text().strip())
    if design.get("prompt_sha256") != sha(expected_prompt.encode()):
        raise ValueError("arm prompt differs from assignment")
    if (process.get("exit_code") != 0 or process.get("timed_out") is not False or
            process.get("source_only_qualified") is not True or
            process.get("cleanup", {}).get("verified") is not True or
            not isinstance(process.get("elapsed_seconds"), (int, float)) or
            not 0 < process["elapsed_seconds"] <= protocol["per_arm_timeout_seconds"] or
            process.get("source_sha256") != sha(candidate) or
            process.get("source_bytes") != len(candidate) or
            published_candidate.read_bytes() != candidate):
        raise ValueError("source snapshot or agent lifecycle invalid")
    if (evaluation.get("schema") != "solcodex.action-fusion-evaluation.v1" or
            evaluation.get("source_sha256") != sha(candidate) or
            evaluation.get("controls") != dict.fromkeys(CONTROLS, True) or
            evaluation.get("source_unchanged_after") is not True or
            evaluation.get("external_accepted") is not True or
            evaluation.get("public", {}).get("exit_code") != 0 or
            evaluation.get("public", {}).get("cleanup_complete") is not True or
            evaluation.get("invalid_wire_cases") != [] or
            set(evaluation.get("external_case_results", {})) != {name for name, _, _ in CASES} or
            set(evaluation.get("external_case_evidence", {})) != {name for name, _, _ in CASES} or
            any(value is not True for value in evaluation["external_case_results"].values()) or
            any(item.get("cleanup_complete") is not True or
                item.get("timed_out") is not False or item.get("exit_code") != 0
                for item in evaluation.get("external_case_evidence", {}).values())):
        raise ValueError("external evaluation invalid")
    usage = check_usage(proxy)
    trace = trace_summary(trace_raw, usage)
    return {
        "accepted": True,
        "source_sha256": sha(candidate),
        "external_cases_passed": sum(evaluation["external_case_results"].values()),
        "external_cases_total": len(CASES),
        "model_requests": proxy["journal"]["attempts"],
        "proxy_observed_usage": usage,
        "agent_elapsed_seconds": process["elapsed_seconds"],
        "trace_mechanism": trace,
        "provider_billing_complete": False,
        "evidence_sha256": {"design": design_hash, "process": process_hash,
                            "proxy_summary": proxy_hash, "evaluation": evaluation_hash,
                            "trace": sha(trace_raw), "candidate": sha(candidate)},
    }


def reduce(off_root: Path, on_root: Path) -> dict:
    protocol_raw = Path(__file__).with_name("protocol.json").read_bytes()
    if sha(protocol_raw) != PROTOCOL_SHA256:
        raise ValueError("protocol differs from preregistered commit")
    protocol = json.loads(protocol_raw)
    if protocol["arms_in_order"] != ["off", "on"] or protocol["off_fusion_prohibited"] is not False:
        raise ValueError("unexpected assignment rule")
    result_root = Path(__file__).with_name("results")
    off = arm("off", off_root, protocol, result_root / "off/duration.py")
    on = arm("on", on_root, protocol, result_root / "on/duration.py")
    off_tokens = off["proxy_observed_usage"]["input_plus_output_tokens"]
    on_tokens = on["proxy_observed_usage"]["input_plus_output_tokens"]
    return {
        "schema": "solcodex.action-fusion-mechanism-development.v1",
        "frozen_commit": FROZEN_COMMIT,
        "protocol_sha256": PROTOCOL_SHA256,
        "run_order": ["off", "on"],
        "arms": {"off": off, "on": on},
        "observed_on_minus_off_tokens": on_tokens - off_tokens,
        "observed_on_over_off_token_ratio": round(on_tokens / off_tokens, 6),
        "observed_on_minus_off_seconds": round(
            on["agent_elapsed_seconds"] - off["agent_elapsed_seconds"], 3),
        "one_pair_general_savings_supported": False,
        "installed_hook_tested": False,
        "provider_billing_complete": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("off_root", type=Path)
    parser.add_argument("on_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = reduce(args.off_root, args.on_root)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
