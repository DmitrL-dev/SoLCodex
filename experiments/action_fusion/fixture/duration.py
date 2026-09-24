"""Small duration parser used by the action-fusion development pilot."""


def parse_duration(value):
    """Return nonnegative whole seconds from a Python integer."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("duration must be an integer number of seconds")
    if value < 0:
        raise ValueError("duration must be nonnegative")
    return value
