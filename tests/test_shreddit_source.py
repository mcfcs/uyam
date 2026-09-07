"""Unit tests for ShredditBrowserSource construction (no live browser)."""

from __future__ import annotations

import pytest

from uyam.sources.base import CollectionRequest
from uyam.sources.proxy_pool import ProxyPool
from uyam.sources.shreddit import (
    ShredditBrowserSource,
    _exc_text,
    _json_created_in_window,
    _json_status_kind,
    _merge_comment_dicts,
    _nav_error_kind,
    _reddit_id_from_permalink,
    listing_card_to_raw,
    page_text_is_captcha,
    page_text_is_whoa,
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
    assert _nav_error_kind(RuntimeError("reddit_whoa")) == "whoa"
    assert _nav_error_kind(Exception("Timeout 45000ms exceeded")) == "transient"
    assert _nav_error_kind(Exception()) == "transient"
    http_fail = "Page.goto: net::ERR_HTTP_RESPONSE_CODE_FAILURE at https://x"
    assert _nav_error_kind(Exception(http_fail)) == "http_status"
    assert _nav_error_kind(Exception("HTTP 429 too many requests")) == "rate_limit"


def test_whoa_page_is_not_a_captcha() -> None:
    whoa = (
        "whoa there, pardner!\n"
        "We've seen far too many requests come from your IP address recently.\n"
        "Please wait a few minutes and try again."
    )
    assert page_text_is_whoa(whoa) is True
    assert page_text_is_captcha(whoa) is False
    captcha = "Verify you are human to continue"
    assert page_text_is_whoa(captcha) is False
    assert page_text_is_captcha(captcha) is True


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


def test_json_status_kind_does_not_treat_403_or_429_as_rate_limit() -> None:
    assert _json_status_kind(200) == "ok"
    assert _json_status_kind(429) == "unavailable"
    assert _json_status_kind(403) == "unavailable"
    assert _json_status_kind(401) == "unavailable"
    assert _json_status_kind(500) == "http_error"


def test_parse_json_http_result_429_does_not_raise() -> None:
    src = ShredditBrowserSource(proxy_pool=ProxyPool(["http://127.0.0.1:9"]))
    url = "https://www.reddit.com/r/Philippines/new.json"
    assert src._parse_json_http_result(url, status=403, text="blocked") is None
    assert src._parse_json_http_result(url, status=429, text="slow down") is None
    payload = src._parse_json_http_result(
        url, status=200, text='{"kind":"Listing","data":{"children":[]}}'
    )
    assert payload["kind"] == "Listing"


def test_listing_card_to_raw() -> None:
    raw = listing_card_to_raw(
        {
            "id": "t3_abc123",
            "permalink": "/r/Philippines/comments/abc123/x/",
            "created": "2026-08-05T01:00:00.000000+0000",
            "promoted": False,
        }
    )
    assert raw is not None
    assert raw["id"] == "abc123"
    assert raw["permalink"].endswith("/abc123/x/")
    assert raw["created_utc"] == 1785891600.0
    assert listing_card_to_raw({"id": "t3_ad", "permalink": "/r/x/", "promoted": True}) is None
    assert listing_card_to_raw({"id": "abc", "permalink": "/r/x/"}) is None


def test_json_listing_unavailable_does_not_rotate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = ShredditBrowserSource(proxy_pool=ProxyPool(["http://127.0.0.1:9"]))

    def _boom(**_kwargs: object) -> None:
        raise AssertionError("JSON 403/429 must not rotate proxies")

    monkeypatch.setattr(src, "_rotate_proxy", _boom)
    monkeypatch.setattr(src, "_fetch_json", lambda _url: None)
    req = CollectionRequest(
        subreddit="Philippines", listing_type="new", collection_run_id="run-1"
    )
    assert src._json_listing(req, fetch_limit=10) == []


def test_mark_json_unavailable_skips_later_fetches() -> None:
    src = ShredditBrowserSource(proxy_pool=ProxyPool(["http://127.0.0.1:9"]))
    src._mark_json_unavailable(reason="unavailable", path="/r/x/new.json", status=429)
    assert src._json_mode == "off"
    assert src._fetch_json("https://www.reddit.com/r/x/new.json") is None


def test_calendar_falls_back_to_dom_when_json_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = ShredditBrowserSource(proxy_pool=ProxyPool(["http://127.0.0.1:9"]))
    harvested: list[str] = []

    class _Record:
        pass

    record = _Record()

    monkeypatch.setattr("uyam.sources.shreddit.check_control", lambda: None)
    monkeypatch.setattr("uyam.sources.shreddit.write_status", lambda *a, **k: None)
    monkeypatch.setattr(src, "_goto", lambda *a, **k: None)
    monkeypatch.setattr(src, "_listing_tab_ready", lambda _url: True)
    monkeypatch.setattr(src, "_iter_json_listing_posts", lambda *a, **k: iter(()))
    monkeypatch.setattr(
        src,
        "_collect_dom_listing_posts",
        lambda *a, **k: [
            {
                "id": "abc123",
                "permalink": "/r/Philippines/comments/abc123/x/",
                "created_utc": 1785891600.0,  # 2026-08-05 01:00 UTC
            }
        ],
    )

    def _harvest(permalink: str, _request: CollectionRequest) -> object:
        harvested.append(permalink)
        return record

    monkeypatch.setattr(src, "_harvest_post", _harvest)
    req = CollectionRequest(
        subreddit="Philippines",
        listing_type="new",
        collection_run_id="run-1",
        calendar_since="2026-08-05",
        calendar_until="2026-09-04",
        posts_per_day=1,
    )
    out = list(src._iter_calendar_listing(req))
    assert out == [record]
    assert harvested == ["/r/Philippines/comments/abc123/x/"]
    assert "abc123" not in src._json_post_cache
