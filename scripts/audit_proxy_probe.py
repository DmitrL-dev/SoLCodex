"""Reduce private proxy probe metadata and CLI JSONL to safe aggregate evidence.

Input proxy metadata must already exclude headers, request/response bodies and raw
SSE frames. This script deliberately emits only a fixed whitelist of aggregate
fields. It does not establish provider billing or production-grade completeness.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def nonnegative_integer(value):
    return type(value) is int and value >= 0


def journal_observation(raw, attempts, states, tokens):
    if not isinstance(raw, dict):
        return {"valid": False}
    counts = raw.get("states")
    usage = raw.get("observed_completed_usage")
    if not isinstance(counts, dict) or not isinstance(usage, dict):
        return {"valid": False}
    count_keys = ("pending", "completed", "unknown")
    usage_keys = ("input_tokens", "output_tokens", "cached_input_tokens")
    if not nonnegative_integer(raw.get("attempts")) or not all(
        nonnegative_integer(counts.get(key)) for key in count_keys
    ) or not all(nonnegative_integer(usage.get(key)) for key in usage_keys):
        return {"valid": False}
    safe_counts = {key: counts[key] for key in count_keys}
    safe_usage = {key: usage[key] for key in usage_keys}
    valid = (sum(safe_counts.values()) == raw["attempts"] and
             safe_usage["cached_input_tokens"] <= safe_usage["input_tokens"])
    return {"valid": valid, "attempts": raw["attempts"], "states": safe_counts,
            "observed_completed_usage": safe_usage,
            "matches_proxy_observation": valid and raw["attempts"] == attempts and
            safe_counts["completed"] == states["observed_completion"] and
            safe_usage == tokens,
            "provider_billing_complete": False}


def audit(proxy, cli_events):
    attempts = proxy.get("sink_requests") or []
    states = {"upstream_200": 0, "upstream_other": 0, "client_disconnected": 0,
              "observed_completion": 0, "missing_or_ambiguous_usage": 0}
    tokens = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
    kill_to_completion = []
    response_ids = set()
    for attempt in attempts:
        if attempt.get("upstream_status") == 200:
            states["upstream_200"] += 1
        else:
            states["upstream_other"] += 1
        if attempt.get("client_disconnected"):
            states["client_disconnected"] += 1
        completions = attempt.get("completions") or []
        if len(completions) != 1:
            states["missing_or_ambiguous_usage"] += 1
            continue
        item = completions[0]
        values = (item.get("input_tokens"), item.get("output_tokens"),
                  item.get("cached_tokens"))
        if not all(nonnegative_integer(v) for v in values) or values[2] > values[0]:
            states["missing_or_ambiguous_usage"] += 1
            continue
        response_id = item.get("response_id_sha256")
        if response_id is None:
            if len(attempts) > 1:
                states["missing_or_ambiguous_usage"] += 1
                continue
        elif not isinstance(response_id, str) or not re.fullmatch(r"[0-9a-f]{64}", response_id) or response_id in response_ids:
            states["missing_or_ambiguous_usage"] += 1
            continue
        if response_id is not None:
            response_ids.add(response_id)
        states["observed_completion"] += 1
        for key, value in zip(tokens, values):
            tokens[key] += value
        killed_at, completed_at = attempt.get("cli_killed_at"), attempt.get("completion_at")
        if type(killed_at) in (int, float) and type(completed_at) in (int, float):
            kill_to_completion.append(round(completed_at - killed_at, 3))
    cli_completed = [e for e in cli_events if e.get("type") == "turn.completed"]
    cli_tokens = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
    cli_usage_known = True
    for event in cli_completed:
        usage = event.get("usage") or {}
        fields = (usage.get("input_tokens"), usage.get("output_tokens"),
                  usage.get("cached_input_tokens"))
        if not all(nonnegative_integer(v) for v in fields) or fields[2] > fields[0]:
            cli_usage_known = False
            continue
        for key, value in zip(cli_tokens, fields):
            cli_tokens[key] += value
    exit_code = proxy.get("exit")
    result = {"cli_exit_code": exit_code if type(exit_code) is int else None,
            "cli_turn_completions": len(cli_completed),
            "cli_completed_usage": cli_tokens if cli_usage_known and cli_completed else None,
            "attempts": len(attempts), "attempt_states": states,
            "observed_response_usage": tokens,
            "kill_to_completion_seconds": kill_to_completion,
            "all_attempts_have_observed_usage": bool(attempts) and
            states["missing_or_ambiguous_usage"] == 0,
            "durable_proxy_ledger_tested": False,
            "provider_billing_complete": False}
    if "journal" in proxy:
        result["attempt_journal"] = journal_observation(
            proxy["journal"], len(attempts), states, tokens)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy-summary", required=True, type=Path)
    parser.add_argument("--cli-trace", required=True, type=Path)
    args = parser.parse_args()
    proxy = json.loads(args.proxy_summary.read_text())
    cli_events = []
    for line in args.cli_trace.read_text().splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            cli_events.append(item)
    print(json.dumps(audit(proxy, cli_events), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
