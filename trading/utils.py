"""Utility functions shared across the trading package."""


def safe_float(value, default=0.0):
    """Safely convert a value to float, returning default on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp_percentage(value, default_value, max_value):
    """Clamp a percentage value between 0 and max_value."""
    if value is None:
        value = default_value
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = default_value
    value = max(0.0, min(value, 100.0))
    return min(value, max_value)


def append_reason(reason, note):
    """Append a note to a reason string with pipe separator."""
    if not note:
        return reason
    return f"{reason} | {note}" if reason else note
