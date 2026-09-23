"""Windows integration checks for the installed Codex hook commands."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows hook commands")
class WindowsHookTests(unittest.TestCase):
    def test_all_lifecycle_hooks_have_windows_commands(self) -> None:
        config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        commands = [
            hook["commandWindows"]
            for matchers in config["hooks"].values()
            for matcher in matchers
            for hook in matcher["hooks"]
        ]
        self.assertEqual(len(commands), 7)
        self.assertEqual(len(set(commands)), 1)
        self.assertTrue(all("sol_hook.cmd" in command for command in commands))

    def test_removed_cache_does_not_block_tools(self) -> None:
        config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        commands = [
            hook["commandWindows"]
            for matchers in config["hooks"].values()
            for matcher in matchers
            for hook in matcher["hooks"]
        ]
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["PLUGIN_ROOT"] = str(Path(directory) / "removed-cache")
            environment["PLUGIN_DATA"] = str(Path(directory) / "data")
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(
                        command, input="{}", text=True, capture_output=True,
                        shell=True, env=environment, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "")

    def test_missing_python_script_does_not_block_tools(self) -> None:
        config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        command = config["hooks"]["SessionStart"][0]["hooks"][0]["commandWindows"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copy2(PLUGIN_ROOT / "scripts" / "sol_hook.cmd", scripts / "sol_hook.cmd")
            environment = os.environ.copy()
            environment.update({"PLUGIN_ROOT": str(root), "PLUGIN_DATA": str(root / "data")})
            result = subprocess.run(
                command, input="{}", text=True, capture_output=True,
                shell=True, env=environment, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_session_start_windows_command_runs(self) -> None:
        config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        command = config["hooks"]["SessionStart"][0]["hooks"][0]["commandWindows"]
        with tempfile.TemporaryDirectory() as data:
            environment = os.environ.copy()
            environment.update({"PLUGIN_ROOT": str(PLUGIN_ROOT), "PLUGIN_DATA": data})
            result = subprocess.run(
                command,
                input=json.dumps({"session_id": "windows-command-smoke", "hook_event_name": "SessionStart"}),
                text=True,
                capture_output=True,
                shell=True,
                env=environment,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("hookSpecificOutput", json.loads(result.stdout))

    def test_windows_command_handles_spaces_and_python_fallback(self) -> None:
        config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        command = config["hooks"]["SessionStart"][0]["hooks"][0]["commandWindows"]
        with tempfile.TemporaryDirectory(prefix="sol plugin space ") as directory:
            root = Path(directory) / "plugin root"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            for name in ("sol_hook.py", "sol_hook.cmd"):
                shutil.copy2(PLUGIN_ROOT / "scripts" / name, scripts / name)
            fake_bin = Path(directory) / "fake bin"
            fake_bin.mkdir()
            (fake_bin / "py.cmd").write_text("@echo off\r\nexit /b 1\r\n", encoding="utf-8")
            environment = os.environ.copy()
            environment.update({
                "PLUGIN_ROOT": str(root),
                "PLUGIN_DATA": str(Path(directory) / "plugin data"),
                "PATH": str(fake_bin) + os.pathsep + environment["PATH"],
            })
            result = subprocess.run(
                command,
                input=json.dumps({"session_id": "windows-space-smoke", "hook_event_name": "SessionStart"}),
                text=True,
                capture_output=True,
                shell=True,
                env=environment,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("hookSpecificOutput", json.loads(result.stdout))

    def test_concurrent_hook_updates_are_not_lost(self) -> None:
        config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        command = config["hooks"]["PostToolUse"][0]["hooks"][0]["commandWindows"]
        with tempfile.TemporaryDirectory() as data:
            environment = os.environ.copy()
            environment.update({"PLUGIN_ROOT": str(PLUGIN_ROOT), "PLUGIN_DATA": data})

            def run_hook(number: int) -> subprocess.CompletedProcess[str]:
                payload = {
                    "session_id": "windows-locking-smoke",
                    "hook_event_name": "PostToolUse",
                    "tool_name": "apply_patch",
                    "tool_use_id": f"patch-{number}",
                    "tool_input": {"patch": "*** Begin Patch\n*** Update File: src/main.py\n@@\n-old\n+new\n*** End Patch"},
                    "tool_response": {"output": "Done!"},
                }
                return subprocess.run(
                    command,
                    input=json.dumps(payload),
                    text=True,
                    capture_output=True,
                    shell=True,
                    env=environment,
                    check=False,
                )

            with ThreadPoolExecutor(max_workers=6) as pool:
                for result in pool.map(run_hook, range(18)):
                    self.assertEqual(result.returncode, 0, result.stderr)

            state_file = next((Path(data) / "state").glob("*.json"))
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["metrics"]["code_mutations"], 18)

    def test_bash_verifier_is_not_wrapped_with_posix_sidecar(self) -> None:
        config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        command = config["hooks"]["PreToolUse"][0]["hooks"][0]["commandWindows"]
        with tempfile.TemporaryDirectory() as data:
            environment = os.environ.copy()
            environment.update({"PLUGIN_ROOT": str(PLUGIN_ROOT), "PLUGIN_DATA": data})
            result = subprocess.run(
                command,
                input=json.dumps({
                    "session_id": "windows-verifier-smoke",
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_use_id": "verifier-1",
                    "permission_mode": "bypassPermissions",
                    "tool_input": {"command": "python3 -m unittest test_smoke"},
                }),
                text=True,
                capture_output=True,
                shell=True,
                env=environment,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
