#!/usr/bin/env python3
"""Experimental bounded literal search of an exact receipt_command artifact.

POSIX, Python 3.9+, stdlib only. The full artifact remains local. This tool is
not a security boundary or a substitute for independent task verification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from receipt_command import redact


SCHEMA = "solcodex.receipt-search.v1"
MAX_RESULT_BYTES = 2048
MAX_MATCHES = 4
MAX_LINE_BYTES = 240
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


def encoded(value):
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")


def search(path: Path, expected_sha256: str, literal: str = None, start_line: int = None):
    if os.name != "posix":
        raise ValueError("unsupported_platform")
    if not SHA256.fullmatch(expected_sha256):
        raise ValueError("invalid_sha256")
    if (literal is None) == (start_line is None):
        raise ValueError("choose_literal_or_line")
    if literal is not None:
        if not literal or "\n" in literal or "\r" in literal or len(literal.encode("utf-8")) > 256:
            raise ValueError("invalid_literal")
        needle = literal.encode("utf-8")
    elif type(start_line) is not int or start_line < 1:
        raise ValueError("invalid_line")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError("artifact_unavailable") from None
    with os.fdopen(descriptor, "rb") as stream:
        details = os.fstat(stream.fileno())
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("artifact_not_regular")
        if details.st_size > MAX_ARTIFACT_BYTES:
            raise ValueError("artifact_too_large")
        digest = hashlib.sha256()
        shown = []
        match_count = 0
        omitted_long_matches = 0
        line_count = 0
        total_bytes = 0
        for line_count, line in enumerate(stream, 1):
            total_bytes += len(line)
            if total_bytes > MAX_ARTIFACT_BYTES:
                raise ValueError("artifact_too_large")
            digest.update(line)
            selected = (needle in line if literal is not None
                        else start_line <= line_count < start_line + MAX_MATCHES)
            if not selected:
                continue
            match_count += 1
            long_line = len(line.rstrip(b"\r\n")) > MAX_LINE_BYTES
            if long_line:
                omitted_long_matches += 1
            if len(shown) == MAX_MATCHES:
                continue
            if long_line:
                display = "[line omitted: over 240 bytes]"
            else:
                display = redact(line.rstrip(b"\r\n").decode("utf-8", "replace"))
            shown.append({"line": line_count, "text": display})
    if digest.hexdigest() != expected_sha256:
        raise ValueError("hash_mismatch")
    result = {"schema": SCHEMA, "status": "ok", "sha256": expected_sha256,
              "line_count": line_count, "match_count": match_count,
              "matches": shown, "omitted_long_matches": omitted_long_matches,
              "truncated": match_count > len(shown) or omitted_long_matches > 0}
    while len(encoded(result)) > MAX_RESULT_BYTES and result["matches"]:
        result["matches"].pop()
        result["truncated"] = True
    if len(encoded(result)) > MAX_RESULT_BYTES:
        raise ValueError("result_metadata_too_large")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--literal")
    choice.add_argument("--line", type=int)
    args = parser.parse_args(argv)
    try:
        result = search(args.artifact, args.sha256, args.literal, args.line)
        code = 0
    except ValueError as error:
        result = {"schema": SCHEMA, "status": "error", "error": str(error)}
        code = 2
    sys.stdout.buffer.write(encoded(result))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
