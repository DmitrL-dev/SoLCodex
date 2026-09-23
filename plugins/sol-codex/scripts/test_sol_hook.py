#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import patch

import sol_hook


SCRIPT = Path(__file__).with_name("sol_hook.py")


class HookHarness:
    def __init__(self, data: Path) -> None:
        self.data = data
        self.temp_root = data.parent / "sandbox-temp"
        self.temp_root.mkdir(mode=0o700)

    def run(self, event: Dict[str, Any], threshold: Optional[int] = None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PLUGIN_DATA"] = str(self.data)
        environment["TMPDIR"] = str(self.temp_root)
        environment.pop("SOL_CODEX_PACK_THRESHOLD_BYTES", None)
        if threshold is not None:
            environment["SOL_CODEX_PACK_THRESHOLD_BYTES"] = str(threshold)
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            check=True,
            env=environment,
        )

    def report(self) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PLUGIN_DATA"] = str(self.data)
        environment["TMPDIR"] = str(self.temp_root)
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--report"],
            text=True,
            capture_output=True,
            check=True,
            env=environment,
        )


def event(name: str, **fields: Any) -> Dict[str, Any]:
    return {
        "session_id": "session-under-test",
        "cwd": "/tmp/project",
        "hook_event_name": name,
        "model": "gpt-5.6-sol",
        **fields,
    }


class SolHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name) / "plugin-data"
        self.harness = HookHarness(self.data)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def record_code_change(self) -> None:
        result = self.harness.run(event(
            "PostToolUse",
            tool_name="apply_patch",
            tool_use_id="patch-1",
            tool_input={"patch": "*** Begin Patch\n*** Update File: src/main.py\n@@\n-old\n+new\n*** End Patch"},
            tool_response={"output": "Done!"},
        ))
        self.assertEqual(result.stdout, "")

    def assert_pending_stop(self, payload: Dict[str, Any], status: str = "pending") -> None:
        self.assertEqual(payload.get("continue"), True)
        self.assertNotIn("decision", payload)
        self.assertIn(f"verification {status}", payload.get("systemMessage", "").lower())

    def test_windows_session_end_cleanup_does_not_require_posix_uid(self) -> None:
        state = {"pending_verifiers": {"unfinished": {"generation": 1}}}

        class Store:
            @contextmanager
            def locked(self):
                yield state

        # Windows has no POSIX uid and never creates Bash verifier sidecars.
        with patch.object(sol_hook, "os", SimpleNamespace(name="nt")):
            sol_hook.cleanup_verifier_status(self.data, event("SessionEnd"), Store())
        self.assertNotIn("pending_verifiers", state)

    def test_pending_stop_warns_without_blocking_final_answer(self) -> None:
        self.record_code_change()
        payload = json.loads(self.harness.run(event(
            "Stop", stop_hook_active=False,
            last_assistant_message="The code change is ready; verification is pending.",
        )).stdout)
        self.assertEqual(payload.get("continue"), True)
        self.assertNotIn("decision", payload)
        self.assertIn("verification", payload.get("systemMessage", "").lower())
        self.assertNotIn("src/main.py", payload["systemMessage"])

    def test_verification_debt_warns_then_clears(self) -> None:
        self.record_code_change()
        blocked = self.harness.run(event("Stop", stop_hook_active=False))
        blocked_payload = json.loads(blocked.stdout)
        self.assert_pending_stop(blocked_payload)

        self.harness.run(event(
            "PreToolUse", tool_name="Bash", tool_use_id="exec-structured-pass",
            permission_mode="default", tool_input={"command": "python3 -m pytest -q"},
        ))
        verified = self.harness.run(event(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="exec-structured-pass",
            tool_input={"command": "python3 -m pytest -q"},
            tool_response={"output": "1 passed", "exit_code": 0},
        ))
        self.assertEqual(verified.stdout, "")
        allowed = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assertEqual(allowed, {"continue": True})

    def test_unpaired_structured_verifier_cannot_clear_debt(self) -> None:
        self.record_code_change()
        self.harness.run(event(
            "PostToolUse", tool_name="Bash", tool_use_id="missing-pre",
            tool_input={"command": "python3 -m pytest -q"},
            tool_response={"output": "1 passed", "exit_code": 0},
        ))
        blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assert_pending_stop(blocked)

    def test_stale_structured_verifier_cannot_clear_new_code_change(self) -> None:
        self.record_code_change()
        command = "python3 -m py_compile src/main.py"
        tool_use_id = "exec-structured-stale"
        before = self.harness.run(event(
            "PreToolUse", tool_name="Bash", tool_use_id=tool_use_id,
            permission_mode="default", tool_input={"command": command},
        ))
        self.assertEqual(before.stdout, "")
        self.record_code_change()
        self.harness.run(event(
            "PostToolUse", tool_name="Bash", tool_use_id=tool_use_id,
            tool_input={"command": command},
            tool_response={"output": "", "exit_code": 0},
        ))
        blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assert_pending_stop(blocked)

    @unittest.skipIf(os.name == "nt", "requires a POSIX shell")
    def test_stale_sidecar_verifier_cannot_clear_new_code_change(self) -> None:
        self.record_code_change()
        source = Path(self.temporary.name) / "sample.py"
        source.write_text("value = 1\n", encoding="utf-8")
        command = "python3 -m py_compile sample.py"
        tool_use_id = "exec-sidecar-stale"
        before = self.harness.run(event(
            "PreToolUse", tool_name="Bash", tool_use_id=tool_use_id,
            permission_mode="bypassPermissions", tool_input={"command": command},
        ))
        wrapped = json.loads(before.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        ran = subprocess.run(
            wrapped, shell=True, executable="/bin/sh", cwd=source.parent,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(ran.returncode, 0)
        source.write_text("def broken(:\n", encoding="utf-8")
        self.record_code_change()
        self.harness.run(event(
            "PostToolUse", tool_name="Bash", tool_use_id=tool_use_id,
            tool_input={"command": wrapped}, tool_response=ran.stdout + ran.stderr,
        ))
        blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assert_pending_stop(blocked)

    @unittest.skipIf(os.name == "nt", "requires a POSIX shell")
    def test_pre_tool_use_records_real_verifier_status_for_string_response(self) -> None:
        self.record_code_change()
        source = Path(self.temporary.name) / "sample.py"
        source.write_text("value = 1\n", encoding="utf-8")
        command = "python3 -m py_compile sample.py"
        before = self.harness.run(event(
            "PreToolUse",
            tool_name="Bash",
            tool_use_id="exec-verifier-pass",
            permission_mode="bypassPermissions",
            tool_input={"command": command},
        ))
        updated = json.loads(before.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertNotEqual(updated, command)
        ran = subprocess.run(
            updated, shell=True, executable="/bin/sh", cwd=source.parent,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(ran.returncode, 0, ran.stderr)
        response = ran.stdout + ran.stderr + ("x" * 13_000)
        after = self.harness.run(event(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="exec-verifier-pass",
            tool_input={"command": updated},
            tool_response=response,
        ))
        receipt = json.loads(after.stdout)["reason"]
        self.assertIn("Status: exit_code=0", receipt)
        artifacts = list((self.data / "observations").rglob("obs_*.txt"))
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].read_text(encoding="utf-8"), response)
        allowed = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assertEqual(allowed, {"continue": True})

    @unittest.skipIf(os.name == "nt", "requires a POSIX shell")
    def test_verifier_status_uses_configured_temp_root(self) -> None:
        self.record_code_change()
        source = Path(self.temporary.name) / "test_smoke.py"
        source.write_text(
            "import unittest\n\n"
            "class TestSmoke(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertEqual(1 + 1, 2)\n",
            encoding="utf-8",
        )
        before = self.harness.run(event(
            "PreToolUse", tool_name="Bash", tool_use_id="exec-sandbox-status",
            permission_mode="bypassPermissions",
            tool_input={"command": "python3 -m unittest test_smoke.TestSmoke.test_ok"},
        ))
        wrapped = json.loads(before.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        statuses = list(self.harness.temp_root.rglob("*.status"))
        self.assertEqual(len(statuses), 1)
        self.assertFalse((self.data / "verifier-status").exists())
        self.assertIn(str(statuses[0]), wrapped)
        self.assertEqual(stat.S_IMODE(statuses[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(statuses[0].parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(statuses[0].parent.parent.stat().st_mode), 0o700)

        status = statuses[0]
        ran = subprocess.run(
            wrapped, shell=True, executable="/bin/sh", cwd=source.parent,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(status.read_text(encoding="utf-8"), "0\n")
        self.harness.run(event(
            "PostToolUse", tool_name="Bash", tool_use_id="exec-sandbox-status",
            tool_input={"command": wrapped}, tool_response=ran.stdout + ran.stderr,
        ))
        self.assertFalse(status.exists())
        allowed = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assertEqual(allowed, {"continue": True})

    @unittest.skipIf(os.name == "nt", "requires a POSIX shell")
    def test_session_end_removes_unconsumed_temp_status(self) -> None:
        before = self.harness.run(event(
            "PreToolUse", tool_name="Bash", tool_use_id="exec-abandoned-status",
            permission_mode="bypassPermissions",
            tool_input={"command": "python3 -m unittest test_smoke"},
        ))
        self.assertIn("updatedInput", json.loads(before.stdout)["hookSpecificOutput"])
        status = next(self.harness.temp_root.rglob("*.status"))
        self.harness.run(event("SessionEnd"))
        self.assertFalse(status.exists())

    @unittest.skipIf(os.name == "nt", "requires a POSIX shell")
    def test_pre_tool_use_nonzero_status_keeps_debt(self) -> None:
        self.record_code_change()
        source = Path(self.temporary.name) / "sample.py"
        source.write_text("def broken(:\n", encoding="utf-8")
        before = self.harness.run(event(
            "PreToolUse", tool_name="Bash", tool_use_id="exec-verifier-fail",
            permission_mode="bypassPermissions",
            tool_input={"command": "python3 -m py_compile sample.py"},
        ))
        updated = json.loads(before.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        ran = subprocess.run(
            updated, shell=True, executable="/bin/sh", cwd=source.parent,
            text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(ran.returncode, 0)
        self.harness.run(event(
            "PostToolUse", tool_name="Bash", tool_use_id="exec-verifier-fail",
            tool_input={"command": updated}, tool_response=ran.stdout + ran.stderr,
        ))
        blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assert_pending_stop(blocked, "failed")

    @unittest.skipIf(os.name == "nt", "requires a POSIX shell")
    def test_missing_pre_tool_use_status_cannot_clear_debt(self) -> None:
        self.record_code_change()
        before = self.harness.run(event(
            "PreToolUse", tool_name="Bash", tool_use_id="exec-not-run",
            permission_mode="bypassPermissions",
            tool_input={"command": "python3 -m py_compile sample.py"},
        ))
        updated = json.loads(before.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        self.harness.run(event(
            "PostToolUse", tool_name="Bash", tool_use_id="exec-not-run",
            tool_input={"command": updated}, tool_response="Process exited with code 0\n",
        ))
        blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assert_pending_stop(blocked)

    @unittest.skipIf(os.name == "nt", "requires a POSIX shell")
    def test_deleted_status_file_degrades_without_losing_the_shell_result(self) -> None:
        self.record_code_change()
        before = self.harness.run(event(
            "PreToolUse", tool_name="Bash", tool_use_id="exec-status-deleted",
            permission_mode="bypassPermissions",
            tool_input={"command": "python3 -m py_compile sample.py"},
        ))
        updated = json.loads(before.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        status = next(self.harness.temp_root.rglob("*.status"))
        status.unlink()
        after = self.harness.run(event(
            "PostToolUse", tool_name="Bash", tool_use_id="exec-status-deleted",
            tool_input={"command": updated}, tool_response="1 passed\n",
        ))
        self.assertEqual(after.stdout, "")
        self.assertEqual(after.stderr, "")
        blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assert_pending_stop(blocked)

    def test_pre_tool_use_leaves_unrecognized_shell_command_unchanged(self) -> None:
        result = self.harness.run(event(
            "PreToolUse", tool_name="Bash", tool_use_id="exec-ordinary",
            permission_mode="bypassPermissions",
            tool_input={"command": "printf ordinary"},
        ))
        self.assertEqual(result.stdout, "")

    def test_pre_tool_use_does_not_approve_verifier_when_approval_is_possible(self) -> None:
        result = self.harness.run(event(
            "PreToolUse", tool_name="Bash", tool_use_id="exec-needs-approval",
            permission_mode="default",
            tool_input={"command": "python3 -m pytest -q"},
        ))
        self.assertEqual(result.stdout, "")
        self.assertEqual(list(self.harness.temp_root.rglob("*.status")), [])

    def test_pre_tool_use_rejects_non_bash_command_shape(self) -> None:
        result = self.harness.run(event(
            "PreToolUse", tool_name="exec_command", tool_use_id="exec-non-bash",
            permission_mode="bypassPermissions",
            tool_input={"cmd": "python3 -m pytest -q"},
        ))
        self.assertEqual(result.stdout, "")
        self.assertEqual(list(self.harness.temp_root.rglob("*.status")), [])

    def test_verifier_rejects_shell_expansion_that_hides_help_mode(self) -> None:
        self.record_code_change()
        for suffix, command in (
            ("variable", "python3 -m unittest --${SOL_REVIEW_FLAG:-help}"),
            ("glob", "python3 -m unittest --*"),
            ("brace", "python3 -m unittest --{help,version}"),
        ):
            with self.subTest(suffix=suffix):
                tool_use_id = f"exec-expanded-{suffix}"
                before = self.harness.run(event(
                    "PreToolUse", tool_name="Bash", tool_use_id=tool_use_id,
                    permission_mode="bypassPermissions", tool_input={"command": command},
                ))
                self.assertEqual(before.stdout, "")
                self.harness.run(event(
                    "PostToolUse", tool_name="Bash", tool_use_id=tool_use_id,
                    tool_input={"command": command},
                    tool_response={"output": "usage: unittest", "exit_code": 0},
                ))
        blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assert_pending_stop(blocked)

    @unittest.skipIf(os.name == "nt", "requires a POSIX shell")
    def test_verifier_wrapper_preserves_errexit_inside_shell_function(self) -> None:
        self.record_code_change()
        original = "pytest"
        setup = "set -e\npytest() { false; printf 'cleanup ran\\n'; }\n"
        baseline = subprocess.run(
            ["/bin/bash", "-c", setup + original], text=True, capture_output=True,
            check=False,
        )
        self.assertEqual(baseline.returncode, 1)
        self.assertNotIn("cleanup ran", baseline.stdout)
        before = self.harness.run(event(
            "PreToolUse", tool_name="Bash", tool_use_id="exec-errexit",
            permission_mode="bypassPermissions", tool_input={"command": original},
        ))
        wrapped = json.loads(before.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
        ran = subprocess.run(
            ["/bin/bash", "-c", setup + wrapped], text=True, capture_output=True,
            check=False,
        )
        self.assertEqual(ran.returncode, baseline.returncode)
        self.assertNotIn("cleanup ran", ran.stdout)
        self.harness.run(event(
            "PostToolUse", tool_name="Bash", tool_use_id="exec-errexit",
            tool_input={"command": wrapped}, tool_response=ran.stdout + ran.stderr,
        ))
        blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assert_pending_stop(blocked)

    @unittest.skipIf(os.name == "nt", "requires POSIX process signals")
    def test_interrupted_verifier_cannot_clear_debt(self) -> None:
        self.record_code_change()
        source = Path(self.temporary.name)
        (source / "test_interrupt.py").write_text(
            "import time\nimport unittest\nfrom pathlib import Path\n"
            "class InterruptTest(unittest.TestCase):\n"
            "    def test_interrupt(self):\n"
            "        Path('started').write_text('started')\n"
            "        time.sleep(30)\n",
            encoding="utf-8",
        )
        command = "python3 -m unittest -q test_interrupt"
        for name, signum in (("term", signal.SIGTERM), ("hup", signal.SIGHUP)):
            with self.subTest(signal=name):
                started = source / "started"
                started.unlink(missing_ok=True)
                tool_use_id = f"exec-interrupted-{name}"
                before = self.harness.run(event(
                    "PreToolUse", tool_name="Bash", tool_use_id=tool_use_id,
                    permission_mode="bypassPermissions", tool_input={"command": command},
                ))
                wrapped = json.loads(before.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
                process = subprocess.Popen(
                    ["/bin/bash", "-c", wrapped], cwd=source, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                )
                try:
                    deadline = time.monotonic() + 5
                    while not started.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(started.exists(), "verifier did not start")
                    os.killpg(process.pid, signum)
                    stdout, stderr = process.communicate(timeout=5)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate(timeout=5)
                self.assertNotEqual(process.returncode, 0)
                self.harness.run(event(
                    "PostToolUse", tool_name="Bash", tool_use_id=tool_use_id,
                    tool_input={"command": wrapped}, tool_response=stdout + stderr,
                ))
                blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
                self.assert_pending_stop(blocked)

    def test_failed_verifier_keeps_debt(self) -> None:
        self.record_code_change()
        self.harness.run(event(
            "PreToolUse", tool_name="exec_command", tool_use_id="exec-structured-fail",
            permission_mode="default", tool_input={"cmd": "npm test"},
        ))
        self.harness.run(event(
            "PostToolUse",
            tool_name="exec_command",
            tool_use_id="exec-structured-fail",
            tool_input={"cmd": "npm test"},
            tool_response={"output": "FAIL one test", "exit_code": 1},
        ))
        blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assert_pending_stop(blocked, "failed")

    def test_apply_patch_move_tracks_the_code_destination(self) -> None:
        self.harness.run(event(
            "PostToolUse",
            tool_name="apply_patch",
            tool_use_id="patch-move",
            tool_input={
                "patch": (
                    "*** Begin Patch\n"
                    "*** Update File: draft.txt\n"
                    "*** Move to: src/main.py\n"
                    "@@\n-old\n+new\n"
                    "*** End Patch"
                )
            },
            tool_response={"output": "Done!"},
        ))
        blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assert_pending_stop(blocked)
        context = json.loads(self.harness.run(event("SessionStart", source="compact")).stdout)
        self.assertIn("src/main.py", context["hookSpecificOutput"]["additionalContext"])

    def test_compound_verifier_command_cannot_clear_debt_from_wrapper_status(self) -> None:
        for command in (
            "npm test || true",
            "npm test -- --runInBand; printf done",
            "printf '%s\\n' \"$(python3 -m unittest missing_test_module)\"",
            "printf '%s\\n' '(npm test -- --runInBand)'",
            "OUT=`env python3 -m unittest missing_test_module` true",
        ):
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as temporary:
                    harness = HookHarness(Path(temporary) / "plugin-data")
                    harness.run(event(
                        "PostToolUse",
                        tool_name="apply_patch",
                        tool_use_id="patch-compound",
                        tool_input={"patch": "*** Begin Patch\n*** Update File: src/main.py\n@@\n-old\n+new\n*** End Patch"},
                        tool_response={"output": "Done!"},
                    ))
                    harness.run(event(
                        "PostToolUse",
                        tool_name="Bash",
                        tool_input={"command": command},
                        tool_response={"output": "wrapped command returned success", "exit_code": 0},
                    ))
                    blocked = json.loads(harness.run(event("Stop", stop_hook_active=False)).stdout)
                    self.assert_pending_stop(blocked)

    def test_informational_verifier_commands_cannot_clear_debt(self) -> None:
        for command in (
            "python3 -m unittest --help",
            "python3 -m unittest --hel",
            "python3 -m unittest -qh",
            "python3 -m compileall -h",
            "python3 -m compileall --hel",
            "make --version",
            "make -nf /dev/stdin check",
            "make -if /dev/stdin check",
            "MAKEFLAGS=-n make -f /dev/stdin check",
            "MAKEFILES=/dev/stdin make check MAKEFLAGS:=-i",
            "MAKEFILES=/dev/stdin make check MAKEFLAGS+=-i",
            "make check",
            "node --check -v -",
            "tsc -v",
            "eslint -v",
            "tsc --showConfig",
            "tsc --build --dry",
            "ruff check --exit-zero",
            "pytest --collect-only",
            "npm run test --if-present",
            "cargo test -- --list",
            "swift test --list-tests",
            "gradle tasks",
            "mvn help:effective-pom",
            "ninja -n",
            "xcodebuild -list",
            "eslint --print-config src/main.js",
            "mypy --install-types",
            "pyright --createstub package",
            "shellcheck --list-optional",
            "rubocop --show-cops",
            "dotnet test --list-tests",
            "python3 -m pytest --setup-plan",
            "python3 -m pytest --setup-only",
            "pytest --collectonly",
            "python3 -m pytest --funcargs",
            "pytest --markers",
            "python3 -m pytest --cache-show",
            "pytest --last-failed --last-failed-no-failures=none",
            "python3 -m pytest --lf --lfnf=none",
            "go test -n ./...",
            "ruff check --show-files",
            "ruff check --show-settings",
            "dotnet test -t",
            "eslint --env-info",
            "npm test -- --passWithNoTests",
            "PYTEST_ADDOPTS=--collect-only python3 -m pytest -q",
            "python3 -m pytest -o addopts=--collect-only",
            "pytest -oaddopts=--collect-only",
            "python3 -m pytest -oaddopts=--setup-only",
            "pytest -VV",
            "python3 -m pytest -qV",
            "tsc --listFilesOnly",
            "cmake --build build -- -n",
            "gradle test",
            "mvn test",
            "ninja",
            "xcodebuild build",
            "cmake --build build",
        ):
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as temporary:
                    harness = HookHarness(Path(temporary) / "plugin-data")
                    harness.run(event(
                        "PostToolUse",
                        tool_name="apply_patch",
                        tool_use_id="patch-informational",
                        tool_input={"patch": "*** Begin Patch\n*** Update File: src/main.py\n@@\n-old\n+new\n*** End Patch"},
                        tool_response={"output": "Done!"},
                    ))
                    harness.run(event(
                        "PostToolUse",
                        tool_name="Bash",
                        tool_input={"command": command},
                        tool_response={"output": "usage information", "exit_code": 0},
                    ))
                    blocked = json.loads(harness.run(event("Stop", stop_hook_active=False)).stdout)
                    self.assert_pending_stop(blocked)

    def test_python_compile_counts_as_narrow_verifier(self) -> None:
        self.record_code_change()
        self.harness.run(event(
            "PreToolUse", tool_name="Bash", tool_use_id="exec-compile-pass",
            permission_mode="default",
            tool_input={"command": "python3 -m py_compile src/main.py"},
        ))
        self.harness.run(event(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="exec-compile-pass",
            tool_input={"command": "python3 -m py_compile src/main.py"},
            tool_response={"output": "", "exit_code": 0},
        ))
        allowed = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assertEqual(allowed, {"continue": True})

    def test_large_output_is_exactly_archived_and_safely_receipted(self) -> None:
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        output = "header\n" + ("ordinary output\n" * 300) + f"ERROR request failed api_key={secret}\n" + "tail\n"
        result = self.harness.run(event(
            "PostToolUse",
            tool_name="Bash",
            tool_input={"command": "npm test"},
            tool_response={"output": output, "exit_code": 1},
        ), threshold=256)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        receipt = payload["reason"]
        self.assertIn("ERROR request failed", receipt)
        self.assertIn("[REDACTED]", receipt)
        self.assertNotIn(secret, receipt)
        match = re.search(r"^Artifact: (.+)$", receipt, re.M)
        self.assertIsNotNone(match)
        artifact = Path(match.group(1))
        self.assertEqual(artifact.read_text(encoding="utf-8"), output)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o600)
        digest = hashlib.sha256(output.encode("utf-8")).hexdigest()
        self.assertIn(f"sha256={digest}", receipt)

    def test_receipt_redacts_json_secrets_and_entire_private_key_blocks(self) -> None:
        json_secret = "FAKE_REVIEW_SECRET_VALUE"
        private_key_body = "FAKE_PRIVATE_KEY_BODY_SHOULD_NOT_LEAK"
        multiline_secret = "FAKE_MULTILINE_SECRET_VALUE"
        authorization_secret = "FAKE_AUTHORIZATION_SECRET_VALUE"
        basic_authorization_secret = "ZmFrZS11c2VyOmZha2UtcGFzc3dvcmQ="
        folded_basic_secret = "RkFLRV9GT0xERURfQkFTSUNfU0VDUkVU"
        escaped_key_body = "FAKE_ESCAPED_PRIVATE_KEY_BODY"
        prefixed_key_body = "FAKE_PREFIXED_PRIVATE_KEY_BODY"
        unterminated_key_body = "FAKE_UNTERMINATED_PRIVATE_KEY_BODY"
        output = (
            f'{{"password":"{json_secret}","status":"ERROR"}}\n'
            "-----BEGIN PRIVATE KEY-----\n"
            f"{private_key_body}\n"
            "-----END PRIVATE KEY-----\n"
            f'{{"password":\n"{multiline_secret}","status":"ERROR"}}\n'
            f'{{"Authorization":"Bearer {authorization_secret}","status":"ERROR"}}\n'
            f"ERROR Authorization: Basic {basic_authorization_secret}\n"
            f"ERROR Authorization: Basic\r\n {folded_basic_secret}\n"
            f'{{"private_key":"-----BEGIN PRIVATE KEY-----\\n{escaped_key_body}\\n-----END PRIVATE KEY-----","status":"ERROR"}}\n'
            "ERROR -----BEGIN PRIVATE KEY-----\n"
            f"{prefixed_key_body}\n"
            "-----END PRIVATE KEY-----\n"
            + ("ordinary output\n" * 300)
            + f'{{"private_key":"-----BEGIN PRIVATE KEY-----\\n{unterminated_key_body}"}}\n'
        )
        result = self.harness.run(event(
            "PostToolUse",
            tool_name="Bash",
            tool_input={"command": "credential-safety-check"},
            tool_response={"output": output, "exit_code": 0},
        ), threshold=256)
        receipt = json.loads(result.stdout)["reason"]
        for secret in (
            json_secret, private_key_body, multiline_secret, authorization_secret,
            basic_authorization_secret, escaped_key_body, prefixed_key_body,
            folded_basic_secret, unterminated_key_body,
        ):
            self.assertNotIn(secret, receipt)
        self.assertIn('"password":[REDACTED]', receipt)
        self.assertIn("[REDACTED PRIVATE KEY]", receipt)

    def test_unknown_exit_code_keeps_original_tool_result(self) -> None:
        result = self.harness.run(event(
            "PostToolUse",
            model="gpt-6-astra",
            tool_name="Bash",
            tool_input={"command": "command-with-unavailable-status"},
            tool_response={"output": "previous run exited with code 0\n" + ("x" * 5_000)},
        ))
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.data / "observations").exists())

    def test_stdout_exit_code_text_cannot_clear_verification_debt(self) -> None:
        self.record_code_change()
        self.harness.run(event(
            "PostToolUse",
            tool_name="Bash",
            tool_input={"command": "python3 -m pytest -q"},
            tool_response={"output": "previous run exited with code 0\n1 failed"},
        ))
        blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assert_pending_stop(blocked)

    def test_unknown_status_test_output_stays_inline(self) -> None:
        self.record_code_change()
        response = "1 passed\nProcess exited with code 0\n" + ("x" * 13_000)
        result = self.harness.run(event(
            "PostToolUse",
            tool_name="exec_command",
            tool_input={"cmd": "python3 -m pytest -q"},
            tool_response=response,
        ))
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.data / "observations").exists())
        blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assert_pending_stop(blocked)

    def test_compound_unknown_status_test_output_stays_inline(self) -> None:
        result = self.harness.run(event(
            "PostToolUse",
            tool_name="exec_command",
            tool_input={"cmd": "python3 edit_fixture.py && python3 -m unittest -v"},
            tool_response="Ran 3 tests\nOK\n" + ("x" * 13_000),
        ))
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.data / "observations").exists())

    def test_non_verifier_string_response_still_packs_with_unknown_status(self) -> None:
        response = "search results\n" + ("x" * 13_000)
        result = self.harness.run(event(
            "PostToolUse",
            tool_name="exec_command",
            tool_input={"cmd": "rg -n TODO src"},
            tool_response=response,
        ))
        receipt = json.loads(result.stdout)["reason"]
        self.assertIn("Status: exit_code=unknown", receipt)
        self.assertIn(f"sha256={hashlib.sha256(response.encode()).hexdigest()}", receipt)

    def test_known_status_test_output_still_packs(self) -> None:
        result = self.harness.run(event(
            "PostToolUse",
            tool_name="exec_command",
            tool_input={"cmd": "python3 -m unittest -v"},
            tool_response={"output": "Ran 3 tests\nOK\n" + ("x" * 13_000), "exit_code": 0},
        ))
        receipt = json.loads(result.stdout)["reason"]
        self.assertIn("Status: exit_code=0", receipt)

    def test_nested_command_output_cannot_spoof_structured_exit_status(self) -> None:
        self.record_code_change()
        self.harness.run(event(
            "PostToolUse",
            tool_name="exec_command",
            tool_input={"cmd": "python3 -m pytest -q"},
            tool_response={"output": {"text": "1 failed", "exit_code": 0}},
        ))
        blocked = json.loads(self.harness.run(event("Stop", stop_hook_active=False)).stdout)
        self.assert_pending_stop(blocked)

    def test_receipt_never_costs_more_context_than_the_source(self) -> None:
        output = "\n".join(f"ERROR {index:02d} " + ("x" * 330) for index in range(12))
        result = self.harness.run(event(
            "PostToolUse",
            model="gpt-6-astra",
            tool_name="Bash",
            tool_input={"command": "diagnostic-heavy-command"},
            tool_response={"output": output, "exit_code": 1},
        ), threshold=256)
        receipt = json.loads(result.stdout)["reason"]
        source_bytes = len(output.encode("utf-8"))
        receipt_bytes = len(receipt.encode("utf-8"))
        self.assertLess(receipt_bytes, source_bytes)

        report = json.loads(self.harness.report().stdout)
        self.assertEqual(report["totals"]["source_bytes"], source_bytes)
        self.assertEqual(report["totals"]["receipt_bytes"], receipt_bytes)
        self.assertEqual(report["totals"]["saved_bytes"], source_bytes - receipt_bytes)

    def test_compaction_context_preserves_pending_debt(self) -> None:
        self.record_code_change()
        precompact = self.harness.run(event("PreCompact", trigger="auto"))
        self.assertEqual(precompact.stdout, "")
        result = self.harness.run(event("SessionStart", source="compact"))
        payload = json.loads(result.stdout)
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Pending verification debt", context)
        self.assertIn("src/main.py", context)

    def test_small_shell_output_is_not_replaced(self) -> None:
        result = self.harness.run(event(
            "PostToolUse",
            tool_name="Bash",
            tool_input={"command": "printf ok"},
            tool_response={"output": "ok", "exit_code": 0},
        ))
        self.assertEqual(result.stdout, "")

    def test_host_truncated_plain_string_exceeds_default_threshold(self) -> None:
        response = "Warning: truncated output (original token count: 6183)\n" + ("x" * 8_000)
        result = self.harness.run(event(
            "PostToolUse",
            model="gpt-6-luna",
            tool_name="Bash",
            tool_input={"command": "python3 inspect_fixture.py"},
            tool_response=response,
        ))
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("threshold_bytes=6144", payload["reason"])
        self.assertIn("Status: exit_code=unknown", payload["reason"])

    def test_astra_packs_at_four_kib_while_sol_keeps_same_output_inline(self) -> None:
        output = "x" * 5_000
        astra = self.harness.run(event(
            "PostToolUse",
            model="gpt-6-astra",
            tool_name="Bash",
            tool_input={"command": "printf large"},
            tool_response={"output": output, "exit_code": 0},
        ))
        astra_payload = json.loads(astra.stdout)
        self.assertEqual(astra_payload["decision"], "block")
        self.assertIn("Model: gpt-6-astra; profile=astra; threshold_bytes=4096", astra_payload["reason"])

        sol = self.harness.run(event(
            "PostToolUse",
            model="gpt-5.6-sol",
            tool_name="Bash",
            tool_input={"command": "printf large"},
            tool_response={"output": output, "exit_code": 0},
        ))
        self.assertEqual(sol.stdout, "")

    def test_packing_metrics_are_split_by_model(self) -> None:
        for model, marker in (("gpt-6-astra", "astra"), ("gpt-5.6-sol", "sol")):
            self.harness.run(event(
                "PostToolUse",
                model=model,
                tool_name="Bash",
                tool_input={"command": f"printf {marker}"},
                tool_response={"output": marker * 2_000, "exit_code": 0},
            ), threshold=256)

        key = hashlib.sha256(b"session-under-test").hexdigest()[:24]
        state = json.loads((self.data / "state" / f"{key}.json").read_text(encoding="utf-8"))
        by_model = state["metrics_by_model"]
        self.assertEqual(by_model["gpt-6-astra"]["packed_observations"], 1)
        self.assertEqual(by_model["gpt-5.6-sol"]["packed_observations"], 1)
        self.assertGreater(by_model["gpt-6-astra"]["saved_bytes"], 0)
        self.assertGreater(by_model["gpt-5.6-sol"]["saved_bytes"], 0)

    def test_receipt_collapses_consecutive_duplicate_log_lines(self) -> None:
        output = "heartbeat\n" * 100
        result = self.harness.run(event(
            "PostToolUse",
            model="gpt-6-astra",
            tool_name="Bash",
            tool_input={"command": "long-running-check"},
            tool_response={"output": output, "exit_code": 0},
        ), threshold=256)
        receipt = json.loads(result.stdout)["reason"]
        self.assertEqual(receipt.count("> heartbeat"), 1)
        self.assertIn("repeated 100 times", receipt)

    def test_astra_session_guidance_preserves_reasoning_and_delegates_mechanics(self) -> None:
        result = self.harness.run(event("SessionStart", model="gpt-6-astra", source="startup"))
        payload = json.loads(result.stdout)
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Do not reduce Astra reasoning effort", context)
        self.assertIn("delegate deterministic discovery", context)
        self.assertIn("Sol", context)

    def test_report_exposes_savings_by_model(self) -> None:
        for model, marker in (("gpt-6-astra", "a"), ("gpt-5.6-sol", "s")):
            self.harness.run(event(
                "PostToolUse",
                model=model,
                tool_name="Bash",
                tool_input={"command": f"printf {marker}"},
                tool_response={"output": marker * 2_000, "exit_code": 0},
            ), threshold=256)

        report = json.loads(self.harness.report().stdout)
        self.assertEqual(report["totals"]["packed_observations"], 2)
        self.assertEqual(report["by_model"]["gpt-6-astra"]["packed_observations"], 1)
        self.assertEqual(report["by_model"]["gpt-5.6-sol"]["packed_observations"], 1)
        self.assertEqual(report["unattributed"]["packed_observations"], 0)

    def test_stop_reentry_is_fail_open(self) -> None:
        self.record_code_change()
        payload = json.loads(self.harness.run(event("Stop", stop_hook_active=True)).stdout)
        self.assertEqual(payload, {"continue": True})


if __name__ == "__main__":
    unittest.main()
