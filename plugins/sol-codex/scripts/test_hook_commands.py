"""Verify durable hook commands and generation binding across cache removal."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class HookCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        key = "commandWindows" if os.name == "nt" else "command"
        self.commands = [
            hook[key]
            for matchers in config["hooks"].values()
            for matcher in matchers
            for hook in matcher["hooks"]
        ]
        self.assertEqual(len(self.commands), 7)
        self.assertEqual(len(set(self.commands)), 1)

    def test_commands_match_pinned_bootstrap(self) -> None:
        sys.path.insert(0, str(PLUGIN_ROOT.parents[1] / "scripts"))
        try:
            from generate_hook_commands import commands
            expected = commands()[0 if os.name != "nt" else 1]
            self.assertEqual(self.commands[0], expected)
        finally:
            sys.path.pop(0)

    def test_crlf_source_generates_same_pinned_commands(self) -> None:
        sys.path.insert(0, str(PLUGIN_ROOT.parents[1] / "scripts"))
        try:
            import generate_hook_commands as generator

            source = generator.BOOTSTRAP.read_bytes().replace(b"\r\n", b"\n")
            with tempfile.TemporaryDirectory() as directory:
                bootstrap = Path(directory) / "sol_bootstrap.py"
                with patch.object(generator, "BOOTSTRAP", bootstrap):
                    bootstrap.write_bytes(source)
                    lf_commands = generator.commands()
                    bootstrap.write_bytes(source.replace(b"\n", b"\r\n"))
                    self.assertEqual(generator.commands(), lf_commands)
        finally:
            sys.path.pop(0)

    def test_changed_bootstrap_contents_fail_pin_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "cache"
            self.fixture(root, "changed-bootstrap")
            bootstrap = root / "scripts" / "sol_bootstrap.py"
            bootstrap.write_bytes(bootstrap.read_bytes() + b"\n# modified bootstrap\n")
            result = self.invoke(root, base / "data")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("verified hook bootstrap unavailable", result.stderr)

    def invoke(self, root: Path, data: Path, session: str = "same-task") -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update({"PLUGIN_ROOT": str(root), "PLUGIN_DATA": str(data), "PYTHONDONTWRITEBYTECODE": "1"})
        return subprocess.run(
            self.commands[0],
            input=json.dumps({"session_id": session, "hook_event_name": "SessionStart"}),
            text=True, capture_output=True, shell=True, env=environment, check=False,
        )

    def fixture(self, root: Path, version: str) -> None:
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(PLUGIN_ROOT / "scripts" / "sol_bootstrap.py", scripts / "sol_bootstrap.py")
        (scripts / "sol_hook.py").write_text(
            "import json,sys\n"
            f"print(json.dumps({{'runtime': {version!r}, 'event': json.load(sys.stdin)['hook_event_name']}}))\n",
            encoding="utf-8",
        )

    def test_fresh_cache_runs_real_hook(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self.invoke(PLUGIN_ROOT, Path(directory) / "data")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("hookSpecificOutput", json.loads(result.stdout))

    def test_crlf_checkout_keeps_bootstrap_pin_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "cache"
            self.fixture(root, "crlf")
            bootstrap = root / "scripts" / "sol_bootstrap.py"
            normalized = bootstrap.read_bytes().replace(b"\r\n", b"\n")
            bootstrap.write_bytes(normalized.replace(b"\n", b"\r\n"))
            result = self.invoke(root, base / "data")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["runtime"], "crlf")

    def test_removed_cache_before_first_use_fails_open_with_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.invoke(root / "missing", root / "data")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("verified hook bootstrap unavailable", result.stderr)

    def test_snapshot_survives_prune_and_new_root_selects_new_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            old = base / "old cache"
            new = base / "new cache"
            data = base / "plugin data"
            self.fixture(old, "old")
            first = self.invoke(old, data)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout)["runtime"], "old")
            shutil.rmtree(old)
            after_prune = self.invoke(old, data)
            self.assertEqual(after_prune.returncode, 0, after_prune.stderr)
            self.assertEqual(json.loads(after_prune.stdout)["runtime"], "old")
            self.fixture(new, "new")
            upgraded = self.invoke(new, data)
            self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
            self.assertEqual(json.loads(upgraded.stdout)["runtime"], "new")
            old_inflight = self.invoke(old, data)
            self.assertEqual(json.loads(old_inflight.stdout)["runtime"], "old")
            shutil.rmtree(new)
            new_after_prune = self.invoke(new, data)
            self.assertEqual(json.loads(new_after_prune.stdout)["runtime"], "new")

    def test_new_task_recovers_unique_snapshot_for_removed_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "cache"
            data = base / "data"
            self.fixture(root, "saved")
            self.assertEqual(self.invoke(root, data).returncode, 0)
            shutil.rmtree(root)
            result = self.invoke(root, data, session="later-task")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["runtime"], "saved")

    def test_same_root_changed_runtime_is_rejected_for_bound_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "cache"
            data = base / "data"
            self.fixture(root, "old")
            self.assertEqual(self.invoke(root, data).returncode, 0)
            (root / "scripts" / "sol_hook.py").write_text("print('changed')\n", encoding="utf-8")
            result = self.invoke(root, data)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("plugin root reused with different runtime", result.stderr)

    def test_corrupt_snapshot_is_rejected_after_prune(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "cache"
            data = base / "data"
            self.fixture(root, "saved")
            self.assertEqual(self.invoke(root, data).returncode, 0)
            snapshot = next((data / "runtime-v1" / "snapshots").glob("*.py"))
            snapshot.write_text("print('corrupt')\n", encoding="utf-8")
            shutil.rmtree(root)
            result = self.invoke(root, data)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("runtime snapshot digest mismatch", result.stderr)

    def test_ambiguous_unbound_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "cache"
            data = base / "data"
            self.fixture(root, "first")
            self.assertEqual(self.invoke(root, data, session="first-task").returncode, 0)
            (root / "scripts" / "sol_hook.py").write_text("print('second')\n", encoding="utf-8")
            self.assertEqual(self.invoke(root, data, session="second-task").returncode, 0)
            shutil.rmtree(root)
            result = self.invoke(root, data, session="unbound-task")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("runtime unavailable or ambiguous", result.stderr)


if __name__ == "__main__":
    unittest.main()
