"""Audit exposed SymPy #26807 verifier and source-variant reports.

Private checkout paths, raw errors, and JUnit output are never published.
This is a development verifier probe, not a held-out repair or cost estimate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

try:
    from experiments.sympy_26807.materialize_variants import (
        PARENT_EXPORT, SOURCE_FILE, export_digest, variant,
    )
except ModuleNotFoundError:
    from materialize_variants import PARENT_EXPORT, SOURCE_FILE, export_digest, variant


GOLD_EXPORT = "31b282f6b5c61f241f7126ceadf6d54ca31848d9888aa8978d502bda890c4c36"
V1_IDS = "58eb637f9a793182de6bf5183af923c3131d688c9bc3885632f7e2055bf71585"
V2_IDS = "ccf007cebb02583679b7c479a5102c637f6262f880c5d16df783c230dbbfb45f"
UPSTREAM_IDS = "a819dce14cae8240a912ccd293e328475b5293e1c55a5d31dc3c9e3c54237ee6"
PARENT_FAILURES = {f"R{index:02d}" for index in range(1, 18)}
M1_FAILURES = {"C19_zero_array_iterable_preserved",
               "C20_one_array_iterable_preserved"}
PARENT_ERROR_TYPES = {"R03": "TypeError", "R07": "TypeError", "R15": "TypeError",
                      "R16": "AssertionError", "R17": "AssertionError"}
DEPENDENCIES = {"hypothesis": "6.108.8", "mpmath": "1.3.0",
                "numpy": "1.26.4", "pytest": "8.2.2"}
REPORT_NAMES = {
    "parent": {"v1": "parent_v1_source.json", "v2": "parent_v2_source.json"},
    "gold": {"v1": "gold_v1_source.json", "v2": "gold_v2_source.json"},
    "superclass": {"v1": "m1_v1_public.json", "v2": "m1_v2_public.json"},
    "alternative": {"v1": "alt_v1_public.json", "v2": "alt_v2_public.json"},
}
JUNIT_NAMES = {"parent": "parent-upstream.xml", "gold": "gold-upstream.xml",
               "superclass": "m1-upstream.xml", "alternative": "alt-upstream.xml"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_manifest(roots: dict[str, Path]) -> dict:
    hashes = {name: export_digest(root) for name, root in roots.items()}
    if hashes["parent"] != (2061, PARENT_EXPORT) or hashes["gold"] != (2061, GOLD_EXPORT):
        raise ValueError("parent or gold source export differs from pinned revisions")
    parent = (roots["parent"] / SOURCE_FILE).read_text()
    source_hashes = {name: sha((root / SOURCE_FILE).read_bytes())
                     for name, root in roots.items()}
    for name, kind in (("superclass", "superclass_noniterable"),
                       ("alternative", "array_symbol_property")):
        if hashes[name][0] != 2061:
            raise ValueError("variant source file count changed")
        expected = variant(parent, kind).encode()
        if (roots[name] / SOURCE_FILE).read_bytes() != expected:
            raise ValueError("source variant is not the declared one-file edit")
        # A full export digest is published; all other files must match parent.
        parent_rows = {p.relative_to(roots["parent"]).as_posix(): sha(p.read_bytes())
                       for p in roots["parent"].rglob("*") if p.is_file()}
        variant_rows = {p.relative_to(roots[name]).as_posix(): sha(p.read_bytes())
                        for p in roots[name].rglob("*") if p.is_file()}
        changed = {key for key in parent_rows.keys() | variant_rows.keys()
                   if parent_rows.get(key) != variant_rows.get(key)}
        if changed != {SOURCE_FILE.as_posix()}:
            raise ValueError("source variant changed other files")
    if len(set(source_hashes.values())) != 4:
        raise ValueError("parent, gold and variants are not distinct")
    return {name: {"export_sha256": hashes[name][1],
                   "source_key_sha256": source_hashes[name]}
            for name in roots}


def read_behavior(path: Path, root: Path, verifier_hash: str,
                  source_hash: str, version: str) -> tuple[dict, list[str], dict]:
    raw = path.read_bytes()
    report = json.loads(raw)
    metadata = report.get("metadata") or {}
    results = report.get("results")
    count = 35 if version == "v1" else 37
    if (report.get("schema") != "sympy-26807-development-verifier-v1" or
            metadata.get("verifier_sha256") != verifier_hash or
            metadata.get("source_key_sha256") != source_hash or
            not isinstance(metadata.get("source_root"), str) or
            Path(metadata["source_root"]).resolve() != root.resolve() or
            not isinstance(metadata.get("sympy_import_path"), str) or
            not Path(metadata["sympy_import_path"]).resolve().is_relative_to(root.resolve()) or
            not isinstance(metadata.get("python"), str) or
            not metadata["python"].startswith("3.12.13 ") or
            not isinstance(metadata.get("python_executable"), str) or
            not Path(metadata["python_executable"]).is_absolute() or
            not isinstance(metadata.get("platform"), str) or not metadata["platform"] or
            not isinstance(metadata.get("machine"), str) or not metadata["machine"] or
            metadata.get("recursion_limit") != 1000 or
            metadata.get("sympy_version") != "1.14.dev" or
            metadata.get("dependencies") != DEPENDENCIES or
            not isinstance(results, list) or len(results) != count or
            report.get("total") != count):
        raise ValueError("behavior report provenance or count invalid")
    ids = [item.get("id") for item in results]
    if (len(set(ids)) != count or
            any(not isinstance(item, str) or
                re.fullmatch(r"[RC][0-9]{2}_[a-z0-9_]+", item) is None for item in ids) or
            sha(json.dumps(ids, separators=(",", ":")).encode()) !=
            (V1_IDS if version == "v1" else V2_IDS)):
        raise ValueError("verifier assertion inventory changed")
    if any(item.get("status") not in {"pass", "fail"} for item in results):
        raise ValueError("unknown assertion status")
    failed = [item for item in results if item["status"] == "fail"]
    if (report.get("passed") != count - len(failed) or
            report.get("failed") != len(failed) or
            report.get("error_types") != dict(Counter(
                item.get("error_type") for item in failed)) or
            any(not isinstance(item.get("error_type"), str) or
                item["error_type"] in {"CheckTimeout", "ImportError",
                                       "ModuleNotFoundError", "MemoryError",
                                       "KeyboardInterrupt", "SystemExit", "OSError"}
                for item in failed)):
        raise ValueError("failed assertions or infrastructure state invalid")
    return {"passed": report["passed"], "total": count,
            "failed_ids": [item["id"] for item in failed],
            "private_report_sha256": sha(raw)}, ids, metadata


def read_upstream(path: Path, source: Path, source_hash: str,
                  selection_hash: str, nodes: list[str], python: str
                  ) -> tuple[dict, list[tuple[str, str]]]:
    raw = path.read_bytes()
    manifest_path = path.with_name(path.name.replace("-upstream.xml", "-upstream-run.json"))
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    if (not isinstance(manifest.get("source_root"), str) or
            Path(manifest["source_root"]).resolve() != source.resolve() or
            manifest.get("source_export_sha256") != source_hash or
            manifest.get("selection_sha256") != selection_hash or
            manifest.get("selection_count") != len(nodes) or
            manifest.get("pytest_environment_sanitized") is not True or
            manifest.get("python_executable") != python or
            not isinstance(manifest.get("import_preflight_path"), str) or
            not Path(manifest["import_preflight_path"]).resolve().is_relative_to(source.resolve()) or
            manifest.get("exit_code") != 0 or manifest.get("junit_sha256") != sha(raw) or
            manifest.get("pytest_argv") != [python, "-I", "-B", "-m", "pytest", "-p",
                                           "no:cacheprovider", "-q", "-o", "addopts=",
                                           "--junitxml=" + str(path), *nodes]):
        raise ValueError("upstream runner manifest differs from private result")
    for extension, field in (("stdout", "stdout_sha256"), ("stderr", "stderr_sha256")):
        output_path = path.with_name(path.name.replace(".xml", "." + extension))
        if manifest.get(field) != sha(output_path.read_bytes()):
            raise ValueError("upstream runner output digest mismatch")
    document = ET.fromstring(raw)
    suites = list(document.iter("testsuite"))
    tests = list(document.iter("testcase"))
    if len(suites) != 1 or len(tests) != 48:
        raise ValueError("upstream JUnit selection incomplete")
    suite = suites[0]
    if any(suite.get(key) != expected for key, expected in (
            ("tests", "48"), ("failures", "0"), ("errors", "0"), ("skipped", "0"))):
        raise ValueError("upstream JUnit suite did not pass")
    ids = [(test.get("classname"), test.get("name")) for test in tests]
    inventory = json.loads((Path(__file__).resolve().parent / "upstream_inventory.json").read_text())
    if (len(set(ids)) != 48 or
            any(not all(isinstance(part, str) and part for part in pair)
                for pair in ids) or
            sha(json.dumps(ids, separators=(",", ":")).encode()) != UPSTREAM_IDS or
            ids != [tuple(item) for item in inventory] or
            any(list(test) for test in tests)):
        raise ValueError("upstream JUnit testcase missing, duplicate or failed")
    return {"passed": 48, "total": 48, "private_junit_sha256": sha(raw),
            "private_run_manifest_sha256": sha(manifest_raw)}, ids


def reduce(roots: dict[str, Path], reports: Path, upstream_reports: Path) -> dict:
    source = source_manifest(roots)
    directory = Path(__file__).resolve().parent
    verifiers = {version: sha((directory / f"verifier_{version}.py").read_bytes())
                 for version in ("v1", "v2")}
    selection = (directory / "upstream_selection.json").read_bytes()
    inventory_bytes = (directory / "upstream_inventory.json").read_bytes()
    inventory = json.loads(inventory_bytes)
    if (not isinstance(inventory, list) or len(inventory) != 48 or
            sha(json.dumps(inventory, separators=(",", ":")).encode()) != UPSTREAM_IDS):
        raise ValueError("upstream test inventory changed")
    nodes = json.loads(selection)
    if not isinstance(nodes, list) or len(nodes) != 24 or len(set(nodes)) != 24:
        raise ValueError("upstream node selection changed")
    behavior = {}
    id_lists = {}
    environments = []
    for name in REPORT_NAMES:
        behavior[name] = {}
        for version, filename in REPORT_NAMES[name].items():
            result, ids, metadata = read_behavior(
                reports / filename, roots[name], verifiers[version],
                source[name]["source_key_sha256"], version)
            behavior[name][version] = result
            id_lists[(name, version)] = ids
            environments.append((metadata.get("python"), metadata.get("python_executable"),
                                 metadata.get("dependencies"),
                                 metadata.get("platform"), metadata.get("machine"),
                                 metadata.get("recursion_limit"), metadata.get("sympy_version")))
    if len(set(json.dumps(item, sort_keys=True) for item in environments)) != 1:
        raise ValueError("behavior runs used different recorded environments")
    if any(id_lists[(name, version)] != id_lists[("parent", version)]
           for name in REPORT_NAMES for version in ("v1", "v2")):
        raise ValueError("behavior assertion order changed across source variants")
    if id_lists[("parent", "v2")][:35] != id_lists[("parent", "v1")]:
        raise ValueError("v2 changed a v1 assertion rather than adding two controls")
    for name in REPORT_NAMES:
        for version in ("v1", "v2"):
            failed = behavior[name][version]["failed_ids"]
            expected = ([item for item in id_lists[(name, version)]
                         if item[:3] in PARENT_FAILURES] if name == "parent" else
                        [item for item in id_lists[(name, version)]
                         if item in M1_FAILURES] if name == "superclass" and version == "v2"
                        else [])
            if failed != expected:
                raise ValueError("behavior verdict differs from declared known-miss matrix")
            raw_report = json.loads((reports / REPORT_NAMES[name][version]).read_text())
            failure_rows = [item for item in raw_report["results"] if item["status"] == "fail"]
            if name == "parent":
                for item in failure_rows:
                    prefix = item["id"][:3]
                    if item.get("error_type") != PARENT_ERROR_TYPES.get(prefix, "RecursionError"):
                        raise ValueError("parent failed for an unexpected reason")
            elif name == "superclass" and version == "v2":
                for item in failure_rows:
                    expected_error = ("ZeroArray classification changed" if item["id"].startswith("C19")
                                      else "OneArray classification changed")
                    if (item.get("error_type") != "AssertionError" or
                            item.get("error") != expected_error):
                        raise ValueError("superclass mutant failed for an unexpected reason")
    upstream = {}
    upstream_ids = None
    for name, filename in JUNIT_NAMES.items():
        report, ids = read_upstream(upstream_reports / filename, roots[name],
                                    source[name]["export_sha256"], sha(selection),
                                    nodes, environments[0][1])
        if upstream_ids is None:
            upstream_ids = ids
        elif ids != upstream_ids:
            raise ValueError("upstream test set differs across source variants")
        upstream[name] = report
    return {"schema": "solcodex.sympy-26807-verifier-known-miss-development.v2",
            "exposed_development_task": True,
            "independent_quality_estimate": False,
            "sibling_controls_api_requirement_established": False,
            "superclass_patch_acceptability": "unresolved",
            "six_mutant_qualification_complete": False,
            "external_review_complete": False,
            "parent_revision": "530149cc7256a98c5963bcccc43cec19a9d04d09",
            "historical_fix_revision": "6760acef2209c9538a3f1b3a286b0275e9420b98",
            "source": source, "verifier_sha256": verifiers,
            "upstream_selection_sha256": sha(selection),
            "upstream_inventory_sha256": sha(inventory_bytes),
            "upstream_test_ids_sha256": sha(json.dumps(upstream_ids, separators=(",", ":")).encode()),
            "behavior": behavior, "upstream": upstream,
            "known_superclass_mutant_survived_v1": True,
            "known_superclass_mutant_rejected_v2": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in REPORT_NAMES:
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--upstream-reports", type=Path, required=True)
    args = parser.parse_args()
    roots = {name: getattr(args, name) for name in REPORT_NAMES}
    print(json.dumps(reduce(roots, args.reports, args.upstream_reports),
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
