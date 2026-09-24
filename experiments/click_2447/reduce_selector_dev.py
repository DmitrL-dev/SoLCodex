#!/usr/bin/env python3
"""Reduce private Click selector runs to a path-free development aggregate.

Run as ``python3 -m experiments.click_2447.reduce_selector_dev`` from the
repository root. Raw traces, request bodies, source overlays, and local paths
are read only for verification and are never included in the output.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

from experiments.packaging_928.reduce_prehook_repair_dev import validate_request_accounting


SOURCE_PATH = "src/click/core.py"
VERIFIER_SHA256 = "12e844a5a801e6d1cc233c893472b5ac1a24d59beb1d313be2e91bc132b1b111"
FROZEN_PROTOCOL_SHA256 = "2d169ef2d4bb923e74d404881756bf85b2381305f1df7b2ebbd13878a5e0e576"
ADAPTERS = {
    "old": ("507922341cb8cda56cd07d6b50983cbdb2b4265a",
            "067ee9df409b84f52da3646df69c693253d9de2e46c8d9da0ed9df3507f80f1d"),
    "new": ("254ac778de8f2b35d65d1b4822f5935ec7f81024",
            "2c472a4dc54149aa4335c077191a0214bfeff55b53ae4dd03cf972492a16fbf8"),
}
SHARED_DESIGN = (
    "fixture_tree", "fixture_files_sha256", "external_verifier_sha256",
    "prompt_template_sha256", "search_sha256", "pilot_sha256",
    "broker_sha256", "audit_sha256", "evaluate_sha256", "safe_tree_sha256",
)
CASES = (
    "direct_exception", "scope_exception", "nested_inner_suppresses",
    "nested_outer_suppresses", "nested_unsuppressed", "cli_transaction_rollback",
    "successful_exit", "explicit_close", "context_reuse",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(root: Path, name: str) -> tuple[dict, str]:
    raw = (root / name).read_bytes()
    return json.loads(raw), digest(raw)


def ast_digest(data: bytes) -> str:
    return digest(ast.dump(ast.parse(data.decode("utf-8")), include_attributes=False).encode())


def read_case_results(path: Path) -> tuple[dict[str, bool], str]:
    raw = path.read_bytes()
    lines = raw.decode("utf-8").splitlines()
    if len(lines) != len(CASES) + 1:
        raise ValueError("external report has unexpected length")
    outcomes: dict[str, bool] = {}
    for case, line in zip(CASES, lines[:-1]):
        match = re.fullmatch(re.escape(case) + r": (PASS|FAIL \([A-Za-z]+\))", line)
        if match is None:
            raise ValueError("external report has unexpected case")
        outcomes[case] = match.group(1) == "PASS"
    if lines[-1] != f"TOTAL {sum(outcomes.values())}/{len(CASES)}":
        raise ValueError("external total differs from cases")
    return outcomes, digest(raw)


def complete_upstream_result(summary: str) -> bool:
    return re.fullmatch(r"1283 passed, 22 skipped, 1 xfailed in \d+\.\d+s", summary) is not None


def one(mode: str, host: Path) -> tuple[dict, dict]:
    design, design_sha = load(host, "design.json")
    process, process_sha = load(host, "process.json")
    proxy, proxy_sha = load(host, "proxy-summary.json")
    audit, audit_sha = load(host, "adherence.json")
    delta, delta_sha = load(host, "delta.json")
    evaluation, evaluation_sha = load(host, "evaluation-summary.json")
    trace_bytes = (host / "trace.jsonl").read_bytes()
    if design["mode"] != mode or design["smoke"] is not False:
        raise ValueError("wrong arm design")
    if (design["external_verifier_sha256"] != VERIFIER_SHA256 or
            design["old_adapter_revision"] != ADAPTERS["old"][0] or
            design["new_adapter_revision"] != ADAPTERS["new"][0] or
            design["adapter_sha256"] != ADAPTERS[mode][1]):
        raise ValueError("adapter or verifier does not match pinned design")
    if (audit["mode"] != mode or audit["smoke"] is not False or
            audit["qualified"] is not True or not all(audit["checks"].values()) or
            process["timed_out"] is not False or process["exit_code"] != 0 or
            process["cleanup"]["verified"] is not True):
        raise ValueError("run did not pass prospective adherence")
    if (proxy["pending_connections"] != 0 or
            proxy["accepted_connections"] != proxy["finished_connections"]):
        raise ValueError("broker has unfinished accepted connections")
    journal = validate_request_accounting(proxy)
    if (audit["journal_attempts"] != journal["attempts"] or
            audit["observed_usage"] != journal["observed_completed_usage"]):
        raise ValueError("audit and broker usage differ")
    overlay = (host / "source-overlay" / SOURCE_PATH).read_bytes()
    if (delta["valid"] is not True or delta["changed_paths"] != [SOURCE_PATH] or
            delta["changed_source_sha256"] != {SOURCE_PATH: digest(overlay)} or
            delta["baseline_sha256"] != design["fixture_files_sha256"]):
        raise ValueError("source delta invalid")
    if (evaluation["mode"] != mode or evaluation["delta_sha256"] != delta_sha or
            evaluation["changed_paths"] != [SOURCE_PATH] or
            evaluation["applies"] is not True or evaluation["diff_check_exit"] != 0 or
            evaluation["source_unchanged_after_external"] is not True or
            evaluation["source_unchanged_after_upstream"] is not True or
            evaluation["upstream_exit"] != 0 or
            not complete_upstream_result(evaluation["upstream_result"])):
        raise ValueError("clean-copy evaluation incomplete")
    cases, cases_sha = read_case_results(
        Path(evaluation["evaluation_root"]) / "artifacts/external.stdout")
    score = sum(cases.values())
    if (evaluation["external_score"] != f"TOTAL {score}/9" or
            (evaluation["external_exit"] == 0) != (score == 9)):
        raise ValueError("external score or exit disagrees with case report")
    events = [json.loads(line) for line in trace_bytes.splitlines() if line.strip()]
    first = next((event["item"] for event in events
                  if event.get("type") == "item.completed" and
                  event.get("item", {}).get("type") == "command_execution"), None)
    if first is None:
        raise ValueError("missing first command")
    receipt = json.loads(first["aggregated_output"])
    if (receipt["schema"] != "solcodex.command-receipt.v1" or
            receipt["status"] != "completed" or receipt["capture_complete"] is not True or
            receipt["exit_code"] != 1 or receipt["wrapper_exit_code"] != 1 or
            receipt["bytes"] != audit["captured_bytes"] or
            receipt["sha256"] != audit["captured_sha256"] or
            len(first["aggregated_output"].encode()) != audit["first_visible_output_bytes"] or
            [item["line"] for item in receipt["diagnostics"]] != audit["first_diagnostic_lines"]):
        raise ValueError("first receipt differs from qualified audit")
    usage = journal["observed_completed_usage"]
    result = {
        "qualified": True,
        "first_diagnostic_lines": audit["first_diagnostic_lines"],
        "first_model_visible_result_bytes": audit["first_visible_output_bytes"],
        "first_saved_output_bytes": audit["captured_bytes"],
        "first_saved_output_sha256": audit["captured_sha256"],
        "diagnostic_executions": audit["diagnostic_count"],
        "hook_exact_matches": audit["hook_exact_matches"],
        "command_executions": audit["command_count"],
        "completed_requests": journal["attempts"],
        "agent_elapsed_seconds": process["agent_elapsed_seconds"],
        "provider_observed_usage": {
            "input_tokens": usage["input_tokens"],
            "cached_input_tokens": usage["cached_input_tokens"],
            "uncached_input_tokens": usage["input_tokens"] - usage["cached_input_tokens"],
            "output_tokens": usage["output_tokens"],
            "input_plus_output_tokens": usage["input_tokens"] + usage["output_tokens"],
        },
        "changed_paths": delta["changed_paths"],
        "changed_source_sha256": digest(overlay),
        "changed_source_ast_sha256": ast_digest(overlay),
        "external_checks_passed": score,
        "external_checks_total": len(CASES),
        "external_case_results": cases,
        "accepted": score == len(CASES),
        "upstream_result": evaluation["upstream_result"],
        "evidence_sha256": {
            "design": design_sha, "process": process_sha, "proxy_summary": proxy_sha,
            "audit": audit_sha, "delta": delta_sha, "evaluation": evaluation_sha,
            "trace": digest(trace_bytes), "external_stdout": cases_sha,
        },
    }
    return result, design


def reduce(old_host: Path, new_host: Path, protocol: Path) -> dict:
    frozen_raw = protocol.read_bytes()
    if digest(frozen_raw) != FROZEN_PROTOCOL_SHA256:
        raise ValueError("protocol differs from published pre-repair freeze")
    frozen = json.loads(frozen_raw)
    old, old_design = one("old", old_host)
    new, new_design = one("new", new_host)
    if any(old_design[key] != new_design[key] for key in SHARED_DESIGN):
        raise ValueError("arms used different shared design")
    if frozen["arm_order"] != ["old", "new"] or frozen["design_status"].startswith("pre-repair"):
        raise ValueError("missing frozen pre-repair protocol")
    for design, host in ((old_design, old_host), (new_design, new_host)):
        if digest((host.parent / "work/tests/test_context.py").read_bytes()) != \
                frozen["hashes"]["fixture/tests/test_context.py"]:
            raise ValueError("visible fixture differs from frozen protocol")
        for key, filename in (("pilot_sha256", "pilot.py"), ("broker_sha256", "broker_route.py"),
                              ("audit_sha256", "audit.py"), ("evaluate_sha256", "evaluate.py"),
                              ("safe_tree_sha256", "safe_tree.py")):
            if design[key] != frozen["hashes"][filename]:
                raise ValueError("run code differs from protocol")
    if (frozen["old_adapter_sha256"] != ADAPTERS["old"][1] or
            frozen["new_adapter_sha256"] != ADAPTERS["new"][1]):
        raise ValueError("protocol adapter does not match frozen revision")
    a = old["provider_observed_usage"]
    b = new["provider_observed_usage"]
    return {
        "schema": "solcodex.click-selector-repair-development.v1",
        "task": "exposed Click #2447",
        "parent_revision": "16fe802a3f96c4c8fa3cd382f1a7577fda0c5321",
        "historical_fix_revision": "36deba8a95a2585de1a2aa4475b7f054f52830ac",
        "declared_model": "gpt-6-luna",
        "declared_reasoning_effort": "low",
        "run_order": ["old", "new"],
        "installed_solcodex_plugin": False,
        "temporary_prehook_plugin": True,
        "independent_task": False,
        "confirmatory_pair": False,
        "provider_billing_complete": False,
        "parent_external_checks": "3/9",
        "historical_fix_external_checks": "9/9",
        "protocol_sha256": digest(frozen_raw),
        "shared_design_sha256": {key: old_design[key] for key in SHARED_DESIGN},
        "adapter_revisions": {mode: ADAPTERS[mode][0] for mode in ADAPTERS},
        "runs": {"old": old, "new": new},
        "new_minus_old": {
            "input_plus_output_tokens": b["input_plus_output_tokens"] - a["input_plus_output_tokens"],
            "cached_input_tokens": b["cached_input_tokens"] - a["cached_input_tokens"],
            "uncached_input_tokens": b["uncached_input_tokens"] - a["uncached_input_tokens"],
            "output_tokens": b["output_tokens"] - a["output_tokens"],
            "agent_elapsed_seconds": round(new["agent_elapsed_seconds"] - old["agent_elapsed_seconds"], 3),
            "command_executions": new["command_executions"] - old["command_executions"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_host_artifacts", type=Path)
    parser.add_argument("new_host_artifacts", type=Path)
    parser.add_argument("pre_repair_protocol", type=Path)
    args = parser.parse_args()
    try:
        result = reduce(args.old_host_artifacts, args.new_host_artifacts,
                        args.pre_repair_protocol)
    except (KeyError, TypeError, ValueError, OSError, SyntaxError, StopIteration, json.JSONDecodeError):
        print("invalid private inputs", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
