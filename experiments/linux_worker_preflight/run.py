#!/usr/bin/env python3
"""Secret-free Linux containment preflight; never an agent or billing run."""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import urlopen


IMAGE_TAG = "python:3.12-slim"
MEMORY_LIMIT = 2 * 1024**3
EXPECTED_AGENT_CHECKS = frozenset({
    "broker_reachable", "workspace_readable", "workspace_writable",
    "leak_slot_denied", "gold_denied", "sibling_denied", "host_denied",
    "fake_auth_denied", "symlink_denied", "proc_root_denied",
    "docker_socket_absent", "ci_tokens_absent", "nonroot_uid",
    "egress_sidecar_denied", "host_service_denied",
})


def command(*args: str, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError("command failed")
    return result


def docker_json(*args: str) -> dict:
    value = json.loads(command("docker", *args).stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError("unexpected Docker inspect result")
    return value[0]


def host_memory_bytes() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    raise ValueError("host memory total unavailable")


def probe_output(result: subprocess.CompletedProcess[str]) -> dict:
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise ValueError("probe did not emit one JSON report")
    report = json.loads(lines[0])
    if report.get("schema") != "solcodex.linux-agent-probe.v1":
        raise ValueError("wrong agent probe schema")
    checks = report.get("checks")
    if (not isinstance(checks, dict) or set(checks) != EXPECTED_AGENT_CHECKS or
            any(type(v) is not bool for v in checks.values())):
        raise ValueError("missing or invalid agent checks")
    if report.get("passed") is not all(checks.values()):
        raise ValueError("agent check total inconsistent")
    return report


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")


class HostControl(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/host-ok":
            self.send_error(404)
            return
        payload = b"HOST_OK\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        pass


def run(output: Path) -> int:
    report: dict = {
        "schema": "solcodex.linux-containment-development.v1",
        "model_requests": 0,
        "provider_billing_complete": False,
        "target_worker_qualified": False,
        "checks": {},
    }
    containers: list[str] = []
    networks: list[str] = []
    server: ThreadingHTTPServer | None = None
    server_thread: threading.Thread | None = None
    stage = "start"
    try:
        if sys.platform != "linux" or os.uname().machine != "x86_64":
            raise RuntimeError("Linux x86-64 required")
        report["runner"] = {
            "kernel": os.uname().release,
            "machine": os.uname().machine,
            "available_cpus": len(os.sched_getaffinity(0)),
            "host_memory_bytes": host_memory_bytes(),
        }
        stage = "docker_version"
        report["docker_server_version"] = command(
            "docker", "version", "--format", "{{.Server.Version}}").stdout.strip()
        security_options = json.loads(command(
            "docker", "info", "--format", "{{json .SecurityOptions}}").stdout)
        report["checks"]["daemon_seccomp_enabled"] = any(
            "seccomp" in option for option in security_options)
        stage = "image_pull"
        command("docker", "pull", IMAGE_TAG, timeout=240)
        image = docker_json("image", "inspect", IMAGE_TAG)
        digests = [item for item in image.get("RepoDigests", [])
                   if item.startswith("python@sha256:")]
        if len(digests) != 1 or image.get("Os") != "linux" or image.get("Architecture") != "amd64":
            raise ValueError("image digest or architecture unavailable")
        image_ref = digests[0]
        report["image"] = {"reference": image_ref, "id": image["Id"],
                           "architecture": image["Architecture"]}
        probe_path = Path(__file__).with_name("agent_probe.py").resolve()
        report["probe_sha256"] = hashlib.sha256(probe_path.read_bytes()).hexdigest()
        report["controller_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="sol-linux-preflight-") as tmpname:
            root = Path(tmpname)
            root.chmod(0o755)
            dirs = {key: root / key for key in
                    ("agent", "gold", "sibling", "host", "broker", "egress")}
            for directory in dirs.values():
                directory.mkdir()
            dirs["agent"].chmod(0o777)
            for key in ("gold", "sibling", "host"):
                dirs[key].chmod(0o755)
            (dirs["agent"] / "visible.txt").write_text("VISIBLE\n")
            (dirs["agent"] / "leak-slot.txt").write_text("EMPTY\n")
            canaries = {key: dirs[key] / "canary.txt" for key in
                        ("gold", "sibling", "host")}
            for key, path in canaries.items():
                path.write_text("GOLD\n" if key == "gold" else secrets.token_hex(16) + "\n")
                path.chmod(0o644)
            fake_auth = dirs["host"] / "fake-auth.txt"
            fake_auth.write_text(secrets.token_hex(16) + "\n")
            fake_auth.chmod(0o644)
            (dirs["agent"] / "gold-link").symlink_to(canaries["gold"])
            (dirs["broker"] / "broker-ok.txt").write_text("BROKER_OK\n")
            (dirs["egress"] / "egress-ok.txt").write_text("EGRESS_OK\n")
            for key in ("broker", "egress"):
                dirs[key].chmod(0o755)
            server = ThreadingHTTPServer(("0.0.0.0", 0), HostControl)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            host_port = server.server_port
            with urlopen(f"http://127.0.0.1:{host_port}/host-ok", timeout=2) as response:
                report["checks"]["host_control_live"] = response.read() == b"HOST_OK\n"
            prefix = "sol-preflight-" + secrets.token_hex(4)
            agent_net, egress_net = prefix + "-agent", prefix + "-egress"
            stage = "isolated_network"
            networks.append(agent_net)
            command("docker", "network", "create", "--driver", "bridge", "--internal",
                    "--opt", "com.docker.network.bridge.gateway_mode_ipv4=isolated",
                    agent_net)
            network = docker_json("network", "inspect", agent_net)
            report["checks"]["isolated_network_config"] = (
                network.get("Internal") is True and
                network.get("Options", {}).get(
                    "com.docker.network.bridge.gateway_mode_ipv4") == "isolated" and
                network.get("EnableIPv6") is False)
            stage = "egress_network"
            networks.append(egress_net)
            command("docker", "network", "create", "--driver", "bridge", egress_net)
            egress_network = docker_json("network", "inspect", egress_net)
            host_ip = egress_network["IPAM"]["Config"][0].get("Gateway")
            if not host_ip:
                raise ValueError("egress bridge has no host service address")

            def start_server(name: str, network_name: str, directory: Path,
                             alias: str) -> None:
                containers.append(name)
                command("docker", "run", "-d", "--name", name, "--network", network_name,
                        "--network-alias", alias,
                        "--user", "65534:65534", "--read-only", "--cap-drop=ALL",
                        "--security-opt", "no-new-privileges:true",
                        "--mount", f"type=bind,source={directory},target=/srv,readonly",
                        "--env", "PYTHONDONTWRITEBYTECODE=1", image_ref,
                        "python", "-B", "-m", "http.server", "8000", "-d", "/srv")

            stage = "mock_services"
            egress_name, broker_name = prefix + "-egress", prefix + "-broker"
            start_server(egress_name, egress_net, dirs["egress"], "egress")
            start_server(broker_name, agent_net, dirs["broker"], "broker")
            command("docker", "network", "connect", egress_net, broker_name)
            egress_info = docker_json("inspect", egress_name)
            egress_ip = egress_info["NetworkSettings"]["Networks"][egress_net]["IPAddress"]
            for _ in range(20):
                readiness = command("docker", "exec", broker_name, "python", "-B", "-c",
                                    "import urllib.request; assert urllib.request.urlopen("
                                    "'http://egress:8000/egress-ok.txt',timeout=2).read() == "
                                    "b'EGRESS_OK\\n'; assert urllib.request.urlopen("
                                    f"'http://{host_ip}:{host_port}/host-ok',timeout=2).read() == "
                                    "b'HOST_OK\\n'", timeout=5, check=False)
                if readiness.returncode == 0:
                    break
                time.sleep(0.25)
            report["checks"]["broker_egress_control"] = readiness.returncode == 0
            report["checks"]["host_service_positive_control"] = readiness.returncode == 0
            if not report["checks"]["broker_egress_control"]:
                raise RuntimeError("broker egress control failed")

            common = [
                "docker", "run", "--name", "PLACEHOLDER", "--network", agent_net,
                "--user", "65534:65534", "--read-only", "--cap-drop=ALL",
                "--security-opt", "no-new-privileges:true", "--pids-limit", "64",
                "--cpus", "2", "--memory", "2g", "--memory-swap", "2g",
                "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
                "--mount", f"type=bind,source={dirs['agent']},target=/work",
                "--mount", f"type=bind,source={probe_path},target=/opt/probe.py,readonly",
            ]
            variables = {
                "GOLD_CANARY": str(canaries["gold"]),
                "SIBLING_CANARY": str(canaries["sibling"]),
                "HOST_CANARY": str(canaries["host"]),
                "FAKE_AUTH_CANARY": str(fake_auth),
                "EGRESS_IP": egress_ip,
                "HOST_CONTROL_IP": host_ip,
                "HOST_CONTROL_PORT": str(host_port),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            for key, value in variables.items():
                common.extend(("--env", key + "=" + value))

            def agent_run(name: str, extra_mount: str | None = None) -> tuple[subprocess.CompletedProcess[str], dict]:
                argv = common.copy()
                argv[3] = name
                if extra_mount:
                    argv.extend(("--mount", extra_mount))
                argv.extend((image_ref, "python", "-B", "/opt/probe.py"))
                containers.append(name)
                result = command(*argv, timeout=90, check=False)
                return result, probe_output(result)

            stage = "positive_agent"
            agent_name = prefix + "-agent"
            positive, observation = agent_run(agent_name)
            report["agent_checks"] = observation["checks"]
            report["unqualified_observations"] = observation.get("unqualified_observations")
            report["checks"]["positive_agent_passed"] = (
                positive.returncode == 0 and observation["passed"] is True)
            inspected = docker_json("inspect", agent_name)
            host_config = inspected["HostConfig"]
            bind_targets = {mount["Destination"] for mount in inspected["Mounts"]
                            if mount["Type"] == "bind"}
            report["checks"]["agent_configuration"] = (
                host_config["NetworkMode"] == agent_net and
                set(inspected["NetworkSettings"]["Networks"]) == {agent_net} and
                host_config["ReadonlyRootfs"] is True and
                host_config["Privileged"] is False and
                "ALL" in host_config["CapDrop"] and
                "no-new-privileges:true" in host_config["SecurityOpt"] and
                inspected["Config"]["User"] == "65534:65534" and
                host_config["Memory"] == MEMORY_LIMIT and
                host_config["MemorySwap"] == MEMORY_LIMIT and
                host_config["PidsLimit"] == 64 and
                host_config["NanoCpus"] == 2_000_000_000 and
                bind_targets == {"/work", "/opt/probe.py"} and
                inspected["Image"] == image["Id"])
            report["resource_limits"] = {
                "cpus": 2, "memory_bytes": host_config["Memory"],
                "memory_swap_bytes": host_config["MemorySwap"],
                "pids": host_config["PidsLimit"], "tmpfs_bytes": 64 * 1024**2,
            }
            stage = "negative_control"
            negative, negative_report = agent_run(
                prefix + "-leak-control",
                f"type=bind,source={canaries['gold']},target=/work/leak-slot.txt,readonly")
            failed_keys = {key for key, value in negative_report["checks"].items() if not value}
            report["checks"]["deliberate_leak_detected"] = (
                negative.returncode == 1 and negative_report["passed"] is False and
                failed_keys == {"leak_slot_denied"})
            report["checks"]["forbidden_canaries_live"] = all(
                path.is_file() for path in (*canaries.values(), fake_auth))
            if not all(report["checks"].values()):
                raise RuntimeError("one or more containment checks failed")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired,
            KeyError, TypeError, json.JSONDecodeError) as error:
        report["failure"] = {"stage": stage, "type": type(error).__name__}
    finally:
        cleanup = True
        for name in reversed(containers):
            try:
                command("docker", "rm", "-f", name, timeout=15, check=False)
                absent = command("docker", "container", "inspect", name,
                                 timeout=15, check=False).returncode != 0
                cleanup = cleanup and absent
            except (OSError, subprocess.TimeoutExpired):
                cleanup = False
        for name in reversed(networks):
            try:
                command("docker", "network", "rm", name, timeout=15, check=False)
                absent = command("docker", "network", "inspect", name,
                                 timeout=15, check=False).returncode != 0
                cleanup = cleanup and absent
            except (OSError, subprocess.TimeoutExpired):
                cleanup = False
        if containers or networks:
            try:
                cleanup = cleanup and command("docker", "info", timeout=15,
                                              check=False).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                cleanup = False
        if server is not None:
            server.shutdown()
            server.server_close()
        if server_thread is not None:
            server_thread.join(timeout=5)
            cleanup = cleanup and not server_thread.is_alive()
        report["checks"]["cleanup_complete"] = cleanup
        report["passed"] = "failure" not in report and all(report["checks"].values())
        write_report(output, report)
        print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    return run(parser.parse_args().output)


if __name__ == "__main__":
    raise SystemExit(main())
