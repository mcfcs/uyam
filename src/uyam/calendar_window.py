"""UTC calendar-day windows for historical Reddit collection.

Reddit's live /new feed cannot seek to a date. The old search operator

    timestamp:<unix>..<unix>

is **dead on current Reddit search** (the UI searches it as a literal string
and returns "we couldn't find any results"). Calendar mode therefore walks
``/r/{sub}/new/`` newest-first, buckets posts by UTC day, keeps up to N posts
per day, and stops once created_utc is older than the From date.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from uyam.sources.base import CollectionRequest

CalendarAction = Literal["stop", "skip_newer", "skip_full", "take"]


def parse_iso_date(value: str) -> date:
    """Parse YYYY-MM-DD. Raises ValueError on bad input."""
    return date.fromisoformat(value.strip())


def iter_utc_days(since: date, until: date) -> Iterator[date]:
    """Inclusive UTC day range. Swaps if since > until."""
    start, end = (until, since) if since > until else (since, until)
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def day_bounds_unix(day: date) -> tuple[int, int]:
    """Return [start, end) Unix seconds for a UTC calendar day."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def utc_day_from_unix(ts: float) -> date:
    if ts > 1e12:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, tz=UTC).date()


def created_on_day(created_utc: datetime, day: date) -> bool:
    start, end = day_bounds_unix(day)
    aware = created_utc if created_utc.tzinfo else created_utc.replace(tzinfo=UTC)
    ts = aware.timestamp()
    return start <= ts < end


def calendar_mode(request: CollectionRequest) -> bool:
    return bool(request.calendar_since and request.posts_per_day and request.posts_per_day > 0)


class CalendarWindow:
    """Inclusive UTC From/Until with a per-day harvest cap."""

    def __init__(self, since: date, until: date, per_day: int) -> None:
        if since > until:
            since, until = until, since
        self.since = since
        self.until = until
        self.per_day = max(1, int(per_day))
        self.start_unix, _ = day_bounds_unix(since)
        _, self.end_unix = day_bounds_unix(until)

    @classmethod
    def from_request(cls, request: CollectionRequest) -> CalendarWindow | None:
        if not calendar_mode(request):
            return None
        since = parse_iso_date(str(request.calendar_since))
        until_raw = request.calendar_until or datetime.now(UTC).date().isoformat()
        until = parse_iso_date(str(until_raw))
        return cls(since, until, int(request.posts_per_day or 15))

    @property
    def day_count(self) -> int:
        return (self.until - self.since).days + 1

    def scan_cap(self) -> int:
        """Max /new posts to inspect while walking back to From."""
        return min(4000, max(300, self.day_count * 80))


def calendar_listing_action(
    *,
    created_unix: float,
    window: CalendarWindow,
    taken_on_day: dict[str, int],
) -> CalendarAction:
    """Decide what to do with one /new listing card (newest-first order)."""
    ts = created_unix / 1000.0 if created_unix > 1e12 else created_unix
    if ts >= window.end_unix:
        return "skip_newer"
    if ts < window.start_unix:
        return "stop"
    day = utc_day_from_unix(ts).isoformat()
    if taken_on_day.get(day, 0) >= window.per_day:
        return "skip_full"
    return "take"
