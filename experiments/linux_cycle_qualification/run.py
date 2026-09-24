#!/usr/bin/env python3
"""Fault-injected, secret-free Linux worker/checkpoint/accounting qualification."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

from experiments.action_fusion.external_verify import CASES
from experiments.action_fusion.replay_behavior import (
    IMAGE, invoke, one, parent_qualified, sha, witness_qualified)
from experiments.linux_cycle_qualification.reconcile import (
    ReconciliationLedger, read_provider_journal)
from scripts.usage_attempt_ledger import AttemptLedger


REPO = Path(__file__).resolve().parents[2]
BASE = REPO / "experiments/action_fusion"
HERE = Path(__file__).resolve().parent
PARENT_SHA256 = "cba812ea43e3005bc1937a88d5696bdcbb6ef011c4e6ea11589651cd7ee8f0b2"
GOLD_SHA256 = "9896aa3d83c745a7e91c32256cfe1c4cdd93a89c6a14c0268ca8ecb267cdc26f"
SCENARIOS = ("normal", "worker_killed", "broker_killed", "timeout_checkpoint",
             "retry", "duplicate", "conflict", "no_completion",
             "reconciliation_unavailable", "ledger_write_failure")


def command(*argv: str, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError("subprocess failed: " + argv[0] + " " + argv[1])
    return result


def docker(*args: str, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
    return command("docker", *args, timeout=timeout, check=check)


def wait_for(predicate, label: str, seconds: float = 15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise RuntimeError("event not observed: " + label)


def http_json(port: int, path: str, *, post: bool = False) -> dict:
    request = Request(f"http://127.0.0.1:{port}{path}", data=b"" if post else None,
                      method="POST" if post else "GET")
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def provider_row(path: Path, attempt_id: str) -> dict | None:
    return next((row for row in read_provider_journal(path)
                 if row["attempt_id"] == attempt_id), None)


def local_state(path: Path, attempt_id: str) -> str | None:
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT state FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
    return row[0] if row else None


def worker_logs(name: str) -> list[dict]:
    result = docker("logs", name, check=False)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def confirmed_absent(kind: str, name: str) -> bool:
    result = docker(kind, "inspect", name, check=False)
    if result.returncode == 0:
        return False
    message = result.stderr + result.stdout
    if kind == "container":
        return "No such object" in message or "No such container" in message
    return ("not found" in message or "No such network" in message) and name in message


def evaluate_behavior(checkpoint: Path) -> dict:
    parent = one(BASE / "fixture/duration.py")
    gold = one(HERE / "../action_fusion/nested_smoke/results/valid/duration.py")
    saved = one(checkpoint / "duration.py")
    manifest = json.loads((BASE / "controls_manifest.json").read_text())
    cases = {name: (invocation, expected) for name, invocation, expected in CASES}
    rejected = 0
    for name, entry in manifest["controls"].items():
        if name == "reference":
            continue
        source = BASE / "controls" / name / "duration.py"
        if sha(source.read_bytes()) != entry["source_sha256"]:
            raise ValueError("wrong-fix control source changed")
        invocation, expected = cases[entry["witness_case"]]
        actual, valid = invoke(source, invocation)
        rejected += witness_qualified(actual, valid, expected, entry["witness_observed_wire"])
    if (not parent_qualified(parent) or not gold["accepted"] or not saved["accepted"] or
            gold["source_sha256"] != GOLD_SHA256 or
            saved["source_sha256"] != GOLD_SHA256 or rejected != len(manifest["controls"]) - 1):
        raise RuntimeError("external behavior qualification failed")
    return {"parent_passed": parent["passed"], "parent_total": parent["total"],
            "gold_passed": gold["passed"], "checkpoint_passed": saved["passed"],
            "wrong_fix_witnesses_rejected": rejected,
            "wrong_fix_witnesses_total": len(manifest["controls"]) - 1}


def run(output: Path) -> int:
    report: dict = {"schema": "solcodex.linux-cycle-development.v1",
                    "model_requests": 0, "provider_billing_complete": False,
                    "target_worker_qualified": False, "scenarios": {}}
    containers: list[str] = []
    networks: list[str] = []
    upstream: subprocess.Popen[str] | None = None
    stage = "start"
    try:
        if sys.platform != "linux" or os.uname().machine != "x86_64":
            raise RuntimeError("Linux x86-64 required")
        if os.geteuid() == 0:
            raise RuntimeError("non-root controller required")
        protocol_raw = (HERE / "protocol.json").read_bytes()
        protocol = json.loads(protocol_raw)
        if (protocol.get("schema") != "solcodex.linux-cycle-qualification-protocol.v1" or
                protocol.get("status") != "development-predeclared" or
                protocol.get("image") != IMAGE or
                protocol.get("scenarios_in_order") != list(SCENARIOS) or
                protocol.get("model_requests") != 0 or
                protocol.get("usage") != {"input_tokens": 120, "output_tokens": 20,
                                          "cached_input_tokens": 80} or
                sha((BASE / "fixture/duration.py").read_bytes()) != PARENT_SHA256 or
                sha((BASE / "nested_smoke/results/valid/duration.py").read_bytes()) !=
                GOLD_SHA256):
            raise RuntimeError("protocol or fixture changed")
        report["protocol_sha256"] = sha(protocol_raw)
        docker("pull", IMAGE, timeout=240)
        image = json.loads(docker("image", "inspect", IMAGE).stdout)[0]
        if image["Os"] != "linux" or image["Architecture"] != "amd64":
            raise RuntimeError("pinned image architecture differs")
        report["image"] = IMAGE
        report["code_sha256"] = {path.name: sha(path.read_bytes()) for path in
                                 (HERE / "run.py", HERE / "mock_upstream.py",
                                  HERE / "mock_broker.py", HERE / "scripted_worker.py",
                                  HERE / "reconcile.py", REPO / "scripts/usage_attempt_ledger.py",
                                  BASE / "external_verify.py", BASE / "replay_behavior.py")}
        with tempfile.TemporaryDirectory(prefix="sol-linux-cycle-") as temporary:
            root = Path(temporary)
            root.chmod(0o755)
            host = root / "host"
            host.mkdir()
            upstream_db = host / "upstream.sqlite3"
            stage = "upstream"
            upstream = subprocess.Popen(
                [sys.executable, "-B", "-m",
                 "experiments.linux_cycle_qualification.mock_upstream", "--host", "0.0.0.0",
                 "--port", "0", "--db", str(upstream_db)],
                cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if not select.select([upstream.stdout], [], [], 10)[0]:
                raise RuntimeError("upstream readiness timed out")
            ready = json.loads(upstream.stdout.readline())
            if ready.get("status") != "ready":
                raise RuntimeError("upstream not ready")
            port = ready["port"]
            if http_json(port, "/health") != {"status": "ok"}:
                raise RuntimeError("upstream health failed")
            suffix = hashlib.sha256(os.urandom(16)).hexdigest()[:10]
            agent_net = "sol-cycle-" + suffix + "-agent"
            egress_net = "sol-cycle-" + suffix + "-egress"
            stage = "networks"
            networks.append(agent_net)
            docker("network", "create", "--driver", "bridge", "--internal",
                   "--opt", "com.docker.network.bridge.gateway_mode_ipv4=isolated", agent_net)
            networks.append(egress_net)
            docker("network", "create", "--driver", "bridge", egress_net)
            network = json.loads(docker("network", "inspect", egress_net).stdout)[0]
            gateway = network["IPAM"]["Config"][0]["Gateway"]
            canaries = {}
            for key in ("gold", "sibling", "host", "fake_auth"):
                path = host / (key + ".txt")
                path.write_text(key + "\n")
                canaries[key] = path
            report["checks"] = {"upstream_live": True,
                                "provider_journal_outside_worker": True}
            for index, scenario in enumerate(SCENARIOS):
                stage = scenario
                work = root / (scenario + "-work")
                work.mkdir(mode=0o777)
                work.chmod(0o777)
                shutil.copy2(BASE / "fixture/duration.py", work / "duration.py")
                (work / "duration.py").chmod(0o666)
                ledger_dir = root / (scenario + "-ledger")
                ledger_dir.mkdir(mode=0o777)
                ledger_dir.chmod(0o777)
                ledger_path = ledger_dir / "attempts.sqlite3"
                read_only_ledger = scenario == "ledger_write_failure"
                if read_only_ledger:
                    precreated = AttemptLedger(ledger_path)
                    precreated.close()
                broker_name = "sol-cycle-" + suffix + "-broker-" + str(index)
                worker_name = "sol-cycle-" + suffix + "-worker-" + str(index)
                before = {item["attempt_id"] for item in http_json(port, "/state")["requests"]}
                stage = scenario + ":broker"
                broker_argv = [
                    "run", "-d", "--name", broker_name, "--network", agent_net,
                    "--network-alias", "broker", "--user",
                    str(os.getuid()) + ":" + str(os.getgid()), "--read-only",
                    "--cap-drop=ALL", "--security-opt", "no-new-privileges:true",
                    "--pids-limit", "64", "--cpus", "2", "--memory", "2g",
                    "--memory-swap", "2g", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
                    "--mount", f"type=bind,source={HERE / 'mock_broker.py'},target=/opt/mock_broker.py,readonly",
                    "--mount", f"type=bind,source={REPO / 'scripts'},target=/opt/scripts,readonly",
                    "--mount", f"type=bind,source={ledger_dir},target=/ledger" +
                    (",readonly" if read_only_ledger else ""),
                    "--env", "PYTHONPATH=/opt", "--env", "PYTHONDONTWRITEBYTECODE=1",
                    IMAGE, "python", "-B", "/opt/mock_broker.py", "--upstream-host", gateway,
                    "--upstream-port", str(port), "--db", "/ledger/attempts.sqlite3",
                ]
                containers.append(broker_name)
                docker(*broker_argv)
                docker("network", "connect", egress_net, broker_name)
                wait_for(lambda: docker("exec", broker_name, "python", "-B", "-c",
                                        "import urllib.request;assert urllib.request.urlopen("
                                        "'http://127.0.0.1:8000/health',timeout=1).read()==b'OK'",
                                        timeout=3, check=False).returncode == 0,
                         "broker readiness")
                positive = docker("exec", broker_name, "python", "-B", "-c",
                                  "import urllib.request;assert urllib.request.urlopen("
                                  f"'http://{gateway}:{port}/health',timeout=2).status==200",
                                  timeout=5, check=False)
                if positive.returncode != 0:
                    raise RuntimeError("broker-to-upstream positive control failed")
                stage = scenario + ":worker"
                worker_argv = [
                    "run", "-d", "--name", worker_name, "--network", agent_net,
                    "--user", "65534:65534", "--read-only", "--cap-drop=ALL",
                    "--security-opt", "no-new-privileges:true", "--pids-limit", "64",
                    "--cpus", "2", "--memory", "2g", "--memory-swap", "2g",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
                    "--mount", f"type=bind,source={work},target=/work",
                    "--mount", f"type=bind,source={HERE / 'scripted_worker.py'},target=/opt/worker.py,readonly",
                    "--env", "PYTHONDONTWRITEBYTECODE=1",
                    "--env", "GOLD_CANARY=" + str(canaries["gold"]),
                    "--env", "SIBLING_CANARY=" + str(canaries["sibling"]),
                    "--env", "HOST_CANARY=" + str(canaries["host"]),
                    "--env", "FAKE_AUTH_CANARY=" + str(canaries["fake_auth"]),
                    "--env", "UPSTREAM_GATEWAY=" + gateway,
                    "--env", "UPSTREAM_PORT=" + str(port),
                    IMAGE, "python", "-B", "/opt/worker.py", scenario,
                ]
                containers.append(worker_name)
                docker(*worker_argv)
                inspected = json.loads(docker("inspect", worker_name).stdout)[0]
                mounts = {item["Destination"] for item in inspected["Mounts"]
                          if item["Type"] == "bind"}
                config = inspected["HostConfig"]
                if (mounts != {"/work", "/opt/worker.py"} or
                        set(inspected["NetworkSettings"]["Networks"]) != {agent_net} or
                        config["ReadonlyRootfs"] is not True or
                        config["Privileged"] is not False or
                        "ALL" not in config["CapDrop"] or
                        "no-new-privileges:true" not in config["SecurityOpt"] or
                        config["Memory"] != 2 * 1024**3 or
                        config["NanoCpus"] != 2_000_000_000):
                    raise RuntimeError("worker container isolation differs")
                if scenario == "ledger_write_failure":
                    exit_code = int(docker("wait", worker_name, timeout=15).stdout.strip())
                    logs = worker_logs(worker_name)
                    error = next((item for item in logs
                                  if item.get("phase") == "request_error"), None)
                    local = AttemptLedger(ledger_path)
                    try:
                        local_attempts = local.summary()["attempts"]
                    finally:
                        local.close()
                    if (exit_code != 3 or {item["attempt_id"] for item in
                                           http_json(port, "/state")["requests"]} != before or
                            error != {"phase": "request_error", "type": "HTTPError",
                                      "http_status": 503} or local_attempts != 0):
                        raise RuntimeError("write refusal dispatched upstream")
                    report["scenarios"][scenario] = {
                        "worker_exit": exit_code, "accepted_attempts": 0,
                        "http_status": 503, "fail_closed_before_dispatch": True}
                    continue
                if scenario == "retry":
                    seen = set(before)
                    second_worker = worker_name + "-retry"
                    for retry_index in range(2):
                        pending = wait_for(lambda: [item["attempt_id"] for item in
                                                    http_json(port, "/state")["requests"]
                                                    if item["attempt_id"] not in seen],
                                           "retry attempt accepted")
                        if len(pending) != 1:
                            raise RuntimeError("retry dispatch count invalid")
                        attempt = pending[0]
                        seen.add(attempt)
                        accepted = provider_row(upstream_db, attempt)
                        if accepted is None or accepted["state"] != "accepted":
                            raise RuntimeError("retry acceptance not durable")
                        reconciliation = ReconciliationLedger(ledger_path)
                        try:
                            reconciliation.reconcile(accepted)
                        finally:
                            reconciliation.close()
                        if retry_index == 0:
                            docker("kill", worker_name)
                            first_exit = int(docker("wait", worker_name, timeout=15).stdout.strip())
                            if first_exit != 137:
                                raise RuntimeError("first retry worker was not killed")
                        http_json(port, "/release/" + quote(attempt), post=True)
                        completed = wait_for(
                            lambda: (row if (row := provider_row(upstream_db, attempt))
                                     and row["state"] == "completed" else None),
                            "retry completion")
                        wait_for(lambda: local_state(ledger_path, attempt) == "completed",
                                 "retry local completion")
                        reconciliation = ReconciliationLedger(ledger_path)
                        try:
                            reconciliation.reconcile(completed)
                        finally:
                            reconciliation.close()
                        if retry_index == 0:
                            second_argv = worker_argv.copy()
                            second_argv[3] = second_worker
                            second_argv[-1] = "normal"
                            containers.append(second_worker)
                            docker(*second_argv)
                    exit_code = int(docker("wait", second_worker, timeout=15).stdout.strip())
                    logs = worker_logs(second_worker)
                    preflight = next((item for item in logs if item.get("phase") == "preflight"), None)
                    if (exit_code != 0 or preflight is None or
                            not all(preflight["checks"].values()) or
                            sha((work / "duration.py").read_bytes()) != GOLD_SHA256):
                        raise RuntimeError("retry worker changed behavior or containment")
                    reconciliation = ReconciliationLedger(ledger_path)
                    try:
                        summary = reconciliation.summary()
                    finally:
                        reconciliation.close()
                    if (summary["attempts"] != 2 or
                            summary["states"]["locally_completed"] != 2 or
                            summary["mock_observed_usage"] !=
                            {"input_tokens": 240, "output_tokens": 40,
                             "cached_input_tokens": 160} or
                            summary["mock_accounting_complete"] is not True):
                        raise RuntimeError("retry attempts were not counted separately")
                    report["scenarios"][scenario] = {
                        "first_worker_exit": first_exit, "retry_worker_exit": exit_code,
                        "accepted_attempts": 2,
                        "source_sha256": GOLD_SHA256, "worker_canaries_passed": True,
                        "reconciliation": summary}
                    docker("rm", "-f", worker_name, check=False)
                    docker("rm", "-f", second_worker, check=False)
                    docker("rm", "-f", broker_name, check=False)
                    continue
                stage = scenario + ":acceptance"
                new = wait_for(lambda: [item["attempt_id"] for item in
                                       http_json(port, "/state")["requests"]
                                       if item["attempt_id"] not in before],
                               "durable provider acceptance")
                if len(new) != 1:
                    raise RuntimeError("unexpected attempt count")
                attempt = new[0]
                accepted = provider_row(upstream_db, attempt)
                if accepted is None or accepted["state"] != "accepted":
                    raise RuntimeError("accepted provider record not durable")
                if scenario != "reconciliation_unavailable":
                    stage = scenario + ":accepted_reconciliation"
                    reconciliation = ReconciliationLedger(ledger_path)
                    try:
                        reconciliation.reconcile(accepted)
                    finally:
                        reconciliation.close()
                if scenario == "timeout_checkpoint":
                    stage = scenario + ":checkpoint"
                    wait_for(lambda: (work / ".checkpoint_ready").is_file(),
                             "workspace checkpoint")
                stage = scenario + ":fault_injection"
                if scenario in {"worker_killed", "timeout_checkpoint"}:
                    docker("kill", worker_name)
                targeted_worker_kill = scenario in {"worker_killed", "timeout_checkpoint"}
                if scenario == "broker_killed":
                    docker("kill", broker_name)
                http_json(port, "/release/" + quote(attempt), post=True)
                stage = scenario + ":provider_journal"
                expected_provider = "accepted" if scenario == "no_completion" else "completed"
                record = wait_for(lambda: (row if (row := provider_row(upstream_db, attempt))
                                        and row["state"] == expected_provider else None),
                                  "provider final journal")
                expected_local = ("pending" if scenario == "broker_killed" else
                                  "unknown" if scenario == "no_completion" else "completed")
                stage = scenario + ":local_ledger"
                wait_for(lambda: local_state(ledger_path, attempt) == expected_local,
                         "local ledger state")
                stage = scenario + ":worker_exit"
                exit_code = int(docker("wait", worker_name, timeout=15).stdout.strip())
                expected_exits = ({137} if targeted_worker_kill else
                                  {3, 4} if scenario == "broker_killed" else
                                  {4} if scenario == "no_completion" else {0})
                if exit_code not in expected_exits:
                    raise RuntimeError("worker exit differs from scenario")
                logs = worker_logs(worker_name)
                preflight = next((item for item in logs if item.get("phase") == "preflight"), None)
                if preflight is None or not all(preflight["checks"].values()):
                    raise RuntimeError("worker containment checks failed")
                if scenario in {"broker_killed", "no_completion"} and any(
                        item.get("phase") == "finished" and item.get("completion_seen")
                        for item in logs):
                    raise RuntimeError("failed request was reported as completed")
                stage = scenario + ":completed_reconciliation"
                reconciliation = ReconciliationLedger(ledger_path)
                try:
                    if scenario != "reconciliation_unavailable":
                        reconciliation.reconcile(record)
                    summary = reconciliation.summary()
                    observations = reconciliation.db.execute(
                        "SELECT count(*) FROM provider_reconciliations WHERE attempt_id=?",
                        (attempt,)).fetchone()[0]
                finally:
                    reconciliation.close()
                if (summary["attempts"] != 1 or
                        summary["mock_accounting_complete"] is not
                        (scenario not in {"no_completion", "reconciliation_unavailable"}) or
                        observations != (0 if scenario == "reconciliation_unavailable" else
                                         1 if scenario == "no_completion" else 2) or
                        summary["provider_billing_complete"] is not False):
                    raise RuntimeError("reconciliation completeness invalid")
                stage = scenario + ":checkpoint_integrity"
                source = (work / "duration.py").read_bytes()
                expected_source = (GOLD_SHA256 if scenario in {"normal", "timeout_checkpoint"}
                                   else PARENT_SHA256)
                if sha(source) != expected_source:
                    raise RuntimeError("checkpoint source differs from expected")
                if scenario == "timeout_checkpoint":
                    if (set(path.name for path in work.iterdir()) !=
                            {"duration.py", ".checkpoint_ready", "forged-evaluation.json"} or
                            json.loads((work / "forged-evaluation.json").read_text()) !=
                            {"accepted": False}):
                        raise RuntimeError("checkpoint shape changed")
                if scenario == "conflict" and not (ledger_dir / ("conflict-" + attempt)).is_file():
                    raise RuntimeError("conflicting completion not rejected")
                report["scenarios"][scenario] = {
                    "worker_exit": exit_code, "provider_state": record["state"],
                    "local_state": expected_local, "source_sha256": sha(source),
                    "targeted_worker_kill": targeted_worker_kill,
                    "worker_canaries_passed": True, "reconciliation": summary,
                    "provider_observations": observations,
                    "conflict_rejected": scenario == "conflict"}
                docker("rm", "-f", worker_name, check=False)
                docker("rm", "-f", broker_name, check=False)
            stage = "external_evaluation"
            report["behavior"] = evaluate_behavior(root / "timeout_checkpoint-work")
            report["checks"]["checkpoint_evaluated_outside_worker"] = True
            report["checks"]["forged_worker_result_ignored"] = True
            report["checks"]["all_scenarios_passed"] = len(report["scenarios"]) == len(SCENARIOS)
            if not all(report["checks"].values()):
                raise RuntimeError("cycle checks incomplete")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        report["failure"] = {"stage": stage, "type": type(error).__name__}
    finally:
        cleanup = True
        for name in reversed(containers):
            try:
                docker("rm", "-f", name, check=False)
                cleanup = confirmed_absent("container", name) and cleanup
            except (OSError, subprocess.TimeoutExpired):
                cleanup = False
        for name in reversed(networks):
            try:
                docker("network", "rm", name, check=False)
                cleanup = confirmed_absent("network", name) and cleanup
            except (OSError, subprocess.TimeoutExpired):
                cleanup = False
        if upstream is not None and upstream.poll() is None:
            upstream.terminate()
            try:
                upstream.wait(timeout=5)
            except subprocess.TimeoutExpired:
                upstream.kill()
                upstream.wait(timeout=5)
        report["cleanup_complete"] = cleanup and (upstream is None or upstream.poll() is not None)
    report["passed"] = "failure" not in report and report["cleanup_complete"]
    if "failure" in report:
        print("::error title=Linux cycle qualification::" +
              report["failure"]["stage"] + ":" + report["failure"]["type"])
    elif not report["cleanup_complete"]:
        print("::error title=Linux cycle qualification::cleanup_incomplete")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.output))
