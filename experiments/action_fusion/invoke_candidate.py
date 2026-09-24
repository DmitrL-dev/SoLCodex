"""One-call sandbox child for the external duration verifier.

This trusted launcher is copied outside the candidate workspace. Its single
wire result is checked by the controller; it contains no assertion logic.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys


WRITE = os.write
TYPE = type
STR = str
VALUE_ERROR = ValueError
TYPE_ERROR = TypeError
BYTES = bytes
LOADS = json.loads


def main() -> int:
    source = Path(sys.argv[1])
    invocation = LOADS(sys.argv[2])
    if invocation["kind"] == "literal":
        value = invocation["value"]
    elif invocation["kind"] == "bytes":
        value = BYTES(invocation["value"], "ascii")
    else:
        raise ValueError("unknown invocation kind")
    spec = importlib.util.spec_from_file_location("duration_candidate", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate could not be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        result = module.parse_duration(value)
        wire = "I:" + STR(result) if TYPE(result) is int else "O"
    except VALUE_ERROR:
        wire = "V"
    except TYPE_ERROR:
        wire = "T"
    except BaseException:
        wire = "X"
    WRITE(1, (wire + "\n").encode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
