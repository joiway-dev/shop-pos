"""Time helpers. The database stores naive UTC; the shop sees local time
(the shop computer's time zone, i.e. Thailand)."""

from datetime import UTC, datetime


def now_local() -> datetime:
    return datetime.now().astimezone()


def to_local(utc_naive: datetime | None) -> datetime | None:
    if utc_naive is None:
        return None
    return utc_naive.replace(tzinfo=UTC).astimezone()
