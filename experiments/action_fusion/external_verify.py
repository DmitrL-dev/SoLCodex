"""Trusted expected observations for the action-fusion duration fixture.

The evaluator invokes candidate code in separate sandboxed children, then
compares each bounded wire result against these expectations outside them.
This module never imports or executes candidate code.
"""
from __future__ import annotations


# (case ID, JSON-serializable invocation specification, exact expected wire line)
CASES = (
    ("int_zero", {"kind": "literal", "value": 0}, "I:0"),
    ("int_one", {"kind": "literal", "value": 1}, "I:1"),
    ("int_large", {"kind": "literal", "value": 10**12}, "I:1000000000000"),
    ("int_hour", {"kind": "literal", "value": 3600}, "I:3600"),
    ("seconds_zero", {"kind": "literal", "value": "0s"}, "I:0"),
    ("seconds_leading_zero", {"kind": "literal", "value": "09s"}, "I:9"),
    ("minutes", {"kind": "literal", "value": "4m"}, "I:240"),
    ("hours", {"kind": "literal", "value": "12h"}, "I:43200"),
    ("negative_int", {"kind": "literal", "value": -1}, "V"),
    ("negative_seconds", {"kind": "literal", "value": "-1s"}, "V"),
    ("negative_minutes", {"kind": "literal", "value": "-2m"}, "V"),
    ("negative_hours", {"kind": "literal", "value": "-3h"}, "V"),
    ("empty_string", {"kind": "literal", "value": ""}, "V"),
    ("missing_unit", {"kind": "literal", "value": "1"}, "V"),
    ("wrong_unit", {"kind": "literal", "value": "1d"}, "V"),
    ("uppercase_unit", {"kind": "literal", "value": "1S"}, "V"),
    ("positive_sign", {"kind": "literal", "value": "+1s"}, "V"),
    ("fractional_seconds", {"kind": "literal", "value": "1.5s"}, "V"),
    ("double_suffix", {"kind": "literal", "value": "1ss"}, "V"),
    ("extra_suffix", {"kind": "literal", "value": "1sfoo"}, "V"),
    ("leading_space", {"kind": "literal", "value": " 1s"}, "V"),
    ("trailing_space", {"kind": "literal", "value": "1s "}, "V"),
    ("trailing_newline", {"kind": "literal", "value": "1s\n"}, "V"),
    ("leading_tab", {"kind": "literal", "value": "\t1s"}, "V"),
    ("trailing_tab", {"kind": "literal", "value": "1s\t"}, "V"),
    ("trailing_cr", {"kind": "literal", "value": "1s\r"}, "V"),
    ("unicode_digit", {"kind": "literal", "value": "١s"}, "V"),
    ("bool_true", {"kind": "literal", "value": True}, "T"),
    ("bool_false", {"kind": "literal", "value": False}, "T"),
    ("float", {"kind": "literal", "value": 1.0}, "T"),
    ("none", {"kind": "literal", "value": None}, "T"),
    ("bytes", {"kind": "bytes", "value": "1s"}, "T"),
    ("list", {"kind": "literal", "value": []}, "T"),
    ("dict", {"kind": "literal", "value": {}}, "T"),
)


def judge(observations: dict[str, str]) -> dict[str, bool]:
    expected = {name: value for name, _, value in CASES}
    if len(expected) != len(CASES) or set(observations) != set(expected):
        raise ValueError("incomplete or duplicate external case report")
    return {name: observations[name] == value for name, value in expected.items()}
