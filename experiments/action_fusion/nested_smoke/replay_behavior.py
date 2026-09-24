#!/usr/bin/env python3
"""Replay the published nested-tool candidate in pinned Linux containers."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import subprocess

from experiments.action_fusion.replay_behavior import IMAGE, one, sha


HERE = Path(__file__).resolve().parent
BASE = HERE.parent
AGGREGATE = BASE.parents[1] / "docs/measurements/data/2026-09-24-nested-tool-cli-smoke.json"


def main() -> int:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("this replay requires Linux x86-64 and Docker Engine")
    subprocess.run(["docker", "pull", IMAGE], check=True, capture_output=True, timeout=240)
    aggregate = json.loads(AGGREGATE.read_text())
    protocol_raw = (HERE / "protocol.json").read_bytes()
    protocol = json.loads(protocol_raw)
    if sha(protocol_raw) != aggregate["protocol_sha256"]:
        raise ValueError("frozen protocol mismatch")
    for filename, key in (("external_verify.py", "external_verifier_sha256"),
                          ("invoke_candidate.py", "candidate_invoker_sha256")):
        if sha((BASE / filename).read_bytes()) != protocol[key]:
            raise ValueError("verifier source mismatch: " + filename)
    source = HERE / "results/valid/duration.py"
    report = one(source)
    passed = (report["accepted"] is True and report["passed"] == 34 and
              report["total"] == 34 and report["invalid_wire_cases"] == [] and
              report["source_sha256"] == protocol["preflight_gold_sha256"] ==
              aggregate["arms"]["valid"]["source_sha256"])
    print(json.dumps({"schema": "solcodex.nested-tool-cli-smoke-replay.v1",
                      "image": IMAGE, "source_sha256": report["source_sha256"],
                      "passed_cases": report["passed"], "total_cases": report["total"],
                      "passed": passed}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
