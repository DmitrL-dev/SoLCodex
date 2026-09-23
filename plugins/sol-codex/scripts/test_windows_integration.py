"""Windows integration checks for the installed Codex hook commands."""

from __future__ import annotations

import json
import os
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
        self.assertTrue(all("sol_hook.py" in command for command in commands))

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
        self.assertEqual(result.returncode, 0, result.stderr)
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


if __name__ == "__main__":
    unittest.main()
