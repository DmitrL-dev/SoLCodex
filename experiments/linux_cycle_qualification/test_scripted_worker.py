"""Check that the scripted worker reports missing final usage as failure."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from experiments.linux_cycle_qualification import scripted_worker


class Response:
    status = 200

    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self.body


class ScriptedWorkerTest(unittest.TestCase):
    def check(self, scenario: str, body: bytes) -> tuple[int, list[dict], bytes]:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "duration.py").write_bytes(
                (Path(__file__).resolve().parents[1] / "action_fusion/fixture/duration.py").read_bytes()
            )
            canaries = {key: str(work / (key + "-absent")) for key in
                        ("GOLD_CANARY", "SIBLING_CANARY", "HOST_CANARY", "FAKE_AUTH_CANARY")}
            canaries.update({"UPSTREAM_GATEWAY": "127.0.0.1", "UPSTREAM_PORT": "1"})
            output = io.StringIO()
            original_path = Path
            with (patch.dict(os.environ, canaries),
                  patch.object(sys, "argv", ["worker", scenario]),
                  patch.object(scripted_worker, "Path",
                               side_effect=lambda value: work if value == "/work" else original_path(value)),
                  patch.object(scripted_worker, "direct_upstream_denied", return_value=True),
                  patch.object(scripted_worker, "urlopen", return_value=Response(body)),
                  redirect_stdout(output)):
                exit_code = scripted_worker.main()
            logs = [json.loads(line) for line in output.getvalue().splitlines()]
            return exit_code, logs, (work / "duration.py").read_bytes()

    def test_missing_completion_is_failure(self) -> None:
        code, logs, source = self.check("no_completion", b"")
        self.assertEqual(code, 4)
        self.assertFalse(logs[-1]["completion_seen"])
        self.assertEqual(scripted_worker.digest(source), scripted_worker.PARENT_SHA256)

    def test_completed_response_allows_patch(self) -> None:
        code, logs, source = self.check("normal", b'{"type":"response.completed"}')
        self.assertEqual(code, 0)
        self.assertTrue(logs[-1]["completion_seen"])
        self.assertEqual(scripted_worker.digest(source), scripted_worker.GOLD_SHA256)


if __name__ == "__main__":
    unittest.main()
