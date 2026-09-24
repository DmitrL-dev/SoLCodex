"""Build the captured packaging checkout as a wheel without network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


WHEEL = re.compile(r"^packaging-[A-Za-z0-9_.+-]+-py3-none-any\.whl$")
FLIT_SITE = Path(os.environ.get("SOL_PACKAGING_SCRATCH", "/tmp")) / "flit-site"


def run(source: Path, backend_wheel: Path, output: Path, epoch: int) -> dict:
    report: dict = {"schema": "solcodex.packaging-wheel-build.v1",
                    "backend_install_ok": False, "build_ok": False,
                    "wheel": None}
    if list(output.iterdir()):
        report["failure_stage"] = "output_not_empty"
        return report
    try:
        installed = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
             "--no-compile", "--disable-pip-version-check", "--target", str(FLIT_SITE),
             str(backend_wheel)], capture_output=True, timeout=120, check=False)
        report["backend_install_ok"] = installed.returncode == 0
        if not report["backend_install_ok"]:
            report["failure_stage"] = "backend_install"
            return report
        env = os.environ.copy()
        env.update({"PYTHONPATH": str(FLIT_SITE), "PIP_NO_INDEX": "1",
                    "PIP_DISABLE_PIP_VERSION_CHECK": "1", "SOURCE_DATE_EPOCH": str(epoch),
                    "PYTHONDONTWRITEBYTECODE": "1"})
        build = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps",
             "--no-build-isolation", "--wheel-dir", str(output), str(source)],
            cwd="/tmp", env=env, capture_output=True, timeout=180, check=False)
        if build.returncode != 0:
            report["failure_stage"] = "wheel_build"
            return report
        wheels = list(output.iterdir())
        if len(wheels) != 1 or not wheels[0].is_file() or not WHEEL.fullmatch(wheels[0].name):
            report["failure_stage"] = "wheel_shape"
            return report
        raw = wheels[0].read_bytes()
        if len(raw) > 5 * 1024 * 1024:
            report["failure_stage"] = "wheel_size"
            return report
        report["build_ok"] = True
        report["wheel"] = {"name": wheels[0].name,
                           "bytes": len(raw),
                           "sha256": hashlib.sha256(raw).hexdigest()}
        return report
    except (OSError, subprocess.TimeoutExpired):
        report["failure_stage"] = "build_runtime"
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--backend-wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.backend_wheel, args.output,
                         args.source_date_epoch), sort_keys=True))


if __name__ == "__main__":
    main()
