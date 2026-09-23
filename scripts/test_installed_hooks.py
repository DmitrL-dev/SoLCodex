"""Smoke-test hook commands from an isolated marketplace plugin copy."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_EVENTS = {
    "SessionStart", "PreToolUse", "PostToolUse", "PreCompact",
    "PostCompact", "Stop", "SessionEnd",
}


class InstalledHookSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="sol codex installed ")
        self.addCleanup(self.temporary.cleanup)
        temporary_root = Path(self.temporary.name)

        marketplace = json.loads(
            (ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
        )
        source = ROOT / marketplace["plugins"][0]["source"]["path"]
        self.plugin_root = temporary_root / "plugin cache with spaces"
        shutil.copytree(source, self.plugin_root)
        self.data_root = temporary_root / "plugin data with spaces"

        manifest = json.loads(
            (self.plugin_root / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.hooks = json.loads(
            (self.plugin_root / manifest["hooks"]).read_text(encoding="utf-8")
        )["hooks"]
        self.environment = os.environ.copy()
        self.environment.update({
            "PLUGIN_ROOT": str(self.plugin_root),
            "PLUGIN_DATA": str(self.data_root),
            "PYTHONDONTWRITEBYTECODE": "1",
        })

    def invoke(self, name: str, **fields: Any) -> dict[str, Any] | None:
        matchers = self.hooks[name]
        self.assertEqual(len(matchers), 1, name)
        selector = {
            "SessionStart": "source",
            "PreToolUse": "tool_name",
            "PostToolUse": "tool_name",
            "PreCompact": "trigger",
            "PostCompact": "trigger",
            "SessionEnd": "reason",
        }.get(name)
        if selector is not None:
            value = fields[selector]
            aliases = [value]
            if value == "exec_command":
                aliases.append("Bash")
            elif value == "apply_patch":
                aliases.extend(("Edit", "Write"))
            matcher = matchers[0].get("matcher")
            if matcher not in (None, "", "*"):
                self.assertTrue(
                    any(re.search(matcher, alias) for alias in aliases),
                    f"{name}: matcher {matcher!r} misses {value!r}",
                )
        hooks = matchers[0]["hooks"]
        self.assertEqual(len(hooks), 1, name)
        command_key = "commandWindows" if os.name == "nt" else "command"
        command = hooks[0][command_key]
        event = {"session_id": "installed-hook-smoke", "hook_event_name": name, **fields}
        result = subprocess.run(
            command,
            input=json.dumps(event),
            text=True,
            capture_output=True,
            shell=True,
            env=self.environment,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, f"{name}: {result.stdout}\n{result.stderr}")
        self.assertNotIn("hook degraded safely", result.stderr, name)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def test_all_installed_lifecycle_commands_run_and_keep_state(self) -> None:
        self.assertEqual(set(self.hooks), EXPECTED_EVENTS)
        command_key = "commandWindows" if os.name == "nt" else "command"
        commands = {
            hook[command_key]
            for name in EXPECTED_EVENTS
            for matcher in self.hooks[name]
            for hook in matcher["hooks"]
        }
        self.assertEqual(len(commands), 1, "all lifecycle events must use the tested launcher")

        started = self.invoke("SessionStart", source="startup")
        self.assertEqual(started["hookSpecificOutput"]["hookEventName"], "SessionStart")

        self.invoke(
            "PostToolUse",
            tool_name="apply_patch",
            tool_use_id="patch-1",
            tool_input={"patch": "*** Begin Patch\n*** Update File: src/smoke.py\n@@\n-old\n+new\n*** End Patch"},
            tool_response={"output": "Done!"},
        )
        pending = self.invoke("Stop")
        self.assertTrue(pending["continue"])
        self.assertIn("verification pending", pending["systemMessage"])

        verifier = {"tool_name": "exec_command", "tool_use_id": "check-1",
                    "tool_input": {"cmd": "python -m unittest test_smoke"}}
        self.invoke("PreToolUse", **verifier)
        self.invoke("PostToolUse", **verifier, tool_response={"exit_code": 0, "output": "OK"})
        verified = self.invoke("Stop")
        self.assertTrue(verified["continue"])
        self.assertNotIn("systemMessage", verified)

        self.invoke("PreCompact", trigger="auto")
        self.invoke("PostCompact", trigger="auto")
        self.invoke("PreToolUse", **{**verifier, "tool_use_id": "check-unfinished"})
        self.invoke("SessionEnd", reason="other")

        state_files = list((self.data_root / "state").glob("*.json"))
        self.assertEqual(len(state_files), 1)
        state = json.loads(state_files[0].read_text(encoding="utf-8"))
        self.assertEqual(state["metrics"]["compactions"], 1)
        self.assertEqual(state["metrics"]["verification_pass"], 1)
        self.assertFalse(state.get("pending_verifiers"))


if __name__ == "__main__":
    unittest.main()
