"""Send later (#6): the times the compose window offers, and the conversion to JMAP's UTC dates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

MORNING = 8
AFTERNOON = 14
EVENING = 18


def presets(now: datetime | None = None) -> list[tuple[str, datetime]]:
    """(label, local time) choices for "Send later", in order; only times still ahead."""
    now = now or datetime.now().astimezone()
    today = now.replace(minute=0, second=0, microsecond=0)
    out: list[tuple[str, datetime]] = []
    if now.hour < AFTERNOON - 1:
        out.append(("This afternoon", today.replace(hour=AFTERNOON)))
    if now.hour < EVENING - 1:
        out.append(("This evening", today.replace(hour=EVENING)))
    tomorrow = today + timedelta(days=1)
    out.append(("Tomorrow morning", tomorrow.replace(hour=MORNING)))
    out.append(("Tomorrow afternoon", tomorrow.replace(hour=AFTERNOON)))
    days_ahead = (7 - now.weekday()) % 7 or 7   # next Monday, never today
    monday = today + timedelta(days=days_ahead)
    out.append(("Monday morning", monday.replace(hour=MORNING)))
    return out


def to_utc(when: datetime) -> str:
    """A local datetime as the UTCDate string JMAP wants."""
    if when.tzinfo is None:
        when = when.astimezone()
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def describe(iso: str) -> str:
    """A UTCDate for the user, in local time: "Mon 8 Sep, 08:00"."""
    try:
        when = datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return iso
    return when.strftime("%a %-d %b, %H:%M")
