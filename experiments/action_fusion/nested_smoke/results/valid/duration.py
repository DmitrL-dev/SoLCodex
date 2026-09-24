"""Small duration parser used by the action-fusion development pilot."""


def parse_duration(value):
    """Return nonnegative whole seconds from a Python integer."""
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, str):
            units = {"s": 1, "m": 60, "h": 3600}
            if len(value) > 1 and value[-1] in units and value[:-1].isascii() and value[:-1].isdecimal():
                return int(value[:-1]) * units[value[-1]]
            raise ValueError("invalid duration string")
        raise TypeError("duration must be an integer number of seconds")
    if value < 0:
        raise ValueError("duration must be nonnegative")
    return value
