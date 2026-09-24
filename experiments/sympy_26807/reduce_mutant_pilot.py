"""Reduce six exposed SymPy diagnostic variants without publishing private paths.

These variants were chosen after the verifier was known. Distinct failure
vectors demonstrate diagnostic sensitivity, not six credible independent fixes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from experiments.sympy_26807.materialize_variants import SOURCE_FILE, export_digest, variant
    from experiments.sympy_26807.reduce_qualification import (
        PARENT_EXPORT, REPORT_NAMES, read_behavior, read_upstream, reduce, sha,
    )
except ModuleNotFoundError:
    from materialize_variants import SOURCE_FILE, export_digest, variant
    from reduce_qualification import (
        PARENT_EXPORT, REPORT_NAMES, read_behavior, read_upstream, reduce, sha,
    )


KINDS = {
    "m2": "rank_one_only", "m3": "concrete_extent_only",
    "m4": "identifier_only", "m5": "zero_sibling", "m6": "one_sibling",
}
EXPECTED_FAILURES = {
    "m2": {
        "R03_list_argument_rank_two_identity": ("TypeError", "object of type 'ArraySymbol' has no len()"),
        "R07_matrix_element_polynomial": ("TypeError", "object of type 'ArraySymbol' has no len()"),
        "R15_rank_three_identity": ("TypeError", "object of type 'ArraySymbol' has no len()"),
        "R16_array_symbols_not_iterable": ("AssertionError", "ArraySymbol treated as iterable"),
        "R17_array_symbols_not_sequences": ("AssertionError", "ArraySymbol treated as sequence"),
    },
    "m3": {
        "R04_symbolic_extent_identity": ("RecursionError", "maximum recursion depth exceeded"),
        "R16_array_symbols_not_iterable": ("AssertionError", "ArraySymbol treated as iterable"),
    },
    "m4": {
        "R13_nonidentifier_name_with_dummify": ("RecursionError", "maximum recursion depth exceeded"),
    },
    "m5": {
        "C19_zero_array_iterable_preserved": ("AssertionError", "ZeroArray classification changed"),
    },
    "m6": {
        "C20_one_array_iterable_preserved": ("AssertionError", "OneArray classification changed"),
    },
}
ENVIRONMENT_FIELDS = (
    "python", "python_executable", "dependencies", "platform", "machine",
    "recursion_limit", "sympy_version",
)


def regular_file_hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): sha(path.read_bytes())
            for path in root.rglob("*") if path.is_file()}


def reduce_pilot(baseline_roots: dict[str, Path], baseline_reports: Path,
                 baseline_upstream: Path, mutant_roots: dict[str, Path],
                 reports: Path, upstream_reports: Path) -> dict:
    directory = Path(__file__).resolve().parent
    baseline_path = (directory.parents[1] / "docs/measurements/data/"
                     "2026-09-24-sympy-verifier-known-miss-dev.json")
    baseline_raw = baseline_path.read_bytes()
    baseline = reduce(baseline_roots, baseline_reports, baseline_upstream)
    if baseline != json.loads(baseline_raw):
        raise ValueError("fresh baseline does not match the published aggregate")
    parent = baseline_roots["parent"]
    if export_digest(parent) != (2061, PARENT_EXPORT):
        raise ValueError("parent differs from pinned source export")
    parent_bytes = (parent / SOURCE_FILE).read_bytes()
    parent_files = regular_file_hashes(parent)
    v2_hash = baseline["verifier_sha256"]["v2"]
    selection = (directory / "upstream_selection.json").read_bytes()
    nodes = json.loads(selection)
    parent_report = json.loads((baseline_reports / REPORT_NAMES["parent"]["v2"]).read_text())
    reference_env = tuple(parent_report["metadata"][key] for key in ENVIRONMENT_FIELDS)
    expected_ids = None
    mutants = {
        "m1": {
            "kind": "superclass_noniterable",
            "source": baseline["source"]["superclass"],
            "behavior": baseline["behavior"]["superclass"]["v2"],
            "upstream": baseline["upstream"]["superclass"],
        }
    }
    failure_vectors = [tuple(mutants["m1"]["behavior"]["failed_ids"])]
    for name, kind in KINDS.items():
        root = mutant_roots[name]
        file_count, export_hash = export_digest(root)
        if file_count != 2061 or (root / SOURCE_FILE).read_bytes() != variant(parent_bytes.decode(), kind).encode():
            raise ValueError(f"{name}: source variant differs from declared edit")
        files = regular_file_hashes(root)
        changed = {key for key in parent_files.keys() | files.keys()
                   if parent_files.get(key) != files.get(key)}
        if changed != {SOURCE_FILE.as_posix()}:
            raise ValueError(f"{name}: source variant changed other files")
        source_hash = files[SOURCE_FILE.as_posix()]
        result_path = reports / f"{name}-v2.json"
        behavior, ids, metadata = read_behavior(result_path, root, v2_hash,
                                                source_hash, "v2")
        if expected_ids is None:
            old_report = json.loads((baseline_reports / REPORT_NAMES["parent"]["v2"]).read_text())
            expected_ids = [item["id"] for item in old_report["results"]]
        if ids != expected_ids or tuple(metadata[key] for key in ENVIRONMENT_FIELDS) != reference_env:
            raise ValueError(f"{name}: verifier inventory or environment changed")
        rows = json.loads(result_path.read_text())["results"]
        actual = {item["id"]: (item.get("error_type"), item.get("error"))
                  for item in rows if item["status"] == "fail"}
        if actual != EXPECTED_FAILURES[name] or rows[0]["id"] != "R01_issue_bare_argument_default_modules" or rows[0]["status"] != "pass":
            raise ValueError(f"{name}: defect smoke or wrong-fix witness differs")
        upstream, upstream_ids = read_upstream(
            upstream_reports / f"{name}-upstream.xml", root, export_hash,
            sha(selection), nodes, metadata["python_executable"])
        if upstream_ids != [tuple(item) for item in json.loads((directory / "upstream_inventory.json").read_text())]:
            raise ValueError(f"{name}: upstream test inventory changed")
        mutants[name] = {
            "kind": kind,
            "source": {"export_sha256": export_hash, "source_key_sha256": source_hash},
            "behavior": behavior, "upstream": upstream,
        }
        failure_vectors.append(tuple(behavior["failed_ids"]))
    if len(set(failure_vectors)) != 6 or any(not vector for vector in failure_vectors):
        raise ValueError("diagnostic variants are not distinguished by v2")
    return {
        "schema": "solcodex.sympy-26807-diagnostic-mutant-pilot.v1",
        "exposed_development_task": True,
        "mutants_selected_after_v2_known": True,
        "six_distinct_diagnostic_vectors_observed": True,
        "six_plausible_wrong_fix_qualification_complete": False,
        "independent_quality_estimate": False,
        "baseline_aggregate_sha256": sha(baseline_raw),
        "parent_revision": baseline["parent_revision"],
        "historical_fix_revision": baseline["historical_fix_revision"],
        "verifier_v2_sha256": v2_hash,
        "upstream_selection_sha256": sha(selection),
        "mutants": mutants,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in REPORT_NAMES:
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in KINDS:
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--baseline-reports", type=Path, required=True)
    parser.add_argument("--baseline-upstream", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--upstream-reports", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(reduce_pilot(
        {name: getattr(args, name) for name in REPORT_NAMES},
        args.baseline_reports, args.baseline_upstream,
        {name: getattr(args, name) for name in KINDS},
        args.reports, args.upstream_reports), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
