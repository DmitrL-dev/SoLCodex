"""Synthetic counterexamples for the packaged-plugin request auditor."""
import hashlib
import io
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

try:
    from scripts.audit_packaged_plugin_request_probe import VERSION, inspect, main
except ModuleNotFoundError:
    from audit_packaged_plugin_request_probe import VERSION, inspect, main


class PackagedPluginAuditTests(unittest.TestCase):
    @staticmethod
    def make_fixture(root: Path, plugin: bool):
        marker = "HIDDEN_" + "a" * 24
        work, artifacts = root / "work", root / "artifacts"
        work.mkdir()
        artifacts.mkdir()
        fixture = f"value = {marker!r}\n".encode()
        (work / "test_probe.py").write_bytes(fixture)
        (work / "run-count.log").write_text("run\n")
        command = "/tmp/fixed-python3 -m unittest -v test_probe"
        prompt = f"Use the Bash command tool to run `{command}`. HIDDEN_ token if visible."
        argv = ["/Applications/ChatGPT.app/Contents/Resources/codex", "exec", "--json",
                "--ephemeral", "--skip-git-repo-check", "-s", "workspace-write",
                "-m", "gpt-6-sol", "-c", "model_reasoning_effort=medium",
                "-c", 'model_provider="local_probe"', "--enable", "code_mode",
                "--enable" if plugin else "--disable", "hooks",
                "--enable" if plugin else "--disable", "plugins",
                "--disable", "apps", "--disable", "browser_use", prompt]
        if plugin:
            argv.insert(-1, "--dangerously-bypass-hook-trust")
        manifest = {"argv": argv, "plugin_enabled": plugin,
                    "plugin_version": VERSION if plugin else None,
                    "plugin_hook_sha256": None, "plugin_config_sha256": None,
                    "driver_sha256": "b" * 64}
        if plugin:
            package = root / "market/plugins/sol-codex/.codex-plugin/plugin.json"
            cached_package = root / "home/plugins/cache/sol-codex/sol-codex" / VERSION / ".codex-plugin/plugin.json"
            package.parent.mkdir(parents=True)
            cached_package.parent.mkdir(parents=True)
            package.write_text(json.dumps({"name": "sol-codex", "version": VERSION}))
            cached_package.write_bytes(package.read_bytes())
            for relative, key in (("scripts/sol_hook.py", "plugin_hook_sha256"),
                                  ("hooks/hooks.json", "plugin_config_sha256")):
                source = root / "market/plugins/sol-codex" / relative
                cached = root / "home/plugins/cache/sol-codex/sol-codex" / VERSION / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                cached.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"synthetic plugin fixture " + relative.encode())
                cached.write_bytes(source.read_bytes())
                manifest[key] = hashlib.sha256(source.read_bytes()).hexdigest()
                if key == "plugin_hook_sha256":
                    snapshot = root / "home/plugins/data/sol-codex-sol-codex/runtime-v1/snapshots" / (manifest[key] + ".py")
                    snapshot.parent.mkdir(parents=True)
                    snapshot.write_bytes(source.read_bytes())
        (artifacts / "harness-manifest.json").write_text(json.dumps(manifest))
        requests = [{"upstream_status": 200, "done": True,
                     "client_disconnected": False, "upstream_error": None,
                     "body_bytes": 100 + index,
                     "request_has_probe_marker": index == 1,
                     "completions": [{"input_tokens": 10 + index,
                                      "output_tokens": 1, "cached_tokens": 2}]}
                    for index in range(2)]
        summary = {"exit": 0, "proxy_blocked": 0, "sink_requests": requests,
                   "journal": {"attempts": 2,
                               "states": {"pending": 0, "completed": 2, "unknown": 0},
                               "all_attempts_have_observed_usage": True,
                               "provider_billing_complete": False,
                               "observed_completed_usage": {
                                   "input_tokens": 21, "output_tokens": 2,
                                   "cached_input_tokens": 4}}}
        (artifacts / "proxy-summary.json").write_text(json.dumps(summary))
        stdout = b"A" * 8000 + b"\n" + marker.encode() + b"\n"
        stderr = (b"test_output (test_probe.Probe.test_output) ... ok\n\n" +
                  b"----------------------------------------------------------------------\n" +
                  b"Ran 1 test in 0.000s\n\nOK\n")
        output = (stdout + stderr).decode()
        payload = command
        if plugin:
            status = root / "tmp" / ("sol-codex-verifier-status-501-" + "b" * 16) / ("c" * 24) / ("a" * 32 + ".status")
            status.parent.mkdir(parents=True)
            payload += ("\n_sol_codex_exit=$?\n"
                        "printf '%s\\n' \"$_sol_codex_exit\" > " +
                        shlex.quote(str(status)) + "\nexit \"$_sol_codex_exit\"")
            state = {"last_verification": {"command_sha256": hashlib.sha256(
                        command.encode()).hexdigest(), "exit_code": 0, "passed": True},
                     "metrics": {"verification_pass": 1, "packed_observations": 1,
                                 "source_bytes": len(output.encode()), "receipt_bytes": 1000,
                                 "saved_bytes": len(output.encode()) - 1000},
                     "pending_verifiers": {}}
            state_path = root / "home/plugins/data/sol-codex-sol-codex/state/test.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps(state))
        events = [{"type": "item.completed", "item": {"type": "command_execution",
                    "command": "/bin/zsh -lc " + shlex.quote(payload),
                    "exit_code": 0, "aggregated_output": output}},
                  {"type": "item.completed", "item": {"type": "agent_message",
                    "text": marker}}, {"type": "turn.completed"}]
        (artifacts / "trace.jsonl").write_text("\n".join(json.dumps(event) for event in events))
        return marker

    def test_valid_controls_and_fixed_fields(self):
        for plugin in (False, True):
            with self.subTest(plugin=plugin), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = self.make_fixture(root, plugin)
                report = inspect(root, plugin)
                self.assertEqual(report["request_marker_flags"], [False, True])
                self.assertEqual(report["child_executions"], 1)
                self.assertNotIn(marker, json.dumps(report))

    def test_missing_or_duplicate_execution_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root, True)
            (root / "work/run-count.log").write_text("run\nrun\n")
            with self.assertRaises(ValueError):
                inspect(root, True)
            (root / "work/run-count.log").write_text("run\n")
            (root / "home/plugins/data/sol-codex-sol-codex/state/test.json").unlink()
            with self.assertRaises(ValueError):
                inspect(root, True)

    def test_request_marker_and_usage_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root, True)
            path = root / "artifacts/proxy-summary.json"
            summary = json.loads(path.read_text())
            summary["sink_requests"][1]["request_has_probe_marker"] = False
            path.write_text(json.dumps(summary))
            with self.assertRaises(ValueError):
                inspect(root, True)
            summary["sink_requests"][1]["request_has_probe_marker"] = True
            summary["journal"]["observed_completed_usage"]["input_tokens"] += 1
            path.write_text(json.dumps(summary))
            with self.assertRaises(ValueError):
                inspect(root, True)

    def test_source_or_permission_boundary_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root, True)
            cached = root / "home/plugins/cache/sol-codex/sol-codex" / VERSION / "scripts/sol_hook.py"
            cached.write_bytes(b"different")
            with self.assertRaises(ValueError):
                inspect(root, True)
            source = root / "market/plugins/sol-codex/scripts/sol_hook.py"
            cached.write_bytes(source.read_bytes())
            path = root / "artifacts/harness-manifest.json"
            manifest = json.loads(path.read_text())
            manifest["argv"].insert(-1, "--dangerously-bypass-approvals-and-sandbox")
            path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                inspect(root, True)

    def test_cross_arm_fixture_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as control_dir, tempfile.TemporaryDirectory() as plugin_dir:
            control, plugin = Path(control_dir), Path(plugin_dir)
            self.make_fixture(control, False)
            self.make_fixture(plugin, True)
            with (plugin / "work/test_probe.py").open("ab") as stream:
                stream.write(b"# changed\n")
            with mock.patch.object(sys, "argv", ["audit", "--control", str(control),
                                                "--plugin", str(plugin)]), self.assertRaises(ValueError):
                main()

    def test_manifest_hash_cannot_export_private_payload(self):
        with tempfile.TemporaryDirectory() as control_dir, tempfile.TemporaryDirectory() as plugin_dir:
            control, plugin = Path(control_dir), Path(plugin_dir)
            self.make_fixture(control, False)
            self.make_fixture(plugin, True)
            for root in (control, plugin):
                path = root / "artifacts/harness-manifest.json"
                manifest = json.loads(path.read_text())
                manifest["driver_sha256"] = {"secret": "SYNTHETIC_SECRET"}
                path.write_text(json.dumps(manifest))
            stream = io.StringIO()
            with mock.patch.object(sys, "argv", ["audit", "--control", str(control),
                                                "--plugin", str(plugin)]), \
                    redirect_stdout(stream), self.assertRaises(ValueError):
                main()
            self.assertEqual(stream.getvalue(), "")

    def test_conflicting_feature_flags_and_comment_wrapper_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root, True)
            manifest_path = root / "artifacts/harness-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["argv"].insert(-1, "--disable")
            manifest["argv"].insert(-1, "code_mode")
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                inspect(root, True)
            manifest["argv"][-3:-1] = []
            manifest_path.write_text(json.dumps(manifest))
            trace = root / "artifacts/trace.jsonl"
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            command = "/tmp/fixed-python3 -m unittest -v test_probe"
            events[0]["item"]["command"] = ("/bin/zsh -lc " + shlex.quote(
                command + '\n_sol_codex_exit=$?\n# exit "$_sol_codex_exit"'))
            trace.write_text("\n".join(json.dumps(event) for event in events))
            with self.assertRaises(ValueError):
                inspect(root, True)

    def test_duplicate_feature_switches_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root, False)
            path = root / "artifacts/harness-manifest.json"
            manifest = json.loads(path.read_text())
            manifest["argv"][-1:-1] = ["--enable", "hooks", "--enable", "hooks",
                                      "--enable", "plugins", "--enable", "plugins"]
            path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                inspect(root, False)

    def test_negative_counters_and_impossible_journal_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root, True)
            state_path = root / "home/plugins/data/sol-codex-sol-codex/state/test.json"
            state = json.loads(state_path.read_text())
            original = dict(state["metrics"])
            state["metrics"]["receipt_bytes"] = -1
            state["metrics"]["saved_bytes"] = original["source_bytes"] + 1
            state_path.write_text(json.dumps(state))
            with self.assertRaises(ValueError):
                inspect(root, True)
            state["metrics"] = original
            state_path.write_text(json.dumps(state))
            path = root / "artifacts/proxy-summary.json"
            summary = json.loads(path.read_text())
            summary["sink_requests"][1]["body_bytes"] = 0
            path.write_text(json.dumps(summary))
            with self.assertRaises(ValueError):
                inspect(root, True)
            summary["sink_requests"][1]["body_bytes"] = 101
            summary["proxy_blocked"] = -1
            summary["journal"]["states"]["failed"] = 9
            path.write_text(json.dumps(summary))
            with self.assertRaises(ValueError):
                inspect(root, True)


if __name__ == "__main__":
    unittest.main()
