"""Materialize exposed SymPy #26807 source variants from a pinned parent export."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


SOURCE_FILE = Path("sympy/tensor/array/expressions/array_expressions.py")
EXPECTED = "1736959e0f0f29997274200f099c233809d1ecd7afc73062bfba90fdd460daeb"
PARENT_EXPORT = "bd900fce6a20f1a4fa15991ee179d1c7ff362743ac9435eb9c503972f6f71132"
KINDS = (
    "superclass_noniterable", "array_symbol_property", "rank_one_only",
    "concrete_extent_only", "identifier_only", "zero_sibling", "one_sibling",
    "blind_superclass_before_shape",
)


def export_digest(root: Path) -> tuple[int, str]:
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("source export contains a symlink")
        if path.is_file():
            rows.append([path.relative_to(root).as_posix(),
                         hashlib.sha256(path.read_bytes()).hexdigest()])
        elif not path.is_dir():
            raise ValueError("source export contains a nonregular entry")
    encoded = json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()
    return len(rows), hashlib.sha256(encoded).hexdigest()


def variant(source: str, kind: str) -> str:
    if kind == "blind_superclass_before_shape":
        before = "class _ArrayExpr(Expr):\n"
        after = before + "    _iterable = False\n"
    elif kind == "superclass_noniterable":
        before = "class _ArrayExpr(Expr):\n    shape: tTuple[Expr, ...]\n"
        after = before + "    _iterable = False\n"
    elif kind == "array_symbol_property":
        before = "class ArraySymbol(_ArrayExpr):\n    \"\"\"\n    Symbol representing an array expression\n    \"\"\"\n\n"
        after = before + "    @property\n    def _iterable(self):\n        return False\n\n"
    elif kind in {"rank_one_only", "concrete_extent_only", "identifier_only",
                  "zero_sibling", "one_sibling"}:
        before = "class ArraySymbol(_ArrayExpr):\n    \"\"\"\n    Symbol representing an array expression\n    \"\"\"\n\n"
        body = {
            "rank_one_only": "    @property\n    def _iterable(self):\n        return len(self.shape) != 1\n\n",
            "concrete_extent_only": "    @property\n    def _iterable(self):\n        return not all(i.is_Integer for i in self.shape)\n\n",
            "identifier_only": "    @property\n    def _iterable(self):\n        return not self.name.name.isidentifier()\n\n",
            "zero_sibling": "    _iterable = False\n\n",
            "one_sibling": "    _iterable = False\n\n",
        }[kind]
        after = before + body
    else:
        raise ValueError(kind)
    if source.count(before) != 1:
        raise ValueError("expected source anchor exactly once")
    changed = source.replace(before, after)
    if kind in {"zero_sibling", "one_sibling"}:
        sibling = "ZeroArray" if kind == "zero_sibling" else "OneArray"
        anchor = f"class {sibling}(_ArrayExpr):\n"
        if changed.count(anchor) != 1:
            raise ValueError("expected sibling source anchor exactly once")
        changed = changed.replace(anchor, anchor + "    _iterable = False\n")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", choices=KINDS, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    if args.output.resolve().is_relative_to(repo):
        raise ValueError("variant output must be outside the public repository")
    if export_digest(args.parent) != (2061, PARENT_EXPORT):
        raise ValueError("wrong parent export")
    source = (args.parent / SOURCE_FILE).read_bytes()
    if hashlib.sha256(source).hexdigest() != EXPECTED:
        raise ValueError("wrong parent source")
    shutil.copytree(args.parent, args.output)
    target = args.output / SOURCE_FILE
    mutated = variant(source.decode(), args.kind).encode()
    target.write_bytes(mutated)
    (args.output.parent / f"{args.output.name}-manifest.json").write_text(json.dumps({
        "kind": args.kind, "parent_file_sha256": EXPECTED,
        "parent_export_sha256": PARENT_EXPORT,
        "variant_export_sha256": export_digest(args.output)[1],
        "variant_file_sha256": hashlib.sha256(mutated).hexdigest(),
    }, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
