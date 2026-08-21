"""Unit tests for ShredditBrowserSource construction (no live browser)."""

from __future__ import annotations

import pytest

from uyam.sources.proxy_pool import ProxyPool
from uyam.sources.shreddit import (
    ShredditBrowserSource,
    _exc_text,
    _json_created_in_window,
    _merge_comment_dicts,
    _nav_error_kind,
    _reddit_id_from_permalink,
    submission_already_seen,
)


def test_requires_non_empty_proxy_pool() -> None:
    with pytest.raises(RuntimeError, match="proxies.txt"):
        ShredditBrowserSource(proxy_pool=ProxyPool([]))


def test_exc_text_uses_playwright_message() -> None:
    class PlaywrightError(Exception):
        def __init__(self) -> None:
            super().__init__("")
            self.message = "net::ERR_ABORTED at https://www.reddit.com/r/x/"

        def __str__(self) -> str:
            return ""

    assert "ERR_ABORTED" in _exc_text(PlaywrightError())
    assert "x" in _exc_text(Exception("timeout 45000ms exceeded"))


def test_nav_error_kind() -> None:
    assert _nav_error_kind(Exception("net::ERR_TUNNEL_CONNECTION_FAILED")) == "proxy"
    closed = "page.goto: Target page, context or browser has been closed"
    assert _nav_error_kind(Exception(closed)) == "closed"
    assert _nav_error_kind(RuntimeError("reddit_block_or_challenge")) == "block"
    assert _nav_error_kind(Exception("Timeout 45000ms exceeded")) == "transient"
    assert _nav_error_kind(Exception()) == "transient"


def test_permalink_id() -> None:
    assert (
        _reddit_id_from_permalink(
            "/r/Philippines/comments/1vthze0/group_of_pickpockets/"
        )
        == "1vthze0"
    )


def test_page_is_open_without_browser() -> None:
    src = ShredditBrowserSource(proxy_pool=ProxyPool(["http://127.0.0.1:9"]))
    assert src._page_is_open() is False


def test_json_created_in_window() -> None:
    start, end = 1754006400, 1754092800  # placeholders; relative checks below
    raw = {"created_utc": start + 3600}
    assert _json_created_in_window(raw, start, end) is True
    assert _json_created_in_window({"created_utc": start - 1}, start, end) is False
    assert _json_created_in_window({"created_utc": end}, start, end) is False
    assert _json_created_in_window({}, start, end) is True


def test_skip_seen_permalink() -> None:
    permalink = "/r/Philippines/comments/1vthze0/group_of_pickpockets/"
    assert submission_already_seen(permalink, None) is False
    assert submission_already_seen(permalink, lambda fn: fn == "t3_1vthze0") is True
    assert submission_already_seen(permalink, lambda fn: False) is False


def test_merge_prefers_primary() -> None:
    primary = [{"id": "a", "body": "json"}]
    extra = [{"id": "a", "body": "dom"}, {"id": "b", "body": "only-dom"}]
    merged = _merge_comment_dicts(primary, extra)
    assert [c["id"] for c in merged] == ["a", "b"]
    assert merged[0]["body"] == "json"
