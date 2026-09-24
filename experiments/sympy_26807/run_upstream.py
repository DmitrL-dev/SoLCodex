"""Run the pinned upstream selection on one private SymPy source export."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

try:
    from experiments.sympy_26807.materialize_variants import export_digest
except ModuleNotFoundError:
    from materialize_variants import export_digest


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--name", required=True,
                        choices=("parent", "gold", "m1", "alt"))
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    script_dir = Path(__file__).resolve().parent
    selection_bytes = (script_dir / "upstream_selection.json").read_bytes()
    nodes = json.loads(selection_bytes)
    if (not isinstance(nodes, list) or len(nodes) != 24 or
            len(set(nodes)) != 24 or any(not isinstance(node, str) for node in nodes)):
        raise ValueError("upstream selection is not pinned")
    if args.report_dir.resolve().is_relative_to(script_dir.parents[1]):
        raise ValueError("private test reports must be outside public repository")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    xml = args.report_dir / f"{args.name}-upstream.xml"
    manifest = args.report_dir / f"{args.name}-upstream-run.json"
    stdout = args.report_dir / f"{args.name}-upstream.stdout"
    stderr = args.report_dir / f"{args.name}-upstream.stderr"
    if any(path.exists() for path in (xml, manifest, stdout, stderr)):
        raise FileExistsError("upstream report path already exists")
    interpreter = args.python.resolve(strict=True)
    preflight = subprocess.run(
        [str(args.python), "-I", "-B", "-c",
         "import pathlib,sys;sys.path.insert(0,sys.argv[1]);import sympy;"
         "print(pathlib.Path(sympy.__file__).resolve())", str(source)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    imported = Path(preflight.stdout.strip()).resolve(strict=True)
    if not imported.is_relative_to(source):
        raise ValueError("SymPy import preflight used a different checkout")
    command = [str(args.python), "-I", "-B", "-m", "pytest", "-p",
               "no:cacheprovider", "-q", "-o", "addopts=",
               "--junitxml=" + str(xml), *nodes]
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("PYTEST_") and key not in
           {"PYTHONPATH", "PYTHONHOME", "PYTHONINSPECT", "PYTHONSTARTUP"}}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    try:
        run = subprocess.run(command, cwd=source, env=env, capture_output=True,
                             text=True, timeout=180, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("upstream pytest timed out; qualification incomplete") from exc
    stdout.write_text(run.stdout)
    stderr.write_text(run.stderr)
    data = {"source_root": str(source), "source_export_sha256": export_digest(source)[1],
            "python_executable": str(args.python),
            "interpreter_resolved": str(interpreter),
            "import_preflight_path": str(imported),
            "selection_sha256": sha(selection_bytes), "selection_count": len(nodes),
            "pytest_environment_sanitized": True,
            "pytest_argv": command, "exit_code": run.returncode,
            "junit_sha256": sha(xml.read_bytes()) if xml.exists() else None,
            "stdout_sha256": sha(run.stdout.encode()),
            "stderr_sha256": sha(run.stderr.encode())}
    manifest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"exit_code": run.returncode, "junit_present": xml.exists()}))
    return run.returncode


if __name__ == "__main__":
    raise SystemExit(main())
