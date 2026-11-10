"""Unit tests for ShredditBrowserSource construction (no live browser)."""

from __future__ import annotations

from datetime import date

import pytest

from uyam.calendar_window import day_bounds_unix
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
    absorb_listing_cards,
    feed_advanced,
    listing_card_to_raw,
    listing_reached_since,
    page_text_is_captcha,
    page_text_is_whoa,
    rate_limit_toast_seconds,
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


def _card(reddit_id: str, created: str, *, promoted: bool = False) -> dict[str, object]:
    return {
        "id": f"t3_{reddit_id}",
        "permalink": f"/r/Philippines/comments/{reddit_id}/t/",
        "created": created,
        "promoted": promoted,
    }


def test_feed_advanced_accepts_new_ids_when_the_count_shrinks() -> None:
    before = {"t3_a", "t3_b", "t3_c"}
    recycled = [_card("c", "2026-11-10T00:00:00+0000"), _card("d", "2026-11-09T00:00:00+0000")]
    assert feed_advanced(before, 3, recycled) is True
    same = [_card("a", "2026-11-10T00:00:00+0000")]
    assert feed_advanced({"t3_a"}, 2, same) is False
    assert feed_advanced(set(), 0, same) is True


def test_listing_reached_since_uses_the_oldest_card() -> None:
    start, _ = day_bounds_unix(date(2026, 11, 8))
    newer = listing_card_to_raw(_card("a", "2026-11-10T00:00:00.000000+0000"))
    older = listing_card_to_raw(_card("b", "2026-11-07T00:00:00.000000+0000"))
    assert newer is not None and older is not None
    assert listing_reached_since([newer], float(start)) is False
    assert listing_reached_since([newer, older], float(start)) is True
    assert listing_reached_since([older], None) is False


def test_absorb_listing_cards_skips_promoted_and_duplicates() -> None:
    seen: set[str] = set()
    ordered: list[dict[str, object]] = []
    cards = [
        _card("a", "2026-11-10T00:00:00+0000"),
        _card("ad", "2026-11-10T00:00:00+0000", promoted=True),
        _card("a", "2026-11-10T00:00:00+0000"),
        _card("b", "2026-11-09T00:00:00+0000"),
    ]
    added = absorb_listing_cards(cards, seen=seen, ordered=ordered)  # type: ignore[arg-type]
    assert added == 2
    assert [row["id"] for row in ordered] == ["a", "b"]


def test_read_listing_cards_is_one_evaluate_all(monkeypatch: pytest.MonkeyPatch) -> None:
    src = ShredditBrowserSource(proxy_pool=ProxyPool(["http://127.0.0.1:9"]))

    class _Locator:
        def evaluate_all(self, script: str) -> list[dict[str, object]]:
            assert script.lstrip().startswith("(els)")
            return [_card("abc", "2026-11-10T00:00:00+0000")]

        def nth(self, _index: int) -> None:
            raise AssertionError("per-card nth() hangs when a card detaches")

    class _Page:
        def locator(self, selector: str) -> _Locator:
            assert selector == "shreddit-post"
            return _Locator()

    monkeypatch.setattr(src, "_require_page", lambda: _Page())
    cards = src._read_listing_cards()
    assert cards[0]["id"] == "t3_abc"

    class _Detached:
        def evaluate_all(self, _script: str) -> list[dict[str, object]]:
            raise TimeoutError("element detached")

    class _DeadPage:
        def locator(self, _selector: str) -> _Detached:
            return _Detached()

    monkeypatch.setattr(src, "_require_page", lambda: _DeadPage())
    assert src._read_listing_cards() == []


def test_dom_listing_scrolls_until_since(monkeypatch: pytest.MonkeyPatch) -> None:
    src = ShredditBrowserSource(proxy_pool=ProxyPool(["http://127.0.0.1:9"]))
    batches = [
        [
            _card("aaa", "2026-11-10T12:00:00.000000+0000"),
            _card("bbb", "2026-11-09T12:00:00.000000+0000"),
        ],
        [
            _card("bbb", "2026-11-09T12:00:00.000000+0000"),
            _card("ccc", "2026-11-07T12:00:00.000000+0000"),
        ],
    ]
    state = {"i": 0, "scrolls": 0, "first": []}

    def _await(
        before_ids: set[str], before_count: int, *, first_page: bool
    ) -> list[dict[str, object]]:
        del before_ids, before_count
        state["first"].append(first_page)
        batch = batches[min(state["i"], len(batches) - 1)]
        state["i"] += 1
        return batch

    monkeypatch.setattr("uyam.sources.shreddit.check_control", lambda: None)
    def _scroll(*, to_bottom: bool = False) -> bool:
        state["scrolls"] += 1
        return False

    monkeypatch.setattr(src, "_await_listing_cards", _await)
    monkeypatch.setattr(src, "_scroll_feed", _scroll)
    start, _ = day_bounds_unix(date(2026, 11, 8))
    req = CollectionRequest(
        subreddit="Philippines", listing_type="new", collection_run_id="run-1"
    )
    posts = src._collect_dom_listing_posts(
        req, max_posts=50, stop_before_unix=float(start)
    )
    assert [post["id"] for post in posts] == ["aaa", "bbb", "ccc"]
    assert state["scrolls"] == 1
    assert state["first"] == [True, False]


def test_dom_listing_idles_when_the_feed_does_not_grow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = ShredditBrowserSource(proxy_pool=ProxyPool(["http://127.0.0.1:9"]))
    state = {"scrolls": 0}

    monkeypatch.setattr("uyam.sources.shreddit.check_control", lambda: None)
    def _scroll(*, to_bottom: bool = False) -> bool:
        state["scrolls"] += 1
        return True  # already at the page bottom: a static feed is genuinely idle

    monkeypatch.setattr(
        src,
        "_await_listing_cards",
        lambda *_a, **_k: [_card("aaa", "2026-11-10T00:00:00+0000")],
    )
    monkeypatch.setattr(src, "_scroll_feed", _scroll)
    req = CollectionRequest(
        subreddit="CasualPH", listing_type="new", collection_run_id="run-1"
    )
    posts = src._collect_dom_listing_posts(req, max_posts=50, max_scrolls=10)
    assert [post["id"] for post in posts] == ["aaa"]
    assert state["scrolls"] == 3


def test_calendar_passes_since_to_the_dom_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    src = ShredditBrowserSource(proxy_pool=ProxyPool(["http://127.0.0.1:9"]))
    captured: dict[str, object] = {}

    def _collect(
        _request: CollectionRequest,
        *,
        max_posts: int,
        stop_before_unix: float | None = None,
        max_scrolls: int | None = None,
    ) -> list[dict[str, object]]:
        del max_scrolls
        captured["max_posts"] = max_posts
        captured["stop"] = stop_before_unix
        return []

    monkeypatch.setattr("uyam.sources.shreddit.check_control", lambda: None)
    monkeypatch.setattr("uyam.sources.shreddit.write_status", lambda *a, **k: None)
    monkeypatch.setattr(src, "_goto", lambda *a, **k: None)
    monkeypatch.setattr(src, "_listing_tab_ready", lambda _url: True)
    monkeypatch.setattr(src, "_iter_json_listing_posts", lambda *a, **k: iter(()))
    monkeypatch.setattr(src, "_collect_dom_listing_posts", _collect)
    req = CollectionRequest(
        subreddit="Philippines",
        listing_type="new",
        collection_run_id="run-1",
        calendar_since="2026-11-08",
        calendar_until="2026-11-10",
        posts_per_day=40,
    )
    assert list(src._iter_calendar_listing(req)) == []
    start, _ = day_bounds_unix(date(2026, 11, 8))
    assert captured["stop"] == float(start)
    assert captured["max_posts"] == 300


def test_keyword_job_does_not_walk_the_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    src = ShredditBrowserSource(proxy_pool=ProxyPool(["http://127.0.0.1:9"]))

    def _calendar(_request: CollectionRequest) -> None:
        raise AssertionError("keyword job must not walk /new")

    monkeypatch.setattr(src, "_iter_calendar_listing", _calendar)
    monkeypatch.setattr(src, "_iter_listing", lambda request: iter([request.search_query]))
    req = CollectionRequest(
        subreddit="Philippines",
        listing_type="search",
        collection_run_id="run-1",
        search_query="edi wow",
        sampling_strategy="keyword_oversampled",
        matched_query_or_keyword="edi wow",
        calendar_since="2026-11-08",
        calendar_until="2026-11-10",
        posts_per_day=40,
    )
    assert list(src.iter_submissions(req)) == ["edi wow"]


def test_dom_listing_jumps_to_the_bottom_instead_of_idling_mid_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first page is several screens tall; viewport steps over it are not idle.

    Seen 2026-11-11: three viewport steps never reached the feed loader, so
    every walk stopped at the first 27 cards with reason 'idle'.
    """
    src = ShredditBrowserSource(proxy_pool=ProxyPool(["http://127.0.0.1:9"]))
    first = [_card(f"p{i:02d}", "2026-11-10T12:00:00+0000") for i in range(27)]
    second = first[2:] + [_card(f"q{i:02d}", "2026-11-09T12:00:00+0000") for i in range(25)]
    state: dict[str, object] = {"page": first, "calls": []}

    def _scroll(*, to_bottom: bool = False) -> bool:
        calls = state["calls"]
        assert isinstance(calls, list)
        calls.append(to_bottom)
        if to_bottom:
            state["page"] = second  # the loader only fires at the bottom
            return True
        return False  # a viewport step over cards already read

    monkeypatch.setattr("uyam.sources.shreddit.check_control", lambda: None)
    monkeypatch.setattr(src, "_await_listing_cards", lambda *_a, **_k: state["page"])
    monkeypatch.setattr(src, "_scroll_feed", _scroll)
    req = CollectionRequest(
        subreddit="CasualPH", listing_type="new", collection_run_id="run-1"
    )
    posts = src._collect_dom_listing_posts(req, max_posts=200, max_scrolls=12)

    assert len(posts) == 52  # both pages, no duplicates
    calls = state["calls"]
    assert isinstance(calls, list)
    assert calls[:2] == [False, True]  # a step that finds nothing becomes a jump


def test_rate_limit_toast_seconds() -> None:
    toast = "Your request has been rate limited, please take a break for 96 seconds and try again."
    assert rate_limit_toast_seconds(toast) == 96.0
    assert rate_limit_toast_seconds("You've been rate limited") == 60.0
    assert rate_limit_toast_seconds("Load more comments") is None


class _ToastLocator:
    def __init__(self, text: str | None) -> None:
        self._text = text

    def count(self) -> int:
        return 0 if self._text is None else 1

    @property
    def first(self) -> _ToastLocator:
        return self

    def inner_text(self, timeout: float = 0) -> str:
        return self._text or ""


class _ToastPage:
    def __init__(self, text: str | None) -> None:
        self.text = text

    def locator(self, _selector: str) -> _ToastLocator:
        return _ToastLocator(self.text)


def test_rate_limit_toast_is_waited_out_and_stop_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = ShredditBrowserSource(proxy_pool=ProxyPool(["http://127.0.0.1:9"]))
    clock = {"t": 1000.0, "slept": 0.0, "checks": 0}

    def _sleep(seconds: float) -> None:
        clock["t"] += seconds
        clock["slept"] += seconds

    def _check() -> None:
        clock["checks"] += 1

    monkeypatch.setattr("uyam.sources.shreddit.time.monotonic", lambda: clock["t"])
    monkeypatch.setattr("uyam.sources.shreddit.time.sleep", _sleep)
    monkeypatch.setattr("uyam.sources.shreddit.check_control", _check)
    monkeypatch.setattr("uyam.sources.shreddit.write_status", lambda *_a, **_k: None)

    monkeypatch.setattr(src, "_require_page", lambda: _ToastPage(None))
    assert src._wait_out_rate_limit_toast() is False
    assert clock["slept"] == 0

    toast = "Your request has been rate limited, please take a break for 96 seconds."
    monkeypatch.setattr(src, "_require_page", lambda: _ToastPage(toast))
    assert src._wait_out_rate_limit_toast() is True
    assert clock["slept"] == pytest.approx(99.0)  # the stated 96 s plus a margin
    assert clock["checks"] >= 19  # polled Stop at least every 5 s
