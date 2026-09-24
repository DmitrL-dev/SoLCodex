#!/usr/bin/env python3
"""Reduce two private packaging repair runs to path-free development aggregates.

The host artifact directories are intentionally private. This script reads only
fixed-field reports, overlay source, and hashes of raw evidence. It never emits
agent text, request bodies, authentication, or local run paths.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys


SHARED_DESIGN = (
    "fixture_tree", "fixture_files_sha256", "external_verifier_sha256",
    "prompt_template_sha256", "adapter_sha256", "search_sha256",
    "pilot_sha256", "broker_sha256", "audit_sha256", "evaluate_sha256",
    "safe_tree_sha256",
)
SOURCE_PATH = "src/packaging/licenses/__init__.py"
FROZEN_VERIFIER_SHA256 = "841776fda08a41df4f0c652dda02a0b440d8b08dbaf5e16231520d3dcc1d4324"
HISTORICAL_FIX_SOURCE_SHA256 = "7047733818c0d945335cb73ed49b61f3a141c34c1c65d8d8815ea252c72c2a53"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(root: Path, name: str) -> tuple[dict, str]:
    raw = (root / name).read_bytes()
    return json.loads(raw), digest(raw)


def ast_digest(data: bytes) -> str:
    return digest(ast.dump(ast.parse(data.decode("utf-8")), include_attributes=False).encode())


def one(mode: str, host: Path) -> tuple[dict, dict]:
    design, design_sha = load(host, "design.json")
    process, process_sha = load(host, "process.json")
    proxy, proxy_sha = load(host, "proxy-summary.json")
    original, original_sha = load(host, "adherence-original.json")
    revised, revised_sha = load(host, "adherence.json")
    delta, delta_sha = load(host, "delta.json")
    evaluation, evaluation_sha = load(host, "evaluation-summary.json")
    trace_sha = digest((host / "trace.jsonl").read_bytes())
    overlay = (host / "source-overlay" / SOURCE_PATH).read_bytes()
    if design["mode"] != mode or design["smoke"] is not False:
        raise ValueError("wrong design arm")
    if original["qualified"] is not False or revised["qualified"] is not True:
        raise ValueError("missing original/revised audit sequence")
    if original["checks"]["diagnostic_once"] is not False:
        raise ValueError("original audit failure was not the known repeat rule")
    allowed_original_failures = {"diagnostic_once", "hook_diagnostic_once"}
    failed = {key for key, value in original["checks"].items() if value is False}
    if failed - allowed_original_failures:
        raise ValueError("other original audit failures")
    if (process["timed_out"] or process["exit_code"] != 0 or
            process["cleanup"]["verified"] is not True):
        raise ValueError("worker incomplete")
    journal = proxy["journal"]
    if (not proxy["all_proxy_handlers_done"] or not proxy["listener_stopped"] or
            proxy["active_handlers"] != 0 or proxy["handler_errors"] != 0 or
            proxy["rejected_client_auth"] != 0 or
            journal["states"]["unknown"] != 0 or journal["states"]["pending"] != 0 or
            journal["attempts"] != journal["states"]["completed"]):
        raise ValueError("request accounting incomplete")
    if (not delta["valid"] or delta["changed_paths"] != [SOURCE_PATH] or
            delta["changed_source_sha256"][SOURCE_PATH] != digest(overlay)):
        raise ValueError("source delta invalid")
    if (evaluation["mode"] != mode or evaluation["delta_sha256"] != delta_sha or
            evaluation["changed_paths"] != [SOURCE_PATH] or
            evaluation["applies"] is not True or evaluation["diff_check_exit"] != 0 or
            evaluation["external_exit"] != 0 or evaluation["external_score"] != "TOTAL 14/14" or
            evaluation["upstream_exit"] != 0 or
            not evaluation["upstream_result"].startswith("27294 passed, 1 skipped in ") or
            evaluation["source_unchanged_after_external"] is not True or
            evaluation["source_unchanged_after_upstream"] is not True):
        raise ValueError("external evaluation failed")
    if revised["checks"]["first_command_exact"] is not True or revised["checks"]["first_command_exit"] is not True:
        raise ValueError("first diagnostic was not observed")
    if mode == "receipt" and (revised["checks"]["receipt_complete"] is not True or
                              revised["hook_exact_matches"] != revised["diagnostic_count"]):
        raise ValueError("receipt/hook mismatch")
    usage = journal["observed_completed_usage"]
    input_tokens = usage["input_tokens"]
    output_tokens = usage["output_tokens"]
    cached_tokens = usage["cached_input_tokens"]
    if not all(type(value) is int and value >= 0 for value in
               (input_tokens, output_tokens, cached_tokens)) or cached_tokens > input_tokens:
        raise ValueError("invalid usage counters")
    result = {
        "original_audit_qualified": False,
        "original_failed_checks": sorted(failed),
        "revised_audit_qualified": True,
        "first_diagnostic_exit_code": 1,
        "first_model_visible_result_bytes": revised["first_visible_output_bytes"],
        "diagnostic_executions": revised["diagnostic_count"],
        "command_executions": revised["command_count"],
        "agent_elapsed_seconds": process["agent_elapsed_seconds"],
        "completed_requests": journal["attempts"],
        "provider_observed_usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "uncached_input_tokens": input_tokens - cached_tokens,
            "output_tokens": output_tokens,
            "input_plus_output_tokens": input_tokens + output_tokens,
        },
        "changed_source_sha256": digest(overlay),
        "changed_source_ast_sha256": ast_digest(overlay),
        "external_checks": "14/14",
        "upstream_passed": 27294,
        "upstream_skipped": 1,
        "diff_check_exit": 0,
        "evidence_sha256": {
            "design": design_sha, "process": process_sha, "proxy_summary": proxy_sha,
            "original_audit": original_sha, "revised_audit": revised_sha,
            "delta": delta_sha, "evaluation": evaluation_sha, "trace": trace_sha,
        },
    }
    if mode == "receipt":
        result["first_saved_output_bytes"] = revised["captured_bytes"]
        result["first_saved_output_sha256"] = revised["captured_sha256"]
        result["hook_exact_matches"] = revised["hook_exact_matches"]
    return result, design


def reduce(direct_host: Path, receipt_host: Path, gold_source: Path) -> dict:
    direct, direct_design = one("direct", direct_host)
    receipt, receipt_design = one("receipt", receipt_host)
    if any(direct_design[key] != receipt_design[key] for key in SHARED_DESIGN):
        raise ValueError("arms used different shared design")
    if direct_design["external_verifier_sha256"] != FROZEN_VERIFIER_SHA256:
        raise ValueError("wrong external verifier")
    if direct["changed_source_ast_sha256"] != receipt["changed_source_ast_sha256"]:
        raise ValueError("candidate ASTs differ")
    gold_bytes = gold_source.read_bytes()
    if digest(gold_bytes) != HISTORICAL_FIX_SOURCE_SHA256:
        raise ValueError("wrong historical fix source")
    gold_ast = ast_digest(gold_bytes)
    direct_usage = direct["provider_observed_usage"]
    receipt_usage = receipt["provider_observed_usage"]
    return {
        "schema": "solcodex.packaging-prehook-repair-development.v1",
        "task": "exposed packaging #928",
        "parent_revision": "3f83dea9b60e660e464535a1019d2de62723884f",
        "historical_fix_revision": "a1f705642e50b79da6be83fb0f6149daa32fc7cc",
        "declared_model": "gpt-6-luna",
        "declared_reasoning_effort": "low",
        "run_order": ["direct", "receipt"],
        "installed_solcodex_plugin": False,
        "temporary_prehook_plugin": True,
        "independent_task": False,
        "confirmatory_pair": False,
        "provider_billing_complete": False,
        "original_audit_rejected_both_arms": True,
        "audit_rule_revised_after_both_runs": True,
        "parent_external_checks": "8/14",
        "historical_fix_external_checks": "14/14",
        "shared_design_sha256": {key: direct_design[key] for key in SHARED_DESIGN},
        "candidate_ast_matches_historical_fix": direct["changed_source_ast_sha256"] == gold_ast,
        "historical_fix_ast_sha256": gold_ast,
        "runs": {"direct": direct, "receipt": receipt},
        "receipt_minus_direct": {
            "input_plus_output_tokens": receipt_usage["input_plus_output_tokens"] - direct_usage["input_plus_output_tokens"],
            "uncached_input_tokens": receipt_usage["uncached_input_tokens"] - direct_usage["uncached_input_tokens"],
            "cached_input_tokens": receipt_usage["cached_input_tokens"] - direct_usage["cached_input_tokens"],
            "output_tokens": receipt_usage["output_tokens"] - direct_usage["output_tokens"],
            "agent_elapsed_seconds": round(receipt["agent_elapsed_seconds"] - direct["agent_elapsed_seconds"], 3),
            "command_executions": receipt["command_executions"] - direct["command_executions"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direct_host_artifacts", type=Path)
    parser.add_argument("receipt_host_artifacts", type=Path)
    parser.add_argument("gold_source", type=Path)
    args = parser.parse_args()
    try:
        result = reduce(args.direct_host_artifacts, args.receipt_host_artifacts,
                        args.gold_source)
    except (KeyError, TypeError, ValueError, OSError, SyntaxError, json.JSONDecodeError):
        print("invalid private inputs", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
