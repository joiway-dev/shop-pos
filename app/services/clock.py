"""Time helpers. The database stores naive UTC; the shop sees local time
(the shop computer's time zone, i.e. Thailand)."""

from datetime import UTC, date, datetime, time, timedelta


def now_local() -> datetime:
    return datetime.now().astimezone()


def local_range_to_utc(start: date, end: date) -> tuple[datetime, datetime]:
    """Local dates [start, end] inclusive -> naive UTC [from, to) for DB filters."""
    lo = datetime.combine(start, time.min).astimezone().astimezone(UTC).replace(tzinfo=None)
    hi = datetime.combine(end + timedelta(days=1), time.min).astimezone().astimezone(UTC).replace(tzinfo=None)
    return lo, hi


def to_local(utc_naive: datetime | None) -> datetime | None:
    if utc_naive is None:
        return None
    return utc_naive.replace(tzinfo=UTC).astimezone()
