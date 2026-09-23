"""Lifecycle commands must fail open when an old plugin cache is removed."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == "nt", "POSIX hook commands")
class PosixHookCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.commands = [
            hook["command"]
            for matchers in config["hooks"].values()
            for matcher in matchers
            for hook in matcher["hooks"]
        ]

    def test_removed_cache_does_not_block_tools(self) -> None:
        self.assertEqual(len(self.commands), 7)
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["PLUGIN_ROOT"] = str(Path(directory) / "removed-cache")
            environment["PLUGIN_DATA"] = str(Path(directory) / "data")
            for command in self.commands:
                with self.subTest(command=command):
                    result = subprocess.run(
                        command, input="{}", text=True, capture_output=True,
                        shell=True, env=environment, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "")

    def test_existing_hook_still_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["PLUGIN_ROOT"] = str(PLUGIN_ROOT)
            environment["PLUGIN_DATA"] = directory
            result = subprocess.run(
                self.commands[0],
                input=json.dumps({"session_id": "posix-command-smoke", "hook_event_name": "SessionStart"}),
                text=True, capture_output=True, shell=True, env=environment, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("hookSpecificOutput", json.loads(result.stdout))

    def test_cache_removed_after_shell_check_does_not_block_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "cache" / "scripts"
            scripts.mkdir(parents=True)
            hook = scripts / "sol_hook.py"
            shutil.copy2(PLUGIN_ROOT / "scripts" / "sol_hook.py", hook)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            python = fake_bin / "python3"
            python.write_text(
                '#!/bin/sh\nrm "$PLUGIN_ROOT/scripts/sol_hook.py"\n'
                f'exec "{sys.executable}" "$@"\n', encoding="utf-8",
            )
            python.chmod(0o755)
            environment = os.environ.copy()
            environment.update({
                "PLUGIN_ROOT": str(scripts.parent),
                "PLUGIN_DATA": str(root / "data"),
                "PATH": str(fake_bin) + os.pathsep + environment["PATH"],
            })
            result = subprocess.run(
                self.commands[0], input="{}", text=True, capture_output=True,
                shell=True, env=environment, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
