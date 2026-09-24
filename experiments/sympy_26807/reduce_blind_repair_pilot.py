"""Audit two instruction-blinded repair attempts on exposed SymPy #26807.

The source patch is replayed alone on a clean parent export. Raw agent traces,
private paths, detailed exceptions, and self-written tests are not published.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from experiments.sympy_26807.materialize_variants import (
        PARENT_EXPORT, SOURCE_FILE, export_digest, variant,
    )
    from experiments.sympy_26807.reduce_qualification import (
        M1_FAILURES, REPORT_NAMES, read_behavior, read_upstream, reduce, sha,
    )
except ModuleNotFoundError:
    from materialize_variants import PARENT_EXPORT, SOURCE_FILE, export_digest, variant
    from reduce_qualification import (
        M1_FAILURES, REPORT_NAMES, read_behavior, read_upstream, reduce, sha,
    )


TEST_FILE = Path("sympy/utilities/tests/test_lambdify.py")
ATTEMPT_ONE_TEST_SHA256 = "f6956e7ecd47ebc94c7e3a514ad85b4c4978e9a2f51bc6999e3ad08add270548"
CACHE_DIRECTORIES = {"__pycache__", ".pytest_cache"}
ENVIRONMENT_FIELDS = (
    "python", "python_executable", "dependencies", "platform", "machine",
    "recursion_limit", "sympy_version",
)


def durable_files(root: Path) -> dict[str, str]:
    rows = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in CACHE_DIRECTORIES for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError("source checkout contains an unexpected symlink")
        if path.is_file():
            rows[relative.as_posix()] = sha(path.read_bytes())
        elif not path.is_dir():
            raise ValueError("source checkout contains a nonregular entry")
    return rows


def reduce_blind(parent: Path, gold: Path, superclass: Path, alternative: Path,
                 baseline_reports: Path, baseline_upstream: Path,
                 attempts: dict[str, Path], evaluation: Path,
                 reports: Path, upstream_reports: Path) -> dict:
    directory = Path(__file__).resolve().parent
    aggregate_path = (directory.parents[1] / "docs/measurements/data/"
                      "2026-09-24-sympy-verifier-known-miss-dev.json")
    aggregate_raw = aggregate_path.read_bytes()
    baseline = reduce({"parent": parent, "gold": gold, "superclass": superclass,
                       "alternative": alternative}, baseline_reports, baseline_upstream)
    if baseline != json.loads(aggregate_raw):
        raise ValueError("published baseline aggregate does not reproduce")
    if export_digest(parent) != (2061, PARENT_EXPORT):
        raise ValueError("wrong parent export")
    parent_files = durable_files(parent)
    original = (parent / SOURCE_FILE).read_bytes()
    repaired = variant(original.decode(), "blind_superclass_before_shape").encode()
    repaired_hash = sha(repaired)
    attempt_results = {}
    expected_changes = {"a": {SOURCE_FILE.as_posix(), TEST_FILE.as_posix()},
                        "b": {SOURCE_FILE.as_posix()}}
    for name in ("a", "b"):
        root = attempts[name]
        files = durable_files(root)
        changed = {key for key in parent_files.keys() | files.keys()
                   if parent_files.get(key) != files.get(key)}
        if changed != expected_changes[name] or files[SOURCE_FILE.as_posix()] != repaired_hash:
            raise ValueError(f"attempt {name} differs from the recorded repair")
        if name == "a" and files[TEST_FILE.as_posix()] != ATTEMPT_ONE_TEST_SHA256:
            raise ValueError("attempt a self-written test changed")
        attempt_results[name] = {
            "changed_regular_paths": sorted(changed),
            "source_key_sha256": repaired_hash,
            "self_written_test_sha256": files[TEST_FILE.as_posix()] if name == "a" else None,
        }
    if (evaluation / SOURCE_FILE).read_bytes() != repaired:
        raise ValueError("evaluation copy does not contain the agent source patch")
    evaluation_files = durable_files(evaluation)
    changed = {key for key in parent_files.keys() | evaluation_files.keys()
               if parent_files.get(key) != evaluation_files.get(key)}
    if changed != {SOURCE_FILE.as_posix()}:
        raise ValueError("clean evaluation changed files beyond the source patch")
    count, evaluation_hash = export_digest(evaluation)
    if count != 2061:
        raise ValueError("clean evaluation source count changed")
    behavior = {}
    metadata = []
    for version in ("v1", "v2"):
        path = reports / f"blind-sympy-{version}.json"
        result, ids, meta = read_behavior(
            path, evaluation, baseline["verifier_sha256"][version],
            repaired_hash, version)
        original_report = json.loads((baseline_reports / REPORT_NAMES["parent"][version]).read_text())
        if ids != [item["id"] for item in original_report["results"]]:
            raise ValueError("blind repair assertion inventory changed")
        failures = set(result["failed_ids"])
        if failures != (set() if version == "v1" else M1_FAILURES):
            raise ValueError("blind repair verifier result differs from recorded matrix")
        if version == "v2":
            raw_report = json.loads(path.read_text())
            for item in raw_report["results"]:
                if item["id"] in M1_FAILURES:
                    reason = ("ZeroArray classification changed" if item["id"].startswith("C19")
                              else "OneArray classification changed")
                    if item.get("error_type") != "AssertionError" or item.get("error") != reason:
                        raise ValueError("blind repair failed for an unexpected reason")
        behavior[version] = result
        metadata.append(meta)
    if tuple(metadata[0][key] for key in ENVIRONMENT_FIELDS) != tuple(
            metadata[1][key] for key in ENVIRONMENT_FIELDS):
        raise ValueError("blind verifier runs changed environments")
    selection = (directory / "upstream_selection.json").read_bytes()
    nodes = json.loads(selection)
    upstream, ids = read_upstream(
        upstream_reports / "m1-upstream.xml", evaluation, evaluation_hash,
        sha(selection), nodes, metadata[0]["python_executable"])
    inventory = json.loads((directory / "upstream_inventory.json").read_text())
    if ids != [tuple(item) for item in inventory]:
        raise ValueError("blind upstream inventory differs from pinned selection")
    return {
        "schema": "solcodex.sympy-26807-blind-repair-development.v2",
        "exposed_development_task": True,
        "separate_agent_attempts": 2,
        "distinct_source_patches": 1,
        "gold_and_verifier_withheld_by_instruction": True,
        "filesystem_blinding_enforced": False,
        "independent_quality_estimate": False,
        "source_patch_acceptability": "unresolved",
        "provider_usage_recorded": False,
        "baseline_aggregate_sha256": sha(aggregate_raw),
        "parent_revision": baseline["parent_revision"],
        "historical_fix_revision": baseline["historical_fix_revision"],
        "attempts": attempt_results,
        "evaluation": {
            "source_export_sha256": evaluation_hash,
            "source_key_sha256": repaired_hash,
            "verifier_sha256": baseline["verifier_sha256"],
            "behavior": behavior,
            "upstream": upstream,
            "upstream_selection_sha256": sha(selection),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in REPORT_NAMES:
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--baseline-reports", type=Path, required=True)
    parser.add_argument("--baseline-upstream", type=Path, required=True)
    parser.add_argument("--attempt-a", type=Path, required=True)
    parser.add_argument("--attempt-b", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--upstream-reports", type=Path, required=True)
    args = parser.parse_args()
    result = reduce_blind(
        args.parent, args.gold, args.superclass, args.alternative,
        args.baseline_reports, args.baseline_upstream,
        {"a": args.attempt_a, "b": args.attempt_b}, args.evaluation,
        args.reports, args.upstream_reports)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
