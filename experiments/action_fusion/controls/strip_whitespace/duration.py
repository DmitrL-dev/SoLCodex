"""Reference implementation for the action-fusion development preflight."""

def parse_duration(value):
    if isinstance(value, bool):
        raise TypeError("boolean is not a duration")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("negative duration")
        return value
    if isinstance(value, str):
        value = value.strip()
        units = {"s": 1, "m": 60, "h": 3600}
        if len(value) > 1 and value[-1] in units and value[:-1].isascii() and value[:-1].isdecimal():
            return int(value[:-1]) * units[value[-1]]
        raise ValueError("invalid duration string")
    raise TypeError("unsupported duration type")
