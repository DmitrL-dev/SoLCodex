#!/usr/bin/env python3
"""Qualify a stopped, complete packaging checkout with offline Linux evaluation."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse
from urllib.request import urlopen

from experiments.action_fusion.replay_behavior import IMAGE
from experiments.packaging_checkpoint.snapshot import capture, manifest


REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
VERIFIER = REPO / "experiments/packaging_928/verify_packaging_frozen.py"
SCENARIOS = ("parent", "historical_fix", "alternate_checkpoint_1",
             "alternate_checkpoint_2", "wrong_symptom_only_with_forged_tests",
             "alternate_without_helper")
NESTED = frozenset({"nested-single", "nested-whitespace", "nested-and-or",
                    "nested-with", "nested-ref", "metadata-nested"})
EXPECTED = {
    "nested-single": "((MIT))",
    "nested-whitespace": "((MIT))",
    "nested-and-or": "((MIT AND (Apache-2.0 OR BSD-2-Clause)))",
    "nested-with": "((GPL-2.0-only WITH Classpath-exception-2.0))",
    "nested-ref": "((LicenseRef-Custom))",
    "metadata-nested": "((MIT))",
    "simple": "MIT",
    "single-parens": "(MIT)",
    **{f"invalid-{index}": None for index in range(1, 7)},
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def judge_observations(observations: dict) -> dict:
    """Keep expected outputs outside the candidate evaluator container."""
    if not isinstance(observations, dict) or set(observations) != set(EXPECTED):
        raise ValueError("case inventory differs")
    vector = {}
    for case, expected in EXPECTED.items():
        item = observations[case]
        if not isinstance(item, dict):
            raise ValueError("invalid case observation")
        if expected is None:
            vector[case] = item == {"kind": "exception", "type": "InvalidLicenseExpression"}
        else:
            vector[case] = item == {"kind": "value", "type": "str", "value": expected}
    return {"passed": sum(vector.values()), "total": len(EXPECTED), "vector": vector}


def read_regular(path: Path, maximum: int) -> bytes:
    """Read a bounded regular file without following its final symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ValueError("expected bounded regular file")
        chunks = []
        remaining = maximum + 1
        while remaining:
            part = os.read(fd, min(remaining, 65536))
            if not part:
                break
            chunks.append(part)
            remaining -= len(part)
        after = os.fstat(fd)
        if (remaining == 0 or (before.st_dev, before.st_ino, before.st_size) !=
                (after.st_dev, after.st_ino, after.st_size)):
            raise ValueError("file grew or changed during bounded read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def command(*argv: str, cwd: Path | None = None, timeout: int = 60,
            check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                            timeout=timeout, check=False)
    if check and result.returncode != 0:
        raise RuntimeError("subprocess failed: " + argv[0] + " " + argv[1])
    return result


def docker(*args: str, timeout: int = 60,
           check: bool = True) -> subprocess.CompletedProcess[str]:
    return command("docker", *args, timeout=timeout, check=check)


def wait_for(predicate, label: str, seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise RuntimeError("event not observed: " + label)


def protocol() -> tuple[dict, str]:
    raw = (HERE / "protocol.json").read_bytes()
    data = json.loads(raw)
    if (data.get("schema") != "solcodex.packaging-checkpoint-development-protocol.v1" or
            data.get("status") != "development-predeclared" or
            data.get("image") != IMAGE or
            data.get("scenarios_in_order") != list(SCENARIOS) or
            data.get("model_requests") != 0 or
            data.get("provider_billing_complete") is not False or
            data.get("target_worker_qualified") is not False or
            sha(VERIFIER.read_bytes()) != data.get("frozen_behavior_verifier_sha256")):
        raise ValueError("protocol or verifier changed")
    return data, sha(raw)


def download_wheels(data: dict, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    names = set()
    for entry in data["wheels"]:
        name = entry["filename"]
        if (name in names or Path(name).name != name or
                urlparse(entry["url"]).hostname != "files.pythonhosted.org"):
            raise ValueError("wheel manifest invalid")
        names.add(name)
        with urlopen(entry["url"], timeout=30) as response:
            raw = response.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024 or sha(raw) != entry["sha256"]:
            raise ValueError("wheel digest differs")
        (destination / name).write_bytes(raw)
    if len(names) != 5:
        raise ValueError("wheel count changed")


def export_sources(data: dict, root: Path) -> tuple[Path, Path, Path]:
    clone = root / "clone"
    command("git", "clone", "--quiet", "--filter=blob:none", "--no-checkout",
            data["repository"], str(clone), timeout=240)
    result = []
    for label, commit_key, tree_key in (
            ("parent", "parent_commit", "parent_tree"),
            ("historical_fix", "historical_fix_commit", "historical_fix_tree")):
        commit = data[commit_key]
        if command("git", "rev-parse", commit + "^{tree}", cwd=clone).stdout.strip() != data[tree_key]:
            raise ValueError("pinned Git tree differs")
        command("git", "checkout", "--quiet", "--detach", commit, cwd=clone, timeout=120)
        path = root / label
        shutil.copytree(clone, path, symlinks=True,
                        ignore=shutil.ignore_patterns(".git"))
        result.append(path)
    trusted_tests = root / "trusted-tests"
    shutil.copytree(result[1] / "tests", trusted_tests, symlinks=True)
    return result[0], result[1], trusted_tests


def make_writable(root: Path) -> None:
    root.chmod(root.stat().st_mode | 0o222)
    for path in root.rglob("*"):
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | 0o222)


def prepare_worker(parent: Path, root: Path, label: str) -> Path:
    work = root / (label + "-work")
    shutil.copytree(parent, work, symlinks=True)
    make_writable(work)
    control = work / "checkpoint-control"
    control.mkdir(mode=0o777)
    control.chmod(0o777)
    (control / "delete-me.txt").write_text("checkpoint deletion control\n")
    (control / "delete-me.txt").chmod(0o666)
    return work


def worker_checkpoint(work: Path, scenario: str, name: str,
                      containers: list[str]) -> dict:
    if scenario not in {"alternate", "wrong_symptom_only"}:
        raise ValueError("worker scenario invalid")
    argv = ("run", "-d", "--name", name, "--network", "none", "--user",
            "65534:65534", "--read-only", "--cap-drop=ALL", "--security-opt",
            "no-new-privileges:true", "--pids-limit", "64", "--cpus", "2",
            "--memory", "2g", "--memory-swap", "2g", "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m", "--mount",
            f"type=bind,source={work},target=/work", "--mount",
            f"type=bind,source={HERE / 'scripted_worker.py'},target=/opt/worker.py,readonly",
            "--env", "SCENARIO=" + scenario, IMAGE, "python", "-B", "/opt/worker.py")
    containers.append(name)
    docker(*argv, timeout=90)
    inspected = json.loads(docker("inspect", name).stdout)[0]
    mounts = {item["Destination"] for item in inspected["Mounts"]
              if item["Type"] == "bind"}
    config = inspected["HostConfig"]
    if (mounts != {"/work", "/opt/worker.py"} or
            config["NetworkMode"] != "none" or config["ReadonlyRootfs"] is not True or
            config["Privileged"] is not False or "ALL" not in config["CapDrop"] or
            "no-new-privileges:true" not in config["SecurityOpt"] or
            config["Memory"] != 2 * 1024**3 or
            config["NanoCpus"] != 2_000_000_000 or
            inspected["Config"]["User"] != "65534:65534"):
        raise RuntimeError("worker isolation configuration differs")
    marker = work / ".checkpoint-ready"
    wait_for(lambda: os.path.lexists(marker), "worker checkpoint")
    docker("kill", name)
    exit_code = int(docker("wait", name, timeout=30).stdout.strip())
    stopped = json.loads(docker("inspect", name).stdout)[0]
    if exit_code != 137 or stopped["State"]["Running"] is not False:
        raise RuntimeError("worker was not stopped before capture")
    if read_regular(marker, 128) != (scenario + "\n").encode():
        raise RuntimeError("worker checkpoint marker differs")
    logs = docker("logs", name, check=False).stdout.splitlines()
    if {"phase": "checkpoint_ready", "scenario": scenario} not in [
            json.loads(line) for line in logs if line.strip()]:
        raise RuntimeError("worker did not report checkpoint")
    return {"worker_exit": exit_code, "worker_stopped_before_capture": True,
            "worker_isolation_checked": True}


def docker_base(name: str, tmpfs: str) -> tuple[str, ...]:
    return ("run", "--rm", "--name", name, "--network", "none", "--user",
            str(os.getuid()) + ":" + str(os.getgid()), "--read-only",
            "--cap-drop=ALL", "--security-opt", "no-new-privileges:true",
            "--pids-limit", "64", "--cpus", "2", "--memory", "2g",
            "--memory-swap", "2g", "--tmpfs", tmpfs,
            "--env", "PYTHONDONTWRITEBYTECODE=1", IMAGE)


def build_and_evaluate(snapshot: Path, label: str, deps: Path,
                       trusted_tests: Path, root: Path, epoch: int,
                       suffix: str, containers: list[str]) -> dict:
    output = root / (label + "-wheel")
    output.mkdir(mode=0o700)
    build_name = "sol-packaging-" + suffix + "-build-" + label.replace("_", "-")
    build_mounts = ("--mount", f"type=bind,source={snapshot},target=/candidate,readonly",
                    "--mount", f"type=bind,source={deps},target=/deps,readonly",
                    "--mount", f"type=bind,source={output},target=/out",
                    "--mount", f"type=bind,source={HERE / 'build_wheel.py'},target=/opt/build.py,readonly")
    base = docker_base(build_name, "/tmp:rw,nosuid,nodev,size=512m")
    # Insert mounts before the pinned image and its command.
    containers.append(build_name)
    built = docker(*base[:-1], *build_mounts, base[-1], "python", "-B", "/opt/build.py",
                   "--source", "/candidate", "--backend-wheel",
                   "/deps/flit_core-3.12.0-py3-none-any.whl", "--output", "/out",
                   "--source-date-epoch", str(epoch), timeout=360)
    build_report = json.loads(built.stdout)
    if not build_report.get("build_ok"):
        raise RuntimeError("offline wheel build failed: " +
                           str(build_report.get("failure_stage", "unknown")))
    wheel_name = build_report["wheel"]["name"]
    if (not isinstance(wheel_name, str) or Path(wheel_name).name != wheel_name or
            not wheel_name.startswith("packaging-") or not wheel_name.endswith(".whl") or
            len(list(output.iterdir())) != 1):
        raise RuntimeError("built wheel directory differs")
    wheel = output / wheel_name
    raw = read_regular(wheel, 25 * 1024 * 1024)
    if (sha(raw) != build_report["wheel"]["sha256"] or
            len(raw) != build_report["wheel"]["bytes"]):
        raise RuntimeError("built wheel report differs from file")
    verified = root / (label + "-verified-wheel")
    verified.mkdir(mode=0o700)
    (verified / wheel_name).write_bytes(raw)
    shared_mounts = ("--mount", f"type=bind,source={verified},target=/wheel,readonly",
                     "--mount", f"type=bind,source={deps},target=/deps,readonly",
                     "--mount", f"type=bind,source={HERE / 'verify_installed.py'},target=/opt/verify.py,readonly")
    shared_argv = ("python", "-B", "/opt/verify.py", "--wheel", "/wheel/" + wheel_name,
                   "--deps", "/deps")
    observe_name = "sol-packaging-" + suffix + "-observe-" + label.replace("_", "-")
    base = docker_base(observe_name, "/tmp:rw,nosuid,nodev,size=512m")
    containers.append(observe_name)
    observed = docker(*base[:-1], *shared_mounts, base[-1], *shared_argv,
                      "--phase", "observations", timeout=540)
    evaluation = json.loads(observed.stdout)
    if (evaluation.get("schema") != "solcodex.packaging-installed-observations.v2" or
            evaluation.get("phase") != "observations"):
        raise RuntimeError("observation report schema differs")
    if evaluation.get("failure_stage") is not None:
        return {"build": build_report, "evaluation": evaluation}
    upstream_name = "sol-packaging-" + suffix + "-upstream-" + label.replace("_", "-")
    base = docker_base(upstream_name, "/tmp:rw,nosuid,nodev,size=512m")
    containers.append(upstream_name)
    upstream = docker(*base[:-1], *shared_mounts, "--mount",
                      f"type=bind,source={trusted_tests},target=/trusted/tests,readonly",
                      base[-1], *shared_argv, "--phase", "upstream",
                      "--trusted-tests", "/trusted/tests", timeout=540)
    upstream_report = json.loads(upstream.stdout)
    if (upstream_report.get("schema") != "solcodex.packaging-installed-observations.v2" or
            upstream_report.get("phase") != "upstream"):
        raise RuntimeError("upstream report schema differs")
    evaluation["upstream_install_ok"] = upstream_report.get("install_ok")
    evaluation["upstream_import_ok"] = upstream_report.get("import_ok")
    evaluation["upstream_exit"] = upstream_report.get("upstream_exit")
    evaluation["upstream_summary"] = upstream_report.get("upstream_summary")
    if upstream_report.get("failure_stage") is not None:
        evaluation["failure_stage"] = "upstream:" + str(upstream_report["failure_stage"])
    if evaluation.get("observations_complete") is True:
        evaluation["behavior"] = judge_observations(evaluation["observations"])
    return {"build": build_report, "evaluation": evaluation}


def check_controls(snapshot: Path, record: dict) -> None:
    entries = {item["path"]: item for item in record["entries"]}
    if ("checkpoint-control/delete-me.txt" in entries or
            entries.get("src/packaging/licenses/_parenthesis.py", {}).get("type") != "file" or
            entries.get("checkpoint-control/executable.sh", {}).get("mode", 0) & 0o111 != 0o111 or
            entries.get("checkpoint-control/readme-link", {}).get("target") != "../README.rst" or
            json.loads((snapshot / "checkpoint-control/forged-evaluation.json").read_text()) !=
            {"accepted": False}):
        raise RuntimeError("complete checkpoint controls differ")


def accepted(report: dict, expected: int | None = None) -> bool:
    observed = report["evaluation"]
    behavior = observed.get("behavior")
    upstream = observed.get("upstream_summary")
    return (report["build"]["build_ok"] is True and
            observed.get("install_ok") is True and
            observed.get("import_ok") is True and
            observed.get("upstream_install_ok") is True and
            observed.get("upstream_import_ok") is True and
            observed.get("failure_stage") is None and
            observed.get("observations_complete") is True and
            isinstance(behavior, dict) and behavior["total"] == 14 and
            isinstance(upstream, dict) and upstream.get("tests", 0) > 0 and
            upstream.get("errors") == 0 and upstream.get("skipped") == 0 and
            observed.get("upstream_exit") in (0, 1) and
            (expected is None or behavior["passed"] == expected))


def absent(name: str) -> bool:
    inspection = docker("container", "inspect", name, check=False)
    return (inspection.returncode != 0 and
            ("No such object" in inspection.stderr or
             "No such container" in inspection.stderr))


@contextmanager
def managed_workspace(containers: list[str], cleanup_state: dict):
    with tempfile.TemporaryDirectory(prefix="sol-packaging-checkpoint-") as temporary:
        try:
            yield Path(temporary)
        finally:
            for name in reversed(containers):
                try:
                    docker("rm", "-f", name, check=False)
                    cleanup_state["complete"] = absent(name) and cleanup_state["complete"]
                except (OSError, subprocess.TimeoutExpired):
                    cleanup_state["complete"] = False


def run(output: Path) -> int:
    report: dict = {"schema": "solcodex.packaging-checkpoint-development.v1",
                    "model_requests": 0, "provider_billing_complete": False,
                    "target_worker_qualified": False, "scenarios": {}}
    containers: list[str] = []
    cleanup_state = {"complete": True}
    stage = "start"
    try:
        if sys.platform != "linux" or os.uname().machine != "x86_64" or os.geteuid() == 0:
            raise RuntimeError("nonroot Linux x86-64 runner required")
        data, protocol_hash = protocol()
        report["protocol_sha256"] = protocol_hash
        report["image"] = IMAGE
        report["code_sha256"] = {path.name: sha(path.read_bytes()) for path in
                                 (HERE / "run.py", HERE / "snapshot.py", HERE / "scripted_worker.py",
                                  HERE / "build_wheel.py", HERE / "verify_installed.py", VERIFIER)}
        stage = "image"
        docker("pull", IMAGE, timeout=240)
        image = json.loads(docker("image", "inspect", IMAGE).stdout)[0]
        if image["Os"] != "linux" or image["Architecture"] != "amd64":
            raise RuntimeError("pinned image architecture differs")
        with managed_workspace(containers, cleanup_state) as root:
            stage = "dependencies"
            deps = root / "deps"
            download_wheels(data, deps)
            stage = "source_export"
            parent, gold, trusted_tests = export_sources(data, root)
            suffix = sha(os.urandom(16))[:10]
            snapshots: dict[str, tuple[Path, dict]] = {}
            for label, source in (("parent", parent), ("historical_fix", gold)):
                stage = label + ":snapshot"
                destination = root / (label + "-snapshot")
                record = capture(source, destination)
                if manifest(destination) != record:
                    raise RuntimeError("source snapshot differs")
                snapshots[label] = destination, record
            for label, scenario in (("alternate_checkpoint_1", "alternate"),
                                    ("alternate_checkpoint_2", "alternate"),
                                    ("wrong_symptom_only_with_forged_tests", "wrong_symptom_only")):
                stage = label + ":worker"
                work = prepare_worker(parent, root, label)
                worker_name = "sol-packaging-" + suffix + "-worker-" + label.replace("_", "-")
                worker = worker_checkpoint(work, scenario, worker_name, containers)
                stage = label + ":snapshot"
                destination = root / (label + "-snapshot")
                record = capture(work, destination)
                if record != manifest(work) or record != manifest(destination):
                    raise RuntimeError("stopped worker checkpoint differs")
                if scenario == "alternate":
                    check_controls(destination, record)
                elif (json.loads((destination / "checkpoint-control/forged-evaluation.json").read_text()) !=
                      {"accepted": True}):
                    raise RuntimeError("wrong-fix forged report differs")
                snapshots[label] = destination, record
                report["scenarios"][label] = worker
                docker("rm", "-f", worker_name, check=False)
            first = snapshots["alternate_checkpoint_1"][1]["tree_sha256"]
            second = snapshots["alternate_checkpoint_2"][1]["tree_sha256"]
            if first != second:
                raise RuntimeError("independent alternate checkpoints differ")
            stage = "alternate_without_helper:snapshot"
            missing_source = root / "alternate-without-helper-work"
            shutil.copytree(snapshots["alternate_checkpoint_1"][0], missing_source,
                            symlinks=True)
            (missing_source / "src/packaging/licenses/_parenthesis.py").unlink()
            missing_destination = root / "alternate_without_helper-snapshot"
            missing_record = capture(missing_source, missing_destination)
            snapshots["alternate_without_helper"] = missing_destination, missing_record
            for label in SCENARIOS:
                stage = label + ":offline_build_and_evaluation"
                destination, record = snapshots[label]
                results = build_and_evaluate(destination, label, deps, trusted_tests,
                                             root, data["source_date_epoch"], suffix, containers)
                report["scenarios"].setdefault(label, {})
                report["scenarios"][label].update({
                    "tree_sha256": record["tree_sha256"],
                    "entries": len(record["entries"]),
                    "wheel": results["build"]["wheel"],
                    "evaluation": results["evaluation"],
                })
                if label == "parent":
                    behavior = results["evaluation"].get("behavior")
                    if (not accepted(results, 8) or
                            any(behavior["vector"][case] for case in NESTED) or
                            results["evaluation"]["upstream_exit"] != 1 or
                            results["evaluation"]["upstream_summary"]["failures"] < 1):
                        raise RuntimeError("parent negative control differs")
                elif label in {"historical_fix", "alternate_checkpoint_1",
                               "alternate_checkpoint_2"}:
                    if (not accepted(results, 14) or
                            results["evaluation"]["upstream_exit"] != 0 or
                            results["evaluation"]["upstream_summary"]["failures"] != 0):
                        raise RuntimeError("accepted repair behavior differs")
                elif label == "wrong_symptom_only_with_forged_tests":
                    behavior = results["evaluation"].get("behavior")
                    if (not accepted(results) or behavior["passed"] >= 14 or
                            behavior["vector"]["nested-and-or"] is not False):
                        raise RuntimeError("known wrong fix was not rejected")
                else:
                    observed = results["evaluation"]
                    if (results["build"]["build_ok"] is not True or
                            observed.get("install_ok") is not True or
                            observed.get("import_ok") is not False or
                            observed.get("failure_stage") != "case_import" or
                            observed.get("import_error_type") != "ModuleNotFoundError" or
                            observed.get("import_error_module") !=
                            "packaging.licenses._parenthesis"):
                        raise RuntimeError("missing helper did not fail at installed import")
            first_eval = report["scenarios"]["alternate_checkpoint_1"]["evaluation"]
            second_eval = report["scenarios"]["alternate_checkpoint_2"]["evaluation"]
            if (first_eval["behavior"] != second_eval["behavior"] or
                    first_eval["upstream_exit"] != second_eval["upstream_exit"]):
                raise RuntimeError("independent alternate behavior differs")
            gold_inventory = report["scenarios"]["historical_fix"]["evaluation"]["upstream_summary"]["inventory"]
            gold_ids = {(item["classname"], item["name"]) for item in gold_inventory}
            if len(gold_ids) != len(gold_inventory):
                raise RuntimeError("trusted upstream inventory has duplicates")
            for label in SCENARIOS[:-1]:
                inventory = report["scenarios"][label]["evaluation"]["upstream_summary"]["inventory"]
                if {(item["classname"], item["name"]) for item in inventory} != gold_ids:
                    raise RuntimeError("trusted upstream inventory differs: " + label)
            report["checks"] = {"source_commits_pinned": True,
                                "offline_wheels_hash_pinned": True,
                                "worker_stopped_before_capture": True,
                                "complete_checkpoint_roundtrip": True,
                                "installed_wheel_external_behavior": True,
                                "trusted_upstream_tests_external": True,
                                "trusted_upstream_inventory_equal": True,
                                "wrong_fix_and_forged_tests_rejected": True,
                                "alternative_helper_material": True,
                                "two_replays_equal": True}
    except (OSError, ValueError, RuntimeError, KeyError, TypeError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        report["failure"] = {"stage": stage, "type": type(error).__name__}
    cleanup = cleanup_state["complete"]
    report["cleanup_complete"] = cleanup
    report["passed"] = "failure" not in report and cleanup
    if "failure" in report:
        print("::error title=Packaging checkpoint qualification::" +
              report["failure"]["stage"] + ":" + report["failure"]["type"])
    elif not cleanup:
        print("::error title=Packaging checkpoint qualification::cleanup_incomplete")
    else:
        aggregate = {"schema": "solcodex.packaging-checkpoint-development-aggregate.v1",
                     "protocol_sha256": report["protocol_sha256"],
                     "scenarios_passed": len(report["scenarios"]),
                     "parent_passed": report["scenarios"]["parent"]["evaluation"]["behavior"]["passed"],
                     "gold_passed": report["scenarios"]["historical_fix"]["evaluation"]["behavior"]["passed"],
                     "alternate_passed": [report["scenarios"][label]["evaluation"]["behavior"]["passed"]
                                          for label in ("alternate_checkpoint_1", "alternate_checkpoint_2")],
                     "wrong_fix_passed": report["scenarios"]["wrong_symptom_only_with_forged_tests"]["evaluation"]["behavior"]["passed"],
                     "alternate_tree_sha256": first,
                     "model_requests": 0, "provider_billing_complete": False,
                     "target_worker_qualified": False}
        print("::notice title=Packaging checkpoint development aggregate::" +
              json.dumps(aggregate, sort_keys=True, separators=(",", ":")))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.output))
