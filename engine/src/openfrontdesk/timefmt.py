"""Human-readable times. Models make fewer date mistakes when tools return labels, not just ISO."""

from datetime import datetime
from zoneinfo import ZoneInfo


def _clock(dt: datetime) -> str:
    return f"{dt.hour % 12 or 12}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


def short_label(dt: datetime, tz: ZoneInfo) -> str:
    """'Tue Nov 3, 9:00 AM'"""
    local = dt.astimezone(tz)
    return f"{local:%a %b} {local.day}, {_clock(local)}"


def long_label(dt: datetime, tz: ZoneInfo) -> str:
    """'Monday, November 2, 2026, 8:00 AM'"""
    local = dt.astimezone(tz)
    return f"{local:%A, %B} {local.day}, {local.year}, {_clock(local)}"
