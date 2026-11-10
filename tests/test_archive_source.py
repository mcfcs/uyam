"""Tests for the Arctic Shift archive source. All HTTP is faked; no network."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from uyam.calendar_window import day_bounds_unix
from uyam.dedup import DedupDatabase
from uyam.pipeline import run_collection
from uyam.privacy import pseudonymize_author, reset_hmac_key_cache
from uyam.sources.arctic_shift import ArchiveApiError, ArcticShiftSource
from uyam.sources.base import CollectionRequest

TEST_KEY = "test-hmac-key-archive"
SECRET_AUTHOR = "real_user_secret_archive"
JUN1, _ = day_bounds_unix(date(2026, 6, 1))
JUN2, _ = day_bounds_unix(date(2026, 6, 2))
JUN3, _ = day_bounds_unix(date(2026, 6, 3))


@pytest.fixture(autouse=True)
def set_hmac_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AUTHOR_HMAC_KEY", TEST_KEY)
    reset_hmac_key_cache()
    yield
    reset_hmac_key_cache()


# ---------------------------------------------------------------------------
# Fake archive
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(
        self, payload: Any, status: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload


class FakeArchive:
    """Emulates /posts/search and /comments/search: after <= created_utc < before."""

    def __init__(
        self,
        posts: list[dict[str, Any]],
        comments: list[dict[str, Any]],
        *,
        after_exclusive: bool = False,
    ) -> None:
        self.posts = posts
        self.after_exclusive = after_exclusive
        self.comments = comments
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scripted: list[FakeResponse] = []  # served first, one per call
        self.headers: dict[str, str] = {}

    def get(self, url: str, params: dict[str, Any], timeout: float) -> FakeResponse:
        path = url.split("/api", 1)[1]
        self.calls.append((path, dict(params)))
        if self.scripted:
            return self.scripted.pop(0)
        after = int(params.get("after", 0))
        before = int(params.get("before", 1 << 62))
        if path == "/posts/search":
            pool = [
                p for p in self.posts
                if p["subreddit"] == params["subreddit"]
                and (not params.get("query") or params["query"] in p["title"])
            ]
        elif "link_id" in params:  # per-post comments
            pool = [c for c in self.comments if c["link_id"] == f"t3_{params['link_id']}"]
        else:  # subreddit-wide comment stream (bulk mode)
            pool = [c for c in self.comments if c["subreddit"] == params["subreddit"]]
        lo = after + 1 if self.after_exclusive else after
        rows = sorted(
            (r for r in pool if lo <= r["created_utc"] < before),
            key=lambda r: (r["created_utc"], r["id"]),
        )
        return FakeResponse({"data": rows[: int(params["limit"])]})

    def close(self) -> None:
        pass


def _post(rid: str, created: int, sub: str = "CasualPH", **over: Any) -> dict[str, Any]:
    return {
        "id": rid,
        "name": f"t3_{rid}",
        "subreddit": sub,
        "title": f"Title {rid}",
        "selftext": "Ang init ngayon grabe",
        "created_utc": created,
        "score": 3,
        "upvote_ratio": 0.9,
        "num_comments": 2,
        "permalink": f"/r/{sub}/comments/{rid}/x/",
        "url": f"https://www.reddit.com/r/{sub}/comments/{rid}/x/",
        "is_self": True,
        "over_18": False,
        "author": SECRET_AUTHOR,
        "author_fullname": "t2_secret",
        **over,
    }


def _comment(
    rid: str, link: str, parent: str, created: int, *, body: str = "Sana all", **over: Any
) -> dict[str, Any]:
    return {
        "id": rid,
        "name": f"t1_{rid}",
        "subreddit": "CasualPH",
        "link_id": f"t3_{link}",
        "parent_id": parent,
        "body": body,
        "created_utc": created,
        "score": 1,
        "permalink": f"/r/CasualPH/comments/{link}/x/{rid}/",
        "is_submitter": False,
        "author": SECRET_AUTHOR,
        **over,
    }


def _source(
    archive: FakeArchive, sleeps: list[float] | None = None, **kw: Any
) -> ArcticShiftSource:
    kw.setdefault("min_interval_seconds", 0)
    return ArcticShiftSource(
        session=archive,  # type: ignore[arg-type]
        sleep=(sleeps.append if sleeps is not None else (lambda _s: None)),
        **kw,
    )


def _request(**over: Any) -> CollectionRequest:
    base: dict[str, Any] = {
        "subreddit": "CasualPH",
        "listing_type": "archive",
        "collection_run_id": "run-archive-test",
        "calendar_since": "2026-06-01",
        "calendar_until": "2026-06-02",
    }
    return CollectionRequest(**{**base, **over})


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------


def test_walks_each_utc_day_inclusive_and_maps_fields() -> None:
    archive = FakeArchive(
        posts=[
            _post("p0", JUN1 - 1),  # 2026-05-31: outside the window
            _post("p1", JUN1 + 60),
            _post("p2", JUN2 + 60),
            _post("p3", JUN3 + 60),  # 2026-06-03: outside (until is inclusive 06-02)
            _post("other", JUN1 + 90, sub="Philippines"),
        ],
        comments=[],
    )
    subs = list(_source(archive).iter_submissions(_request()))

    assert [s.reddit_id for s in subs] == ["p1", "p2"]
    day_calls = [p for path, p in archive.calls if path == "/posts/search"]
    assert [(c["after"], c["before"]) for c in day_calls] == [(JUN1 - 1, JUN2), (JUN2 - 1, JUN3)]
    assert all(c["subreddit"] == "CasualPH" and c["sort"] == "asc" for c in day_calls)

    first = subs[0]
    expected_hash, _ = pseudonymize_author(SECRET_AUTHOR)
    assert first.reddit_fullname == "t3_p1"
    assert first.author_hash == expected_hash
    assert first.author_status == "pseudonymized"
    assert SECRET_AUTHOR not in first.model_dump_json()
    assert first.sampling_strategy == "natural"


def test_requires_a_date_window() -> None:
    with pytest.raises(ValueError, match="--since"):
        list(_source(FakeArchive([], [])).iter_submissions(_request(calendar_since=None)))


@pytest.mark.parametrize("after_exclusive", [False, True])
def test_post_at_exact_day_start_is_kept_once(after_exclusive: bool) -> None:
    archive = FakeArchive(
        posts=[_post("midnight", JUN2), _post("late", JUN2 - 1)],
        comments=[],
        after_exclusive=after_exclusive,
    )
    subs = [s.reddit_id for s in _source(archive).iter_submissions(_request())]
    assert subs == ["late", "midnight"]  # 06-01 23:59:59, then 06-02 00:00:00


@pytest.mark.parametrize("after_exclusive", [False, True])
def test_paginates_past_full_pages_without_duplicates_or_gaps(after_exclusive: bool) -> None:
    # 250 posts, three per second, so every page boundary splits a second.
    posts = [_post(f"p{i:03d}", JUN1 + 1 + i // 3) for i in range(250)]
    archive = FakeArchive(posts=posts, comments=[], after_exclusive=after_exclusive)
    req = _request(calendar_until="2026-06-01")
    subs = list(_source(archive, page_size=100).iter_submissions(req))

    ids = [s.reddit_id for s in subs]
    assert len(ids) == len(set(ids)) == 250
    assert len([c for c in archive.calls if c[0] == "/posts/search"]) >= 3


def test_per_day_cap_is_a_deterministic_time_ordered_sample() -> None:
    posts = [_post(f"p{i:02d}", JUN1 + i * 60) for i in range(40)]

    def run() -> list[str]:
        archive = FakeArchive(posts=posts, comments=[])
        req = _request(calendar_until="2026-06-01", posts_per_day=5)
        return [s.reddit_id for s in _source(archive).iter_submissions(req)]

    picked = run()
    assert len(picked) == 5
    assert picked == run()  # same sample every run
    assert picked == sorted(picked)  # yielded in created_utc order
    assert picked != [f"p{i:02d}" for i in range(5)]  # not simply the first five


def test_limit_and_search_query_pass_through() -> None:
    posts = [_post("a1", JUN1 + 1, title="sana all may jowa"), _post("a2", JUN1 + 2)]
    archive = FakeArchive(posts=posts, comments=[])
    req = _request(
        search_query="sana all",
        sampling_strategy="keyword_oversampled",
        matched_query_or_keyword="sana all",
        limit=1,
    )
    subs = list(_source(archive).iter_submissions(req))

    assert [s.reddit_id for s in subs] == ["a1"]
    assert archive.calls[0][1]["query"] == "sana all"
    assert subs[0].sampling_strategy == "keyword_oversampled"
    assert subs[0].matched_query_or_keyword == "sana all"


# ---------------------------------------------------------------------------
# Throttling and errors
# ---------------------------------------------------------------------------


def test_throttle_answer_is_retried_after_the_advertised_reset() -> None:
    archive = FakeArchive(posts=[_post("p1", JUN1 + 5)], comments=[])
    archive.scripted = [
        FakeResponse(
            {"data": None, "error": "Timeout. Maybe slow down a bit"},
            headers={"x-ratelimit-reset": "30"},
        ),
        FakeResponse({"data": []}, status=503),
    ]
    sleeps: list[float] = []
    req = _request(calendar_until="2026-06-01")
    subs = list(_source(archive, sleeps).iter_submissions(req))

    assert [s.reddit_id for s in subs] == ["p1"]
    assert sleeps[0] >= 31  # waited out the 30 s reset
    # Two retry waits (the 30 s reset, then the 503 backoff); anything shorter
    # is the adaptive pacing gap that grew after the throttle.
    assert len([s for s in sleeps if s >= 2]) == 2


def test_throttle_body_on_http_422_is_retried_not_fatal() -> None:
    # Observed live: the throttle answer can come back as HTTP 422.
    archive = FakeArchive(posts=[_post("p1", JUN1 + 5)], comments=[])
    archive.scripted = [
        FakeResponse({"data": None, "error": "Timeout. Maybe slow down a bit"}, status=422)
    ]
    req = _request(calendar_until="2026-06-01")
    assert [s.reddit_id for s in _source(archive).iter_submissions(req)] == ["p1"]


def test_bad_request_fails_fast_and_exhausted_retries_raise() -> None:
    archive = FakeArchive([], [])
    archive.scripted = [FakeResponse({"error": "bad param"}, status=400)]
    with pytest.raises(ArchiveApiError, match="HTTP 400"):
        list(_source(archive).iter_submissions(_request()))

    archive = FakeArchive([], [])
    archive.scripted = [FakeResponse({"data": []}, status=503)] * 3
    with pytest.raises(ArchiveApiError, match="after 3 attempts"):
        list(_source(archive, max_retries=3).iter_submissions(_request()))


def test_throttling_has_its_own_budget_and_does_not_end_the_run() -> None:
    """A busy hour throttles for many minutes; that must not kill a backfill."""
    archive = FakeArchive(posts=[_post("p1", JUN1 + 5)], comments=[])
    archive.scripted = [FakeResponse({"data": None, "error": "Timeout. Maybe slow down"})] * 10
    src = _source(archive, max_retries=2, max_throttle_waits=60)
    subs = list(src.iter_submissions(_request(calendar_until="2026-06-01")))
    assert [s.reddit_id for s in subs] == ["p1"]  # 10 throttles > max_retries, still fine

    archive = FakeArchive([], [])
    archive.scripted = [FakeResponse({"data": None}, status=429)] * 4
    with pytest.raises(ArchiveApiError, match="still throttled after 3 waits"):
        list(_source(archive, max_throttle_waits=3).iter_submissions(_request()))


def test_stop_is_honoured_between_throttle_waits() -> None:
    from uyam.scrape_status import ScrapeStopRequested, set_control

    archive = FakeArchive([], [])
    archive.scripted = [FakeResponse({"data": None, "error": "Timeout"})] * 5

    def _sleep(_s: float) -> None:
        set_control("stop")  # operator hits Stop while the source waits

    src = ArcticShiftSource(session=archive, min_interval_seconds=0, sleep=_sleep)  # type: ignore[arg-type]
    with pytest.raises(ScrapeStopRequested):
        list(src.iter_submissions(_request()))
    assert len(archive.calls) == 1  # no further request after Stop


def test_limit_auto_is_sent_and_short_pages_end_the_walk() -> None:
    posts = [_post(f"p{i:03d}", JUN1 + 1 + i) for i in range(150)]
    archive = FakeArchive(posts=posts, comments=[])
    src = _source(archive, page_size="auto")
    original = archive.get  # the fake needs a number: serve "auto" as 1000 rows

    def get(url: str, params: dict[str, Any], timeout: float) -> FakeResponse:
        assert params["limit"] == "auto"
        return original(url, {**params, "limit": 1000}, timeout)

    archive.get = get  # type: ignore[method-assign]
    subs = list(src.iter_submissions(_request(calendar_until="2026-06-01")))
    assert len(subs) == 150
    post_calls = [c for c in archive.calls if c[0] == "/posts/search"]
    # 150 rows in the first call (not short, so ask again); the second returns
    # fewer than 100 (only the overlap second), which ends the walk.
    assert len(post_calls) == 2


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


def _thread() -> FakeArchive:
    return FakeArchive(
        posts=[_post("p1", JUN1 + 10)],
        comments=[
            _comment("c1", "p1", "t3_p1", JUN1 + 20),
            _comment("c2", "p1", "t1_c1", JUN1 + 30, depth=None),  # depth missing
            _comment("c3", "p1", "t1_c2", JUN1 + 40),
            _comment("gone", "p1", "t3_p1", JUN1 + 50, body="[removed]"),
            _comment("x1", "other", "t3_other", JUN1 + 60),  # different thread
        ],
    )


@pytest.mark.parametrize("bulk", [True, False])
def test_comments_keep_tree_and_inherit_sampling(bulk: bool) -> None:
    archive = _thread()
    src = _source(archive, bulk_comments=bulk)
    req = _request(
        calendar_until="2026-06-01",
        sampling_strategy="keyword_oversampled",
        matched_query_or_keyword="sana all",
    )
    subs = iter(src.iter_submissions(req))  # keep it alive, as the pipeline does
    sub = next(subs)
    comments = list(src.iter_comments(sub, req))

    comment_calls = [p for path, p in archive.calls if path == "/comments/search"]
    if bulk:
        # One subreddit-wide stream: through the day, then on to the reply horizon.
        assert all(c["subreddit"] == "CasualPH" and "link_id" not in c for c in comment_calls)
        assert comment_calls[0]["after"] == JUN1 - 1
        assert comment_calls[0]["before"] == JUN2
        assert comment_calls[-1]["before"] == JUN2 + 3 * 86400
    else:
        assert comment_calls[0]["after"] == JUN1 + 10 - 1  # from the post's own time, never 0
        assert comment_calls[0]["link_id"] == "p1"

    by_id = {c.reddit_id: c for c in comments}
    assert list(by_id) == ["c1", "c2", "c3"]  # placeholder body + other thread dropped
    assert by_id["c1"].depth == 0 and by_id["c1"].parent_record_type == "submission"
    assert by_id["c2"].depth == 1  # computed from the parent chain
    assert by_id["c3"].parent_id == "t1_c2"
    assert all(c.submission_id == "p1" for c in comments)
    assert all(c.sampling_strategy == "keyword_oversampled" for c in comments)
    assert all(SECRET_AUTHOR not in c.model_dump_json() for c in comments)


def test_comment_cap_keeps_earliest_so_no_reply_is_orphaned() -> None:
    archive = _thread()
    src = _source(archive)
    req = _request(calendar_until="2026-06-01", max_comments_per_submission=2)
    subs = iter(src.iter_submissions(req))  # keep it alive, as the pipeline does
    sub = next(subs)
    assert [c.reddit_id for c in src.iter_comments(sub, req)] == ["c1", "c2"]


# ---------------------------------------------------------------------------
# End to end through the collection pipeline
# ---------------------------------------------------------------------------


def test_run_collection_writes_hashed_jsonl_and_dedups(tmp_path: Path) -> None:
    db = DedupDatabase(tmp_path / "db" / "collection.sqlite3")
    try:
        ctx = run_collection(
            _source(_thread()),
            _request(calendar_until="2026-06-01"),
            data_dir=tmp_path,
            db=db,
            source_type="archive",
        )
        assert ctx.actual_submissions_stored == 1
        assert ctx.comments_stored == 3

        raw = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "raw").rglob("*.jsonl"))
        assert SECRET_AUTHOR not in raw
        assert "t2_secret" not in raw
        assert all("author" not in json.loads(line) for line in raw.splitlines())

        manifest = json.loads(next((tmp_path / "manifests").glob("*.json")).read_text("utf-8"))
        assert manifest["source_type"] == "archive"

        again = run_collection(
            _source(_thread()),
            _request(calendar_until="2026-06-01", collection_run_id="run-2"),
            data_dir=tmp_path,
            db=db,
            source_type="archive",
        )
        assert again.actual_submissions_stored == 0
        assert again.duplicates_skipped == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Bulk comments (one subreddit-wide stream) and adaptive pacing
# ---------------------------------------------------------------------------

DAY = 86400


def _bulk_archive() -> FakeArchive:
    """Three days of posts, comments spread across the reply horizon."""
    posts = [_post(f"d1p{i}", JUN1 + 100 + i) for i in range(30)]
    posts += [_post(f"d2p{i}", JUN2 + 100 + i) for i in range(30)]
    posts += [_post(f"d3p{i}", JUN3 + 100 + i) for i in range(30)]
    comments = [
        _comment(f"c{p['id']}", str(p["id"]), f"t3_{p['id']}", int(p["created_utc"]) + 60)
        for p in posts
    ]
    comments += [
        _comment("late_ok", "d1p0", "t3_d1p0", JUN1 + 2 * DAY + 5),  # day 3: inside 3-day horizon
        # The last day (Jun 3) streams to Jun 7, so day 1 gets at least its
        # 3 days; a reply on Jun 9 is past every horizon in this window.
        _comment("too_late", "d1p0", "t3_d1p0", JUN1 + 8 * DAY),
        _comment("before_window", "may31", "t3_may31", JUN1 + 30),  # post not in the window
    ]
    return FakeArchive(posts=posts, comments=comments)


def _collect(src: ArcticShiftSource, req: CollectionRequest) -> dict[str, list[str]]:
    """What the pipeline would store: each post, then its comments."""
    out: dict[str, list[str]] = {}
    for sub in src.iter_submissions(req):
        out[sub.reddit_id] = [c.reddit_id for c in src.iter_comments(sub, req)]
    return out


def test_bulk_comments_need_far_fewer_requests_than_one_per_post() -> None:
    req = _request(calendar_until="2026-06-03")
    bulk_archive, per_post_archive = _bulk_archive(), _bulk_archive()
    bulk = _collect(_source(bulk_archive), req)
    per_post = _collect(_source(per_post_archive, bulk_comments=False), req)

    def comment_requests(archive: FakeArchive) -> int:
        return len([c for c in archive.calls if c[0] == "/comments/search"])

    assert len(bulk) == len(per_post) == 90
    assert comment_requests(per_post_archive) == 90  # one per post
    assert comment_requests(bulk_archive) <= 6  # ~100 comments per request
    # Same comments per post either way, within the horizon.
    for post_id, ids in bulk.items():
        assert ids == [i for i in per_post[post_id] if i != "too_late"]


def test_bulk_reply_horizon_and_window_edges() -> None:
    got = _collect(_source(_bulk_archive()), _request(calendar_until="2026-06-03"))
    assert got["d1p0"] == ["cd1p0", "late_ok"]  # a day-3 reply to a day-1 post is kept
    assert "too_late" not in got["d1p0"]  # beyond the 3-day horizon
    assert all("before_window" not in ids for ids in got.values())
    assert list(got)[:2] == ["d1p0", "d1p1"]  # posts still come out in time order


def test_bulk_never_streams_past_now() -> None:
    archive = _bulk_archive()
    src = _source(archive, now=lambda: JUN3 + 3600)  # "today" is 2026-06-03 01:00
    _collect(src, _request(calendar_until="2026-06-03"))
    befores = [c[1]["before"] for c in archive.calls if c[0] == "/comments/search"]
    assert max(befores) == JUN3 + 3600


def test_skipped_posts_leave_no_comment_buckets_behind() -> None:
    src = _source(_bulk_archive())
    # The pipeline skips duplicates without ever calling iter_comments.
    assert len(list(src.iter_submissions(_request(calendar_until="2026-06-03")))) == 90
    assert src._buckets == {}


def test_keyword_search_fetches_comments_per_post() -> None:
    archive = _thread()
    src = _source(archive)
    req = _request(calendar_until="2026-06-01", search_query="Title")
    subs = iter(src.iter_submissions(req))  # keep it alive, as the pipeline does
    sub = next(subs)
    list(src.iter_comments(sub, req))
    comment_calls = [p for path, p in archive.calls if path == "/comments/search"]
    assert comment_calls and all(c.get("link_id") == "p1" for c in comment_calls)


def test_pacing_widens_after_a_throttle_and_eases_back() -> None:
    archive = FakeArchive(posts=[_post("p1", JUN1 + 5)], comments=[])
    archive.scripted = [FakeResponse({"data": None, "error": "Timeout. Maybe slow down"})]
    src = _source(archive, min_interval_seconds=1.0)
    list(src.iter_submissions(_request(calendar_until="2026-06-01")))
    after_throttle_and_one_success = src._interval
    assert after_throttle_and_one_success > 1.0  # 1.0 * 1.5 + 0.5, then eased once
    for _ in range(40):
        src._get("/posts/search", {"subreddit": "CasualPH", "after": JUN1, "limit": 100})
    assert src._interval == pytest.approx(1.0)  # back at the configured base
