"""Recheck a saved development patch in a clean, committed parent copy.

This accepts a tracked Git diff only. It is not the complete-checkout evaluator
required for confirmation and must not be used to score untracked additions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def command(args, cwd, input_bytes=None):
    return subprocess.run(args, cwd=cwd, input=input_bytes, capture_output=True,
                          check=False)


def evaluate(parent, patch_bytes, verifier, python):
    parent = Path(parent)
    verifier = Path(verifier)
    if not parent.is_dir() or (parent / ".git").exists():
        raise ValueError("parent must be a directory without Git metadata")
    if not verifier.is_file() or not patch_bytes:
        raise ValueError("verifier and nonempty patch are required")
    result = {"schema": "solcodex.development-patch-evaluation.v1",
              "patch_sha256": hashlib.sha256(patch_bytes).hexdigest(),
              "patch_bytes": len(patch_bytes), "tracked_patch_only": True,
              "applies": False, "diff_check_exit": None,
              "verifier_exit": None, "verifier_summary": None}
    with tempfile.TemporaryDirectory(prefix="sol-development-eval-") as temporary:
        work = Path(temporary) / "parent"
        shutil.copytree(parent, work, symlinks=True)
        for args in (["git", "init", "-q"], ["git", "add", "-A"],
                     ["git", "-c", "user.name=Experiment",
                      "-c", "user.email=experiment@example.invalid",
                      "-c", "commit.gpgsign=false", "commit", "-qm", "Pre-fix fixture"]):
            step = command(args, work)
            if step.returncode:
                raise RuntimeError("could not create clean parent Git baseline")
        if command(["git", "apply", "--check", "--binary", "-"], work,
                   patch_bytes).returncode:
            return result
        step = command(["git", "apply", "--binary", "-"], work, patch_bytes)
        if step.returncode:
            return result
        result["applies"] = True
        result["diff_check_exit"] = command(["git", "diff", "--check"], work).returncode
        checked = command([str(python), "-B", str(verifier.resolve()), str(work)], work)
        result["verifier_exit"] = checked.returncode
        lines = checked.stdout.decode("utf-8", "replace").splitlines()
        result["verifier_summary"] = (lines[-1] if lines and re.fullmatch(
            r"TOTAL [0-9]+/[0-9]+", lines[-1]) else None)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--verifier", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.parent, args.patch.read_bytes(),
                              args.verifier, args.python), sort_keys=True))


if __name__ == "__main__":
    main()
