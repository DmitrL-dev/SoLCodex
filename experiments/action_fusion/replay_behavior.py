#!/usr/bin/env python3
"""Replay published duration behavior in network-isolated Linux containers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import subprocess
import uuid

from experiments.action_fusion.external_verify import CASES, judge


IMAGE = "python@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9"
ROOT = Path(__file__).resolve().parent
PARENT_PASSED = frozenset({
    "bool_false", "bool_true", "bytes", "dict", "float", "int_hour",
    "int_large", "int_one", "int_zero", "list", "negative_int", "none",
})


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def invoke(source: Path, invocation: dict) -> tuple[str, bool]:
    source = source.resolve(strict=True)
    container_name = "sol-action-fusion-" + uuid.uuid4().hex
    command = [
        "docker", "run", "--rm", "--name", container_name,
        "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--pids-limit", "32", "--memory", "256m", "--cpus", "1",
        "--user", "65534:65534", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=16m",
        "--mount", f"type=bind,source={source},target=/candidate/duration.py,readonly",
        "--mount", f"type=bind,source={ROOT / 'invoke_candidate.py'},target=/opt/invoke.py,readonly",
        IMAGE, "python", "-B", "/opt/invoke.py", "/candidate/duration.py",
        json.dumps(invocation, ensure_ascii=True, separators=(",", ":")),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=15)
    finally:
        subprocess.run(["docker", "rm", "-f", container_name],
                       capture_output=True, timeout=15)
        inspection = subprocess.run(["docker", "container", "inspect", container_name],
                                    capture_output=True, timeout=15)
        if (inspection.returncode == 0 or
                not any(marker in inspection.stderr for marker in
                        (b"No such object", b"No such container"))):
            raise RuntimeError("candidate container cleanup could not be verified")
    output = result.stdout
    valid = (result.returncode == 0 and len(output) <= 64 and
             output.endswith(b"\n") and output.count(b"\n") == 1)
    if valid:
        try:
            return output[:-1].decode("ascii"), True
        except UnicodeDecodeError:
            pass
    return "INVALID", False


def one(source: Path) -> dict:
    observations: dict[str, str] = {}
    invalid = []
    for case, invocation, _expected in CASES:
        observations[case], valid = invoke(source, invocation)
        if not valid:
            invalid.append(case)
    cases = judge(observations)
    return {"source_sha256": sha(source.read_bytes()), "passed": sum(cases.values()),
            "total": len(CASES), "accepted": all(cases.values()) and not invalid,
            "invalid_wire_cases": invalid, "case_results": cases}


def parent_qualified(report: dict) -> bool:
    return (report["accepted"] is False and report["invalid_wire_cases"] == [] and
            report["case_results"] ==
            {name: name in PARENT_PASSED for name, _, _ in CASES})


def witness_qualified(actual: str, valid: bool, expected: str, pinned_wrong: str) -> bool:
    return valid and pinned_wrong not in (expected, "X", "INVALID") and actual == pinned_wrong


def main() -> int:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("this replay requires Linux x86-64 and Docker Engine")
    subprocess.run(["docker", "pull", IMAGE], check=True, capture_output=True, timeout=240)
    sources = {
        "parent": ROOT / "fixture/duration.py",
        "off": ROOT / "results/off/duration.py",
        "on": ROOT / "results/on/duration.py",
    }
    reports = {name: one(path) for name, path in sources.items()}
    aggregate = json.loads((ROOT.parents[1] / "docs/measurements/data/"
                            "2026-09-24-action-fusion-mechanism-dev.json").read_text())
    protocol_raw = (ROOT / "protocol.json").read_bytes()
    protocol = json.loads(protocol_raw)
    if sha(protocol_raw) != aggregate["protocol_sha256"]:
        raise ValueError("frozen protocol hash mismatch")
    for name, protocol_key in (("external_verify.py", "external_verifier_sha256"),
                               ("invoke_candidate.py", "candidate_invoker_sha256")):
        if sha((ROOT / name).read_bytes()) != protocol[protocol_key]:
            raise ValueError("frozen evaluator source hash mismatch: " + name)
    fixture = ROOT / "fixture"
    rows = [(path.relative_to(fixture).as_posix(), sha(path.read_bytes()))
            for path in sorted(fixture.rglob("*")) if path.is_file()]
    if sha(json.dumps(rows, separators=(",", ":")).encode()) != protocol["fixture_tree_sha256"]:
        raise ValueError("frozen fixture tree hash mismatch")
    manifest = json.loads((ROOT / "controls_manifest.json").read_text())
    controls = {}
    case_by_name = {name: (invocation, expected) for name, invocation, expected in CASES}
    for name, entry in manifest["controls"].items():
        source = ROOT / "controls" / name / "duration.py"
        digest = sha(source.read_bytes())
        if digest != entry["source_sha256"]:
            raise ValueError("control source hash mismatch: " + name)
        if name == "reference":
            controls[name] = one(source)
            continue
        case = entry["witness_case"]
        invocation, expected = case_by_name[case]
        actual, valid = invoke(source, invocation)
        controls[name] = {"source_sha256": digest, "witness_case": case,
                          "witness_rejected": witness_qualified(
                              actual, valid, expected, entry["witness_observed_wire"]),
                          "wire_valid": valid}
    passed = (parent_qualified(reports["parent"]) and
              reports["off"]["accepted"] is True and
              reports["on"]["accepted"] is True and
              all(not report["invalid_wire_cases"] for report in reports.values()) and
              all(reports[name]["source_sha256"] == aggregate["arms"][name]["source_sha256"]
                  for name in ("off", "on")) and
              controls["reference"]["accepted"] is True and
              controls["reference"]["source_sha256"] == protocol["private_reference_sha256"] and
              all(value["witness_rejected"] and value["wire_valid"] for name, value
                  in controls.items() if name != "reference"))
    print(json.dumps({"schema": "solcodex.action-fusion-public-replay.v1",
                      "image": IMAGE, "reports": reports,
                      "controls": controls, "passed": passed},
                     sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
