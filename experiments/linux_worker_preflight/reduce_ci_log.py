#!/usr/bin/env python3
"""Extract one fixed-field, path-free Linux preflight result from a CI job log.

The raw log stays private. This reducer accepts only the inaugural controlled
push run and never copies arbitrary log fields into the published aggregate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from experiments.linux_worker_preflight.run import EXPECTED_AGENT_CHECKS


SCHEMA = "solcodex.linux-containment-development.v1"
RUN_ID = 35988221556
JOB_ID = 107595811139
COMMIT = "2c547bddaf516c97151b5f9c8583ce31d5110425"
CONTROLLER_SHA256 = "75e8a6532d48a5dea38da5c9f237b7920f33ab8f26e3a032ef6492ad5efae098"
PROBE_SHA256 = "a7b89f3a0bdaa618cda4a3b7217587add9bfbdecb8ba9298c8f5794abf7f88b1"
RAW_JOB_LOG_SHA256 = "22b61d51bc4267f8ca9769bd0e0b81bdcdbe8fd045fcad990c63d421e82df03b"
HOST_CHECKS = frozenset({
    "agent_configuration", "broker_egress_control", "cleanup_complete",
    "daemon_seccomp_enabled", "deliberate_leak_detected", "forbidden_canaries_live",
    "host_control_live", "host_service_positive_control", "isolated_network_config",
    "positive_agent_passed",
})
TOP_LEVEL = frozenset({
    "schema", "agent_checks", "checks", "controller_sha256", "docker_server_version",
    "image", "model_requests", "passed", "probe_sha256",
    "provider_billing_complete", "resource_limits", "runner",
    "target_worker_qualified", "unqualified_observations",
})


def valid_hex(value: object, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{length}}}", value) is not None


def check_bool_map(value: object, expected: frozenset[str], all_true: bool = True) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("missing or unexpected check keys")
    if any(type(item) is not bool for item in value.values()):
        raise ValueError("check value is not Boolean")
    if all_true and not all(value.values()):
        raise ValueError("one or more required checks failed")


def validate(report: object) -> dict:
    if not isinstance(report, dict) or set(report) != TOP_LEVEL:
        raise ValueError("unexpected report fields")
    if (report["schema"] != SCHEMA or report["passed"] is not True or
            type(report["model_requests"]) is not int or report["model_requests"] != 0 or
            report["provider_billing_complete"] is not False or
            report["target_worker_qualified"] is not False or
            report["controller_sha256"] != CONTROLLER_SHA256 or
            report["probe_sha256"] != PROBE_SHA256):
        raise ValueError("report status or code hash differs from pinned run")
    check_bool_map(report["agent_checks"], EXPECTED_AGENT_CHECKS)
    check_bool_map(report["checks"], HOST_CHECKS)
    check_bool_map(report["unqualified_observations"], frozenset({
        "public_ipv4_connection_failed", "public_ipv6_connection_failed",
    }), all_true=False)

    image = report["image"]
    if (not isinstance(image, dict) or set(image) != {"architecture", "id", "reference"} or
            image["architecture"] != "amd64" or
            not isinstance(image["id"], str) or not image["id"].startswith("sha256:") or
            not valid_hex(image["id"][7:], 64) or
            not isinstance(image["reference"], str) or
            not image["reference"].startswith("python@sha256:") or
            not valid_hex(image["reference"][14:], 64)):
        raise ValueError("invalid image identity")
    limits = report["resource_limits"]
    if (not isinstance(limits, dict) or
            set(limits) != {"cpus", "memory_bytes", "memory_swap_bytes", "pids", "tmpfs_bytes"} or
            any(type(item) is not int for item in limits.values()) or
            limits != {"cpus": 2, "memory_bytes": 2 * 1024**3,
                       "memory_swap_bytes": 2 * 1024**3, "pids": 64,
                       "tmpfs_bytes": 64 * 1024**2}):
        raise ValueError("resource limits differ from design")
    runner = report["runner"]
    if (not isinstance(runner, dict) or
            set(runner) != {"available_cpus", "host_memory_bytes", "kernel", "machine"} or
            type(runner["available_cpus"]) is not int or runner["available_cpus"] < 2 or
            type(runner["host_memory_bytes"]) is not int or runner["host_memory_bytes"] < 2 * 1024**3 or
            runner["machine"] != "x86_64" or
            not isinstance(runner["kernel"], str) or not runner["kernel"] or
            not isinstance(report["docker_server_version"], str) or
            not re.fullmatch(r"\d+\.\d+\.\d+", report["docker_server_version"])):
        raise ValueError("unexpected runner metadata")
    return report


def extract(raw: bytes) -> dict:
    reports = []
    for line in raw.decode("utf-8").splitlines():
        marker = '{"agent_checks":'
        if SCHEMA not in line or marker not in line:
            continue
        candidate = line[line.index(marker):]
        report, end = json.JSONDecoder().raw_decode(candidate)
        if candidate[end:].strip():
            raise ValueError("unexpected data after report")
        reports.append(report)
    if len(reports) != 1:
        raise ValueError("expected exactly one CI report")
    return validate(reports[0])


def reduce(raw: bytes) -> dict:
    log_sha256 = hashlib.sha256(raw).hexdigest()
    if log_sha256 != RAW_JOB_LOG_SHA256:
        raise ValueError("CI log hash differs from the pinned run")
    report = extract(raw)
    return {
        "schema": "solcodex.linux-preflight-ci-aggregate.v1",
        "source_commit": COMMIT,
        "ci_run_url": f"https://github.com/DmitrL-dev/SoLCodex/actions/runs/{RUN_ID}",
        "ci_job_id": JOB_ID,
        "raw_job_log_sha256": log_sha256,
        "report": report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_job_log", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    aggregate = reduce(args.raw_job_log.read_bytes())
    args.output.write_text(json.dumps(aggregate, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
