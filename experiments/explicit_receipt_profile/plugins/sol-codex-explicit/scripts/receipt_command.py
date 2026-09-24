#!/usr/bin/env python3
"""Experimental explicit-command capture; Python 3.9+, POSIX, stdlib only.

Usage: receipt_command.py --artifact-dir PRIVATE_DIRECTORY -- PROGRAM [ARG ...]
Runs argv without a shell. The caller owns artifact retention/deletion and disk
capacity. Output bytes are unrestricted on disk; memory and the JSON receipt are
bounded. This is not a sandbox, comprehensive redactor, hook, or transparent tool replacement.
Normal child exit codes are preserved; signal deaths use 128+N at the CLI and
the exact negative subprocess return code in exit_code. Adapter failures use
125 with exit_code=null. stdin is closed. SIGKILL cannot produce a receipt.
Preview text is untrusted child output, decoded with UTF-8 replacement. stdout
and stderr share one pipe; ordering is the order observed on that pipe. Commands
must close inherited output descriptors (including in background descendants).
"""

import argparse
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time


MAX_RECEIPT_BYTES = 3072
CHUNK_BYTES = 65536
LINE_BYTES = 240
DIAGNOSTICS = 6
STOP_GRACE_SECONDS = 0.5
ERROR_SIGNAL = re.compile(rb"\b(?:error|fatal|fail(?:ed|ure)?|exception|traceback|panic|assertion)\b", re.I)
WARNING_SIGNAL = re.compile(rb"\bwarning\b", re.I)
EXPLICIT_SIGNAL = re.compile(
    rb"^(?:[ \t]*(?:ERROR|FATAL|PANIC|FAIL(?:ED|URE)?|Traceback)\b"
    rb"|E[ \t]{2,}\S|[ \t]*>?[ \t]*assert\b"
    rb"|[ \t]*[\w.]+(?:Error|Exception):"
    rb"|[ \t]*(?:[A-Za-z]:)?[^:\n]{1,160}:[0-9]+(?::[0-9]+)?:[ \t]*(?:fatal[ \t]+)?error\b)", re.I)
WARNING_PREFIX = re.compile(rb"^[ \t]*warning\b", re.I)
SECRET_SUBSTITUTIONS = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*)[^\r\n]*"), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[REDACTED]"),
    (re.compile(r"(?i)\b(?:authorization|api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s,;}]+"), "[REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[REDACTED]"),
)


def redact(text):
    for pattern, replacement in SECRET_SUBSTITUTIONS:
        text = pattern.sub(replacement, text)
    return text


class Summary:
    """Streaming byte accounting and bounded samples, even for newline-free data."""

    def __init__(self):
        self.digest = hashlib.sha256()
        self.size = 0
        self.lines = 0
        self.head = []
        self.tail = deque(maxlen=2)
        self.candidates = []
        self.prefix = b""
        self.overlap = b""
        self.line_size = 0
        self.error = False
        self.warning = False

    def feed(self, data):
        self.digest.update(data)
        self.size += len(data)
        parts = data.split(b"\n")
        for index, part in enumerate(parts):
            self.prefix += part[:max(0, LINE_BYTES - len(self.prefix))]
            self.line_size += len(part)
            window = self.overlap + part
            self.error = self.error or bool(ERROR_SIGNAL.search(window))
            self.warning = self.warning or bool(WARNING_SIGNAL.search(window))
            self.overlap = window[-32:]
            if index != len(parts) - 1:
                self.finish_line()

    def finish_line(self):
        self.lines += 1
        item = {"line": self.lines, "text": self.prefix.decode("utf-8", "replace"),
                "truncated": self.line_size > LINE_BYTES}
        if item["truncated"]:
            item["text"] = "[overlong line omitted; inspect private artifact]"
        if len(self.head) < 2:
            self.head.append(item)
        self.tail.append(item)
        priority = 0
        if EXPLICIT_SIGNAL.search(self.prefix):
            priority = 3
        elif WARNING_PREFIX.search(self.prefix):
            priority = 1
        elif self.error:
            priority = 2
        elif self.warning:
            priority = 1
        if priority:
            if item["truncated"]:
                priority = min(priority, 2)
                item = dict(item, text="Diagnostic on overlong line; inspect private artifact")
            candidate = (priority, item["line"], item)
            if len(self.candidates) < DIAGNOSTICS:
                self.candidates.append(candidate)
            else:
                weakest = min(range(len(self.candidates)),
                              key=lambda index: (self.candidates[index][0],
                                                 -self.candidates[index][1]))
                if priority > self.candidates[weakest][0]:
                    self.candidates[weakest] = candidate
        self.prefix = self.overlap = b""
        self.line_size = 0
        self.error = self.warning = False

    @property
    def diagnostics(self):
        return [item for _, _, item in sorted(self.candidates,
                                               key=lambda entry: (-entry[0], entry[1]))]

    def finish(self):
        if self.line_size:
            self.finish_line()


def create_artifact(directory):
    """Reject shared/symlink roots; create a private random run directory/file."""
    root = Path(directory).absolute()
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("artifact directory must be owned by this user with mode 0700")
    # A private per-run directory isolates names from concurrent invocations.
    run_dir = tempfile.mkdtemp(prefix="receipt-", dir=str(root.resolve()))
    os.chmod(run_dir, 0o700)
    fd, path = tempfile.mkstemp(prefix="output-", suffix=".bin", dir=run_dir)
    try:
        os.fchmod(fd, 0o600)
        if len(json.dumps(path).encode("ascii")) > MAX_RECEIPT_BYTES // 2:
            raise ValueError("artifact path is too long for a bounded receipt")
        return os.fdopen(fd, "wb", buffering=0), path
    except BaseException:
        os.close(fd)
        raise


def stop_group(child, signum):
    try:
        os.killpg(child.pid, signum)
    except ProcessLookupError:
        pass


def capture(argv, artifact, interrupted, timeout_seconds):
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             shell=False, start_new_session=True)
    deadline = None
    timeout_at = time.monotonic() + timeout_seconds
    timed_out = False
    eof = False
    finished = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while not eof or child.poll() is None:
                if interrupted[0] and deadline is None:
                    stop_group(child, interrupted[0])
                    deadline = time.monotonic() + STOP_GRACE_SECONDS
                if time.monotonic() >= timeout_at and deadline is None:
                    timed_out = True
                    stop_group(child, signal.SIGTERM)
                    deadline = time.monotonic() + STOP_GRACE_SECONDS
                if deadline is not None and time.monotonic() >= deadline:
                    stop_group(child, signal.SIGKILL)
                    # Descendants can escape the group and hold the pipe open.
                    # On cancellation, finalize only the bytes captured so far.
                    break
                for key, _ in selector.select(timeout=0.05):
                    data = os.read(key.fileobj.fileno(), CHUNK_BYTES)
                    if not data:
                        selector.unregister(key.fileobj)
                        eof = True
                        continue
                    view = memoryview(data)
                    while view:
                        written = artifact.write(view)
                        if not written:
                            raise OSError("artifact write made no progress")
                        view = view[written:]
        code = child.wait()
        finished = True
        return code, eof, timed_out
    finally:
        if not finished or interrupted[0] or timed_out:
            stop_group(child, signal.SIGKILL)
        child.wait()
        child.stdout.close()


def emit(receipt):
    for field in ("head", "tail", "diagnostics"):
        for item in receipt[field]:
            item["text"] = redact(item["text"])
    def encode():
        return (json.dumps(receipt, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
    payload = encode()
    while len(payload) > MAX_RECEIPT_BYTES:
        for field in ("tail", "head", "diagnostics"):
            if receipt[field]:
                receipt[field].pop()
                receipt["preview_limited"] = True
                break
        else:
            raise ValueError("receipt metadata exceeds limit")
        payload = encode()
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.command or args.command[0] != "--" or len(args.command) == 1:
        parser.error("supply -- PROGRAM [ARG ...]")
    if os.name != "posix":
        parser.error("this research prototype requires POSIX process groups")
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be finite and positive")

    receipt = {"schema": "solcodex.command-receipt.v1", "status": "capture_error",
               "exit_code": None, "wrapper_exit_code": 125, "signal": None,
               "interrupted_by": None, "capture_complete": False,
               "path": None, "bytes": None, "sha256": None, "lines": None,
               "head": [], "tail": [], "diagnostics": [], "preview_limited": False,
               "output_trust": "untrusted child output"}
    interrupted = [None]
    previous = {}
    artifact = None
    phase = "artifact_setup"

    def on_signal(signum, _frame):
        if interrupted[0] is None:
            interrupted[0] = signum

    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.signal(signum, on_signal)
        artifact, receipt["path"] = create_artifact(args.artifact_dir)
        if not interrupted[0]:
            phase = "spawn_or_capture"
            code, complete, timed_out = capture(args.command[1:], artifact, interrupted,
                                                args.timeout_seconds)
            receipt.update(exit_code=code, signal=-code if code < 0 else None,
                           wrapper_exit_code=code if code >= 0 else 128 - code,
                           capture_complete=complete, status="completed")
            if timed_out:
                receipt.update(status="timeout", wrapper_exit_code=124,
                               capture_complete=False)
    except (OSError, ValueError) as exc:
        # Never echo exception text: it may contain argv, paths or child data.
        receipt["error"] = {"phase": phase, "type": type(exc).__name__,
                            "errno": getattr(exc, "errno", None)}
    finally:
        if artifact is not None:
            artifact.close()
    try:
        if receipt["path"] is not None:
            summary = Summary()
            with open(receipt["path"], "rb") as source:
                while True:
                    chunk = source.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    summary.feed(chunk)
            summary.finish()
            receipt.update(bytes=summary.size, sha256=summary.digest.hexdigest(),
                           lines=summary.lines, head=summary.head,
                           tail=list(summary.tail), diagnostics=summary.diagnostics)
    except OSError as exc:
        receipt.update(status="capture_error", capture_complete=False,
                       wrapper_exit_code=125,
                       error={"phase": "artifact_read", "type": type(exc).__name__,
                              "errno": exc.errno})
    if interrupted[0]:
        receipt.update(status="interrupted", interrupted_by=interrupted[0],
                       wrapper_exit_code=128 + interrupted[0], capture_complete=False)
    try:
        emit(receipt)
    except (OSError, ValueError):
        return 125
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return receipt["wrapper_exit_code"]


if __name__ == "__main__":
    sys.exit(main())
