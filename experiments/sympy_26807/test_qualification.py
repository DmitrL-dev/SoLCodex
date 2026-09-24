"""Reject contradictory private evidence before publishing fixed-field aggregates."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from experiments.sympy_26807.reduce_qualification import (
    DEPENDENCIES, V1_IDS, V2_IDS, read_behavior, read_upstream, sha,
)


EXPERIMENT = Path(__file__).resolve().parent


def assertion_ids(version):
    tree = ast.parse((EXPERIMENT / f"verifier_{version}.py").read_text())
    return [node.args[0].value for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and
            node.func.id == "case" and node.args and
            isinstance(node.args[0], ast.Constant)]


class QualificationReducerTests(unittest.TestCase):
    def test_pinned_assertion_inventory(self):
        for version, expected in (("v1", V1_IDS), ("v2", V2_IDS)):
            ids = assertion_ids(version)
            self.assertEqual(sha(json.dumps(ids, separators=(",", ":")).encode()), expected)

    def test_behavior_report_rejects_missing_or_infrastructure_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            path = Path(directory) / "report.json"
            ids = assertion_ids("v1")
            verifier_hash = hashlib.sha256((EXPERIMENT / "verifier_v1.py").read_bytes()).hexdigest()
            report = {"schema": "sympy-26807-development-verifier-v1",
                      "metadata": {"verifier_sha256": verifier_hash,
                                   "source_key_sha256": "a" * 64,
                                   "source_root": str(root),
                                   "sympy_import_path": str(root / "sympy/__init__.py"),
                                   "python": "3.12.13 synthetic",
                                   "python_executable": str(root / "venv/bin/python"),
                                   "platform": "synthetic", "machine": "synthetic",
                                   "recursion_limit": 1000, "sympy_version": "1.14.dev",
                                   "dependencies": DEPENDENCIES},
                      "results": [{"id": item, "category": "control", "status": "pass"}
                                  for item in ids],
                      "passed": 35, "failed": 0, "total": 35, "error_types": {}}
            path.write_text(json.dumps(report))
            result, actual_ids, _ = read_behavior(path, root, verifier_hash, "a" * 64, "v1")
            self.assertEqual(result["passed"], 35)
            self.assertEqual(actual_ids, ids)
            report["results"].pop()
            path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                read_behavior(path, root, verifier_hash, "a" * 64, "v1")
            report["results"][0]["error_type"] = "MemoryError"
            report["error_types"] = {"MemoryError": 1}
            path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                read_behavior(path, root, verifier_hash, "a" * 64, "v1")
            report["results"][0] = {"id": ids[0], "category": "control", "status": "pass"}
            report.update(passed=35, failed=0, error_types={})
            del report["metadata"]["recursion_limit"]
            path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                read_behavior(path, root, verifier_hash, "a" * 64, "v1")
            report["results"] = [{"id": item, "category": "control", "status": "pass"}
                                 for item in ids]
            report["results"][0].update(status="fail", error_type="CheckTimeout")
            report.update(passed=34, failed=1, error_types={"CheckTimeout": 1})
            path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                read_behavior(path, root, verifier_hash, "a" * 64, "v1")

    def test_upstream_manifest_and_junit_must_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            source.mkdir()
            imported = source / "sympy/__init__.py"
            imported.parent.mkdir()
            imported.write_text("\n")
            nodes = [f"test_{index}" for index in range(24)]
            selection_hash = "b" * 64
            python = str(base / "venv/bin/python")
            xml = base / "m1-upstream.xml"
            suite = ET.Element("testsuite", tests="48", failures="0", errors="0", skipped="0")
            inventory = json.loads((EXPERIMENT / "upstream_inventory.json").read_text())
            for classname, name in inventory:
                ET.SubElement(suite, "testcase", classname=classname, name=name)
            ET.ElementTree(suite).write(xml)
            stdout, stderr = base / "m1-upstream.stdout", base / "m1-upstream.stderr"
            stdout.write_text("48 passed")
            stderr.write_text("")
            command = [python, "-I", "-B", "-m", "pytest", "-p", "no:cacheprovider",
                       "-q", "-o", "addopts=", "--junitxml=" + str(xml), *nodes]
            manifest = {"source_root": str(source), "source_export_sha256": "a" * 64,
                        "selection_sha256": selection_hash, "selection_count": 24,
                        "pytest_environment_sanitized": True,
                        "python_executable": python, "import_preflight_path": str(imported),
                        "exit_code": 0, "junit_sha256": sha(xml.read_bytes()),
                        "stdout_sha256": sha(stdout.read_bytes()),
                        "stderr_sha256": sha(stderr.read_bytes()), "pytest_argv": command}
            manifest_path = base / "m1-upstream-run.json"
            manifest_path.write_text(json.dumps(manifest))
            result, ids = read_upstream(xml, source, "a" * 64,
                                        selection_hash, nodes, python)
            self.assertEqual(result["passed"], 48)
            self.assertEqual(len(ids), 48)
            manifest["source_export_sha256"] = "c" * 64
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                read_upstream(xml, source, "a" * 64, selection_hash, nodes, python)
            manifest["source_export_sha256"] = "a" * 64
            manifest_path.write_text(json.dumps(manifest))
            first = next(suite.iter("testcase"))
            first.set("name", "test_unselected")
            ET.ElementTree(suite).write(xml)
            manifest["junit_sha256"] = sha(xml.read_bytes())
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                read_upstream(xml, source, "a" * 64, selection_hash, nodes, python)
            first.set("name", inventory[0][1])
            suite.set("failures", "1")
            ET.ElementTree(suite).write(xml)
            manifest["junit_sha256"] = sha(xml.read_bytes())
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                read_upstream(xml, source, "a" * 64, selection_hash, nodes, python)


if __name__ == "__main__":
    unittest.main()
