"""UTC calendar-day helpers for /new date-window collection."""

from __future__ import annotations

from datetime import UTC, date, datetime

from uyam.calendar_window import (
    CalendarWindow,
    calendar_listing_action,
    calendar_mode,
    created_on_day,
    day_bounds_unix,
    iter_utc_days,
    parse_iso_date,
    utc_day_from_unix,
)
from uyam.sources.base import CollectionRequest


def test_parse_iso_date() -> None:
    assert parse_iso_date("2026-08-01") == date(2026, 8, 1)


def test_iter_utc_days_inclusive() -> None:
    days = list(iter_utc_days(date(2026, 8, 1), date(2026, 8, 3)))
    assert days == [date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3)]


def test_iter_utc_days_swaps_reversed() -> None:
    days = list(iter_utc_days(date(2026, 8, 3), date(2026, 8, 1)))
    assert days[0] == date(2026, 8, 1)
    assert days[-1] == date(2026, 8, 3)


def test_aug_1_2026_unix_matches_user_query() -> None:
    start, end = day_bounds_unix(date(2026, 8, 1))
    assert start == 1785542400
    assert end - start == 86400
    assert end - 1 == 1785628799


def test_created_on_day() -> None:
    day = date(2026, 8, 1)
    inside = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    before = datetime(2026, 7, 31, 23, 59, tzinfo=UTC)
    after = datetime(2026, 8, 2, 0, 0, tzinfo=UTC)
    assert created_on_day(inside, day) is True
    assert created_on_day(before, day) is False
    assert created_on_day(after, day) is False


def test_calendar_mode() -> None:
    req = CollectionRequest(
        subreddit="Philippines",
        listing_type="new",
        collection_run_id="run-1",
        calendar_since="2026-08-01",
        calendar_until="2026-08-22",
        posts_per_day=15,
    )
    assert calendar_mode(req) is True
    window = CalendarWindow.from_request(req)
    assert window is not None
    assert window.day_count == 22
    assert window.per_day == 15


def test_listing_action_newest_first() -> None:
    window = CalendarWindow(date(2026, 8, 1), date(2026, 8, 3), 2)
    taken: dict[str, int] = {}
    start, _ = day_bounds_unix(date(2026, 8, 3))
    assert (
        calendar_listing_action(
            created_unix=start + 3600, window=window, taken_on_day=taken
        )
        == "take"
    )
    taken["2026-08-03"] = 2
    assert (
        calendar_listing_action(
            created_unix=start + 100, window=window, taken_on_day=taken
        )
        == "skip_full"
    )
    before, _ = day_bounds_unix(date(2026, 7, 31))
    assert (
        calendar_listing_action(
            created_unix=before + 100, window=window, taken_on_day=taken
        )
        == "stop"
    )
    after, _ = day_bounds_unix(date(2026, 8, 4))
    assert (
        calendar_listing_action(
            created_unix=after + 100, window=window, taken_on_day=taken
        )
        == "skip_newer"
    )


def test_utc_day_from_unix() -> None:
    start, _ = day_bounds_unix(date(2026, 8, 1))
    assert utc_day_from_unix(start) == date(2026, 8, 1)
    assert utc_day_from_unix(start + 86399) == date(2026, 8, 1)
