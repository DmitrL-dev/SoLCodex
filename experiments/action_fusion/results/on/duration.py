"""Small duration parser used by the action-fusion development pilot."""

import re

_DURATION_RE = re.compile(r"([0-9]+)([smh])\Z", re.ASCII)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}


def parse_duration(value):
    """Return nonnegative whole seconds from an integer or unit string."""
    if isinstance(value, bool):
        raise TypeError("duration must be an integer or unit string")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("duration must be nonnegative")
        return value
    if isinstance(value, str):
        match = _DURATION_RE.fullmatch(value)
        if match is None:
            raise ValueError("duration must be digits followed by s, m, or h")
        amount, unit = match.groups()
        return int(amount) * _UNIT_SECONDS[unit]
    raise TypeError("duration must be an integer or unit string")
