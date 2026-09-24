"""Small duration parser used by the action-fusion development pilot."""


def parse_duration(value):
    """Return nonnegative whole seconds from an integer or unit string."""
    if isinstance(value, bool):
        raise TypeError("duration must be an integer or unit string")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("duration must be nonnegative")
        return value
    if not isinstance(value, str):
        raise TypeError("duration must be an integer or unit string")

    if len(value) < 2 or value[-1] not in "smh":
        raise ValueError("duration must be ASCII digits followed by s, m, or h")
    digits = value[:-1]
    if not digits or any(char < "0" or char > "9" for char in digits):
        raise ValueError("duration must be ASCII digits followed by s, m, or h")

    amount = int(digits)
    multiplier = {"s": 1, "m": 60, "h": 3600}[value[-1]]
    return amount * multiplier
