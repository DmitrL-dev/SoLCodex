"""Reject incomplete or misleading CI preflight aggregates."""
from __future__ import annotations

import copy
import json
import unittest

from experiments.linux_worker_preflight.reduce_ci_log import (
    CONTROLLER_SHA256, HOST_CHECKS, PROBE_SHA256, SCHEMA, extract, reduce, validate,
)
from experiments.linux_worker_preflight.run import EXPECTED_AGENT_CHECKS


def valid_report() -> dict:
    return {
        "schema": SCHEMA,
        "agent_checks": dict.fromkeys(EXPECTED_AGENT_CHECKS, True),
        "checks": dict.fromkeys(HOST_CHECKS, True),
        "controller_sha256": CONTROLLER_SHA256,
        "probe_sha256": PROBE_SHA256,
        "docker_server_version": "28.0.4",
        "image": {"architecture": "amd64", "id": "sha256:" + "a" * 64,
                  "reference": "python@sha256:" + "b" * 64},
        "model_requests": 0,
        "passed": True,
        "provider_billing_complete": False,
        "resource_limits": {"cpus": 2, "memory_bytes": 2 * 1024**3,
                            "memory_swap_bytes": 2 * 1024**3, "pids": 64,
                            "tmpfs_bytes": 64 * 1024**2},
        "runner": {"available_cpus": 4, "host_memory_bytes": 16 * 1024**3,
                   "kernel": "test-kernel", "machine": "x86_64"},
        "target_worker_qualified": False,
        "unqualified_observations": {"public_ipv4_connection_failed": True,
                                     "public_ipv6_connection_failed": True},
    }


class ReducerTests(unittest.TestCase):
    def test_one_fixed_report(self):
        report = valid_report()
        raw = ("2026-09-24T10:00:00Z " + json.dumps(report, sort_keys=True) + "\n").encode()
        self.assertEqual(extract(raw), report)
        with self.assertRaisesRegex(ValueError, "log hash"):
            reduce(raw)

    def test_claimed_success_with_missing_or_failed_check_is_rejected(self):
        for mutation in ("missing", "false"):
            with self.subTest(mutation=mutation):
                report = copy.deepcopy(valid_report())
                if mutation == "missing":
                    del report["checks"]["cleanup_complete"]
                else:
                    report["agent_checks"]["gold_denied"] = False
                with self.assertRaises(ValueError):
                    validate(report)

    def test_unexpected_field_and_duplicate_report_are_rejected(self):
        report = valid_report()
        report["host_path"] = "/private/example"
        with self.assertRaises(ValueError):
            validate(report)
        del report["host_path"]
        line = (json.dumps(report, sort_keys=True) + "\n").encode()
        with self.assertRaises(ValueError):
            extract(line + line)


if __name__ == "__main__":
    unittest.main()
