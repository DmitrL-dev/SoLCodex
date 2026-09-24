"""Reduce sequential verbose/quiet pytest preflight reports without raw output."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from experiments.packaging_checkpoint.verify_installed import parse_junit


CASES = {
    "packaging_parent": ("parent", b"InvalidLicenseExpression"),
    "packaging_gold": ("gold", None),
    "click_parent": ("click-parent", b"assert None is RuntimeError"),
    "click_gold": ("click-gold", None),
}
MAX_OUTPUT = 2 * 1024 * 1024


def one(reports: Path, prefix: str, witness: bytes | None) -> dict:
    modes = {}
    for mode in ("verbose", "quiet"):
        output = reports / f"{prefix}-{mode}.out"
        raw = output.read_bytes()
        if len(raw) > MAX_OUTPUT:
            raise ValueError(f"{prefix}: output exceeds bound")
        junit = parse_junit(reports / f"{prefix}-{mode}.xml")
        if junit is None:
            raise ValueError(f"{prefix}: invalid {mode} JUnit report")
        if witness is not None and witness not in raw:
            raise ValueError(f"{prefix}: {mode} omits diagnostic witness")
        modes[mode] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                       "junit": junit}
    verbose, quiet = modes["verbose"], modes["quiet"]
    if (verbose["junit"] != quiet["junit"] or
            quiet["bytes"] >= verbose["bytes"]):
        raise ValueError(f"{prefix}: changed outcomes or no byte reduction")
    counts = {key: verbose["junit"][key] for key in
              ("tests", "failures", "errors", "skipped")}
    return {"verbose_bytes": verbose["bytes"], "quiet_bytes": quiet["bytes"],
            "verbose_sha256": verbose["sha256"],
            "quiet_sha256": quiet["sha256"],
            "same_collected_inventory_and_outcomes": True,
            "diagnostic_witness_retained": None if witness is None else True,
            **counts}


def reduce(reports: Path) -> dict:
    rows = {label: one(reports, prefix, witness)
            for label, (prefix, witness) in CASES.items()}
    if (any(rows[label]["failures"] != 1 for label in
            ("packaging_parent", "click_parent")) or
            any(rows[label]["failures"] != 0 for label in
                ("packaging_gold", "click_gold")) or
            any(row["errors"] != 0 for row in rows.values())):
        raise ValueError("parent/gold failure controls differ")
    return {"schema": "solcodex.quiet-diagnostic-preflight.v1",
            "pair_count": len(rows), "real_model_requests": 0,
            "provider_billing_complete": False,
            "task_level_savings_established": False,
            "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = reduce(args.reports)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
