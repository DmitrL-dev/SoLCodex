"""Prospective per-job V6 phase structure; development component.

Consumes new canonical ASCII JSON+LF launch/report bytes. No V5 adapter and no
old-record reinterpretation. The launch is an independently supplied, frozen
expectation; never construct it from the report under assessment.

This component checks collection, actual phase protocol, declared outcomes and
complete opaque failure digests. It does NOT validate exception/code/causal
content, origin history, observer truth, host custody, six controls, candidate
acceptance or transport completion. Separate content and external verification
remain mandatory. A matching JSON report is not a passing scientific result.
"""
from __future__ import annotations

import hashlib
import json
import math
import re


LAUNCH_SCHEMA = "solcodex.control-phase-launch.v6"
REPORT_SCHEMA = "solcodex.control-phase-report.v6"
RESULT_SCHEMA = "solcodex.control-phase-structure.v6"
MAX_RAW = 32 * 1024 * 1024
MAX_NODES = 10000
MAX_JSON_DEPTH = 64
MAX_JSON_VALUES = 1000000
PHASES = ("setup", "call", "teardown")
PROFILES = (("3.10.19", "9.0.2"), ("3.12.3", "8.3.5"))
HASH_BINDINGS = (
    "runtime_policy_sha256", "catalog_sha256", "source_manifest_sha256",
    "environment_sha256", "snapshot_sha256", "input_sha256",
    "launcher_source_sha256", "observer_source_sha256", "finalizer_source_sha256",
    "protected_startup_sha256", "runtime_rewrite_profile_sha256",
    "fingerprint_source_sha256",
)


class ReportStructureError(ValueError):
    """Unresolved structure/evidence; never turn into a zero, skip or retry."""


def require(condition, diagnostic):
    if not condition:
        raise ReportStructureError(diagnostic)


def _tree(value):
    """Exact native JSON types before encoding; representation limits only."""
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        require(count <= MAX_JSON_VALUES and depth <= MAX_JSON_DEPTH,
                "json-representation-limit")
        kind = type(item)
        if kind is dict:
            require(all(type(k) is str for k in item), "json-key-type")
            pending.extend((v, depth + 1) for v in item.values())
        elif kind is list:
            pending.extend((v, depth + 1) for v in item)
        elif kind is float:
            require(math.isfinite(item), "json-nonfinite")
        else:
            require(item is None or kind is str or kind is int or kind is bool,
                    "json-value-type")


def canonical(value):
    """New V6 raw-byte domain, including exactly one final LF."""
    _tree(value)
    try:
        raw = (json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise ReportStructureError("json-unrepresentable") from error
    require(0 < len(raw) <= MAX_RAW, "raw-byte-limit")
    return raw


def raw_sha256(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_RAW, "raw-byte-limit")
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "json-duplicate-key")
        result[key] = value
    return result


def _constant(value):
    raise ReportStructureError("json-nonfinite")


def decode(raw):
    raw_sha256(raw)
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except ReportStructureError:
        raise
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ReportStructureError("json-invalid") from error
    require(canonical(value) == raw, "json-canonical-ascii-lf")
    return value


def fields(value, names, diagnostic):
    require(type(value) is dict and set(value) == set(names), diagnostic)


def sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            "sha256-type-or-format")
    return value


def text(value):
    require(type(value) is str and 0 < len(value) <= 4096 and "\x00" not in value,
            "text-type-or-size")
    return value


def nodes(value):
    require(type(value) is list and 0 < len(value) <= MAX_NODES, "node-list-size")
    for node in value:
        text(node)
    require(len(set(value)) == len(value), "duplicate-node")
    return value


def validate_launch(launch_raw, *, expected_launch_sha256):
    require(raw_sha256(launch_raw) == sha(expected_launch_sha256), "launch-raw-sha256")
    launch = decode(launch_raw)
    fields(launch, ("schema", "job_id", "nonce", "purpose", "arm", "ordinal",
                    "profile", "bindings", "work_root", "collected", "FAIL_TO_PASS",
                    "phase_contracts"), "launch-fields")
    require(launch["schema"] == LAUNCH_SCHEMA, "launch-schema-v6-required")
    text(launch["job_id"])
    sha(launch["nonce"])
    require(type(launch["purpose"]) is str and type(launch["arm"]) is str
            and ((launch["purpose"] == "control" and launch["arm"] in ("base", "gold"))
                 or (launch["purpose"] == "candidate" and launch["arm"] == "candidate")),
            "purpose-arm")
    require(type(launch["ordinal"]) is int and 0 <= launch["ordinal"] < 2**63,
            "ordinal-type-or-range")
    require(type(launch["work_root"]) is str and launch["work_root"] == "/work",
            "literal-work-root")
    profile = launch["profile"]
    fields(profile, ("python_version", "pytest_version", "image_sha256"), "profile-fields")
    text(profile["python_version"])
    text(profile["pytest_version"])
    require((profile["python_version"], profile["pytest_version"]) in PROFILES,
            "original-profile-unresolved")
    sha(profile["image_sha256"])
    fields(launch["bindings"], HASH_BINDINGS, "bindings-fields")
    for value in launch["bindings"].values():
        sha(value)
    collected = nodes(launch["collected"])
    tfp = nodes(launch["FAIL_TO_PASS"])
    require(set(tfp) <= set(collected), "tfp-outside-collection")
    contracts = launch["phase_contracts"]
    require(type(contracts) is list and len(contracts) == len(collected), "contract-count")
    for node, contract in zip(collected, contracts):
        fields(contract, ("nodeid", "phases"), "contract-fields")
        require(type(contract["nodeid"]) is str and contract["nodeid"] == node,
                "contract-node-order")
        phases = contract["phases"]
        require(type(phases) is list and len(phases) in (2, 3), "contract-phase-count")
        for phase in phases:
            fields(phase, ("when", "outcome", "failure_raw_sha256"), "contract-phase-fields")
            require(type(phase["when"]) is str and type(phase["outcome"]) is str
                    and phase["outcome"] in ("passed", "failed"), "contract-phase-value")
            if phase["outcome"] == "passed":
                require(phase["failure_raw_sha256"] is None, "passed-contract-failure")
            else:
                sha(phase["failure_raw_sha256"])
        setup_failed = phases[0]["outcome"] == "failed"
        require([p["when"] for p in phases] == (["setup", "teardown"] if setup_failed else list(PHASES)),
                "honest-setup-phase-protocol")
        failures = [p for p in phases if p["outcome"] == "failed"]
        is_base_tfp = launch["arm"] == "base" and node in tfp
        require(bool(failures) == is_base_tfp, "base-tfp-or-all-phase-pass-contract")
    return launch


def assess_report(report_raw, *, launch_raw, expected_launch_sha256, expected_origin_sha256):
    """Match one V6 report. Transport/cleanup/semantic acceptance stay external."""
    launch = validate_launch(launch_raw, expected_launch_sha256=expected_launch_sha256)
    report = decode(report_raw)
    fields(report, ("schema", "launch_raw_sha256", "job_id", "nonce", "purpose", "arm",
                    "ordinal", "profile", "bindings", "work_root", "session_started",
                    "collection_finished", "session_finished", "pytest_status",
                    "collection_errors", "infrastructure_errors", "collected",
                    "origin_raw_sha256", "reports"), "report-fields")
    require(report["schema"] == REPORT_SCHEMA, "report-schema-v6-required")
    require(sha(report["launch_raw_sha256"]) == expected_launch_sha256, "report-launch-binding")
    for key in ("job_id", "nonce", "purpose", "arm", "ordinal", "profile", "bindings", "work_root"):
        # Report has already been decoded into exact native JSON types. Check
        # every scalar type recursively via canonical bytes: True != ordinal1.
        require(canonical(report[key]) == canonical(launch[key]), "report-binding-" + key)
    for key in ("session_started", "collection_finished", "session_finished"):
        require(report[key] is True, "incomplete-" + key)
    for key in ("collection_errors", "infrastructure_errors"):
        require(type(report[key]) is list and not report[key], "veto-" + key)
    expected_pytest_status = 1 if launch["arm"] == "base" else 0
    require(type(report["pytest_status"]) is int and report["pytest_status"] == expected_pytest_status,
            "pytest-status")
    require(nodes(report["collected"]) == launch["collected"], "collection-order-or-content")
    require(sha(report["origin_raw_sha256"]) == sha(expected_origin_sha256), "origin-raw-binding")
    sequence = [(c["nodeid"], p) for c in launch["phase_contracts"] for p in c["phases"]]
    phases = report["reports"]
    require(type(phases) is list and len(phases) == len(sequence), "phase-count")
    failures = []
    for phase, (node, expected) in zip(phases, sequence):
        fields(phase, ("nodeid", "when", "outcome", "wasxfail", "failure"), "phase-fields")
        require(type(phase["nodeid"]) is str and type(phase["when"]) is str
                and (phase["nodeid"], phase["when"]) == (node, expected["when"]),
                "phase-order-or-multiplicity")
        require(phase["wasxfail"] is None and type(phase["outcome"]) is str
                and phase["outcome"] == expected["outcome"], "phase-outcome-or-xfail")
        if phase["outcome"] == "passed":
            require(phase["failure"] is None, "passed-phase-failure")
        else:
            require(type(phase["failure"]) is dict and bool(phase["failure"]), "failure-opaque-object")
            actual_sha = raw_sha256(canonical(phase["failure"]))
            require(actual_sha == expected["failure_raw_sha256"], "failure-full-byte-digest")
            failures.append({"nodeid": node, "when": phase["when"], "failure_raw_sha256": actual_sha})
    return {"schema": RESULT_SCHEMA, "status": "STRUCTURAL_REPORT_MATCH_ONLY",
            "launch_raw_sha256": expected_launch_sha256, "report_raw_sha256": raw_sha256(report_raw),
            "origin_raw_sha256": expected_origin_sha256, "pytest_status": expected_pytest_status,
            "collected_count": len(launch["collected"]), "phase_count": len(phases),
            "opaque_failure_bindings": failures, "failure_content_validated": False,
            "origin_content_validated": False, "transport_observed": False,
            "controls_qualified": False, "candidate_acceptance_authorized": False,
            "host_authentication_established": False}
