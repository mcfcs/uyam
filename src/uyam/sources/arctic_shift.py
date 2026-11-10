"""Historical Reddit source backed by the Arctic Shift archive API.

Reddit's own listings cannot seek to a date (/new reaches only days back), so
date-bounded backfill — the thesis needs months of history for the temporal
context channel — comes from the Arctic Shift archive
(https://arctic-shift.photon-reddit.com), which serves Reddit-shaped JSON
by subreddit and created_utc range.

The source walks the request's calendar window one UTC day at a time:

  /api/posts/search?subreddit=S&after=<day start>&before=<day end>&sort=asc
  /api/comments/search?link_id=<post id>&sort=asc

paginating on created_utc. With `posts_per_day` set, each day keeps a
deterministic hash-ranked sample instead of the first N posts, so the cap
does not bias toward early-morning (UTC) threads.

Comments come in bulk: one subreddit-wide `/api/comments/search?subreddit=S`
stream in time order (100-1000 rows per request with limit=auto, whatever
the thread), bucketed by post.
Per-post requests (one per submission, most returning a handful of comments)
made a 2026-11-11 run crawl at ~1-4 posts/min. A day's posts are released
once comments are streamed `comment_horizon_days` past that day (at least
that; the last days of a window share the final stream), so a reply later
than that may not be collected (state this as a limitation). Keyword
searches still fetch comments per post: their posts are scattered in time.

Records are mapped through sources/mapping.py, the one place a raw username is
read (to derive author_hash with the same AUTHOR_HMAC_KEY as every other
source). Archive scores and comment counts are snapshots taken when the
archive ingested the item, not current values.

The API throttles bursts by answering {"data": null, "error": "Timeout. Maybe
slow down a bit"} with an `x-ratelimit-reset` header (seconds). Such answers
are retried after the advertised reset, and pacing adapts: each throttle
widens the gap between requests, each success narrows it back to the base.
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

import requests

from uyam.calendar_window import day_bounds_unix, iter_utc_days, parse_iso_date
from uyam.models import CommentRecord, SubmissionRecord
from uyam.scrape_status import check_control
from uyam.sources.base import CollectionRequest
from uyam.sources.mapping import map_comment_dict, map_submission_dict

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://arctic-shift.photon-reddit.com/api"
_PLACEHOLDER_TEXT = {"[deleted]", "[removed]", "[deleted by user]"}
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_INTERVAL_S = 15.0  # adaptive pacing ceiling
# The archive reports throttling and server-side query timeouts in the JSON
# error body, on HTTP 200 *or* 4xx (seen: 422) — never fail fast on these.
_THROTTLE_MARKERS = ("timeout", "slow down", "rate limit")


class ArchiveApiError(RuntimeError):
    """Non-retryable archive failure (bad request) or retries exhausted."""


class ArcticShiftSource:
    """RedditSource over the Arctic Shift archive. Requires a calendar window."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        session: requests.Session | None = None,
        min_interval_seconds: float = 1.0,
        timeout_seconds: float = 60.0,
        max_retries: int = 8,
        max_throttle_waits: int = 60,
        page_size: int | str = 100,
        bulk_comments: bool = True,
        comment_horizon_days: float = 3.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._session.headers.setdefault(
            "User-Agent", "uyam-collector (thesis research; Taglish sarcasm dataset)"
        )
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._interval = self._min_interval  # adaptive; see _get
        self._timeout = float(timeout_seconds)
        self._max_retries = max(1, int(max_retries))
        self._max_throttle_waits = max(1, int(max_throttle_waits))
        # limit=auto returns 100-1000 rows per call, depending on server
        # capacity, so a page shorter than 100 is the last one.
        if str(page_size).lower() == "auto":
            self._limit_param: int | str = "auto"
            self._full_page = 100
        else:
            self._full_page = max(1, min(100, int(page_size)))
            self._limit_param = self._full_page
        self._sleep = sleep
        self._clock = clock
        self._now = now
        self._last_request: float | None = None
        self._bulk_comments = bool(bulk_comments)
        self._horizon_s = max(0, int(float(comment_horizon_days) * 86400))
        # Bulk-comment state: post id -> its comments (created_utc order), and
        # how far the subreddit-wide comment stream has been read.
        self._buckets: dict[str, list[dict[str, Any]]] = {}
        self._comments_until: int | None = None

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _pace(self) -> None:
        if self._last_request is not None:
            wait = self._interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    @staticmethod
    def _reset_seconds(resp: requests.Response) -> float | None:
        raw = resp.headers.get("x-ratelimit-reset")
        try:
            return float(raw) if raw is not None else None
        except ValueError:
            return None

    def _get(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """One API call. Throttling is waited out; real failures are retried a few times.

        The archive throttles dynamically by server load and query cost
        (its docs), and at busy hours most requests come back throttled for
        many minutes. That is not a failure: it gets its own, much larger
        budget (`max_throttle_waits`), so a slow hour cannot end a multi-day
        backfill the way 8 retries did on 2026-11-11.
        """
        url = f"{self._base_url}{path}"
        failures = 0
        throttles = 0
        while True:
            check_control()  # Stop works between waits, not only between records
            self._pace()
            reason: str
            reset: float | None = None
            throttled = False
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                reason = type(exc).__name__
            else:
                reset = self._reset_seconds(resp)
                try:
                    payload = resp.json()
                except ValueError:
                    payload = None
                error = str(payload.get("error") or "") if isinstance(payload, dict) else ""
                throttled = resp.status_code == 429 or any(
                    m in error.lower() for m in _THROTTLE_MARKERS
                )
                if throttled or resp.status_code in _RETRY_STATUS:
                    reason = error[:80] or f"http_{resp.status_code}"
                elif resp.status_code >= 400:
                    raise ArchiveApiError(
                        f"archive {path} returned HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
                    # Success eases the pace back toward the configured base.
                    self._interval = max(self._min_interval, self._interval * 0.9)
                    return list(payload["data"])
                else:
                    reason = error[:80] or "invalid_json"

            if throttled:
                throttles += 1
                if throttles > self._max_throttle_waits:
                    raise ArchiveApiError(
                        f"archive {path} still throttled after {throttles - 1} waits"
                    )
                delay = max(5.0, (reset if reset is not None else 30.0) + 1.0)
                delay += random.uniform(0, 2)
                # Fixed 1 s pacing re-tripped the limiter every minute; back off.
                self._interval = min(_MAX_INTERVAL_S, self._interval * 1.5 + 0.5)
            else:
                failures += 1
                if failures >= self._max_retries:
                    raise ArchiveApiError(
                        f"archive {path} failed after {self._max_retries} attempts ({reason})"
                    )
                delay = max(
                    min(120.0, (2**failures) + random.uniform(0, 1)), (reset or 0.0) + 1.0
                )
            logger.info(
                "archive_retry",
                extra={
                    "path": path,
                    "throttled": throttled,
                    "attempt": throttles if throttled else failures,
                    "reason": reason,
                    "delay_seconds": round(delay, 1),
                    "interval_seconds": round(self._interval, 2),
                },
            )
            self._sleep(delay)

    def _paginate(
        self, path: str, params: dict[str, Any], *, after: int, before: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield every item in created_utc order, each id once.

        The cursor re-requests from one second before the last page's final
        item, so items sharing that timestamp are never skipped whether the
        API treats `after` as inclusive or exclusive; ids already yielded are
        dropped. A full page with nothing new advances past it.
        """
        cursor = after
        seen: set[str] = set()
        while before is None or cursor < before:
            query = {**params, "after": cursor, "limit": self._limit_param, "sort": "asc"}
            if before is not None:
                query["before"] = before
            page = self._get(path, query)
            fresh = [item for item in page if str(item.get("id")) not in seen]
            for item in fresh:
                seen.add(str(item.get("id")))
                yield item
            if len(page) < self._full_page:
                return
            last = int(page[-1]["created_utc"])
            cursor = max(cursor, last - 1) if fresh else last + 1

    # ------------------------------------------------------------------
    # RedditSource
    # ------------------------------------------------------------------

    def iter_submissions(self, request: CollectionRequest) -> Iterable[SubmissionRecord]:
        if not request.calendar_since:
            raise ValueError("the archive source needs a date window (--since YYYY-MM-DD)")
        since = parse_iso_date(str(request.calendar_since))
        until = parse_iso_date(
            str(request.calendar_until or datetime.now(UTC).date().isoformat())
        )
        per_day = request.posts_per_day if (request.posts_per_day or 0) > 0 else None
        params: dict[str, Any] = {"subreddit": request.subreddit}
        if request.search_query:
            params["query"] = request.search_query
        bulk = self._bulk_comments and not request.search_query
        window_start, _ = day_bounds_unix(since)
        self._buckets = {}
        self._comments_until = None

        # Days fetched but not yet handed on, as (day_end_unix, posts). In bulk
        # mode a day waits until its comments are streamed `horizon` past it.
        pending: deque[tuple[int, list[dict[str, Any]]]] = deque()
        yielded = 0

        for day in iter_utc_days(since, until):
            start, end = day_bounds_unix(day)
            # Start one second early so a post at exactly 00:00:00 survives an
            # exclusive `after`; keep only this day's posts.
            posts = [
                p
                for p in self._paginate("/posts/search", params, after=start - 1, before=end)
                if start <= int(p["created_utc"]) < end
            ]
            if per_day is not None and len(posts) > per_day:
                sample = sorted(posts, key=_rank)[:per_day]
                posts = sorted(sample, key=lambda p: int(p["created_utc"]))
            logger.info(
                "archive_day",
                extra={"subreddit": request.subreddit, "day": day.isoformat(), "posts": len(posts)},
            )
            pending.append((end, posts))
            if bulk:
                for raw in posts:
                    self._buckets.setdefault(str(raw["id"]), [])
                # Never ask for comments from the future (--until can be today).
                upto = min(end, int(self._now()))
                self._stream_comments(request.subreddit, upto=upto, window_start=window_start)
            while pending and (not bulk or pending[0][0] + self._horizon_s <= end):
                for record in self._release(pending.popleft()[1], request):
                    yield record
                    yielded += 1
                    if request.limit and yielded >= request.limit:
                        return

        if bulk and pending:
            # Finish the reply window for the last days, but never past "now".
            upto = min(pending[-1][0] + self._horizon_s, int(self._now()))
            self._stream_comments(request.subreddit, upto=upto, window_start=window_start)
        while pending:
            for record in self._release(pending.popleft()[1], request):
                yield record
                yielded += 1
                if request.limit and yielded >= request.limit:
                    return

    def _release(
        self, posts: list[dict[str, Any]], request: CollectionRequest
    ) -> Iterator[SubmissionRecord]:
        """Hand one day's posts to the pipeline, then drop their unclaimed buckets."""
        try:
            for raw in posts:
                yield map_submission_dict(
                    _submission_defaults(raw, request.subreddit),
                    collection_run_id=request.collection_run_id,
                    sampling_strategy=request.sampling_strategy,
                    matched_query_or_keyword=request.matched_query_or_keyword,
                )
        finally:
            # Posts the pipeline skipped as duplicates never ask for comments.
            for raw in posts:
                self._buckets.pop(str(raw["id"]), None)

    def _stream_comments(self, subreddit: str, *, upto: int, window_start: int) -> None:
        """Read the subreddit's comments in time order up to `upto` (exclusive).

        Only comments on posts awaiting release are kept. A comment always
        comes after its post, and a day's posts are registered before the
        stream passes that day, so no wanted comment goes by unseen.
        """
        start = self._comments_until if self._comments_until is not None else window_start
        if upto <= start:
            return
        kept = 0
        for raw in self._paginate(
            "/comments/search", {"subreddit": subreddit}, after=start - 1, before=upto
        ):
            created = int(raw.get("created_utc") or 0)
            if not start <= created < upto:
                continue  # the one-second overlap with the previous stream
            link = str(raw.get("link_id") or "")
            bucket = self._buckets.get(link[3:] if link.startswith("t3_") else link)
            if bucket is not None:
                bucket.append(raw)
                kept += 1
        self._comments_until = upto
        logger.info(
            "archive_comments_streamed",
            extra={
                "subreddit": subreddit,
                "upto": datetime.fromtimestamp(upto, tz=UTC).isoformat(),
                "kept": kept,
            },
        )

    def iter_comments(
        self, submission: SubmissionRecord, request: CollectionRequest
    ) -> Iterable[CommentRecord]:
        bucket = self._buckets.pop(submission.reddit_id, None)
        if bucket is not None:
            raw_comments = bucket
        else:
            # Keyword-search posts (or bulk mode off): one request per post.
            # A reply cannot predate its post (and the API rejects after=0).
            post_created = int(submission.created_utc.timestamp())
            raw_comments = list(
                self._paginate(
                    "/comments/search", {"link_id": submission.reddit_id}, after=post_created - 1
                )
            )
        depths = _depths(raw_comments)
        kept = 0
        cap = request.max_comments_per_submission
        for raw in raw_comments:
            body = str(raw.get("body") or "").strip()
            if not request.include_deleted and body in _PLACEHOLDER_TEXT:
                continue
            depth = int(raw["depth"]) if raw.get("depth") is not None else depths[str(raw["id"])]
            if request.max_depth is not None and depth > request.max_depth:
                continue
            yield map_comment_dict(
                _comment_defaults(raw, submission, depth),
                collection_run_id=request.collection_run_id,
                sampling_strategy=submission.sampling_strategy,
                matched_query_or_keyword=submission.matched_query_or_keyword,
            )
            kept += 1
            # created_utc order keeps every parent ahead of its replies, so
            # truncating here never orphans a kept comment.
            if cap is not None and kept >= cap:
                return


def _rank(raw: dict[str, Any]) -> str:
    return hashlib.sha256(str(raw["id"]).encode("ascii")).hexdigest()


def _depths(raw_comments: list[dict[str, Any]]) -> dict[str, int]:
    """Depth from the parent chain, for archive rows that lack `depth`."""
    parent = {str(c["id"]): str(c.get("parent_id") or "") for c in raw_comments}
    out: dict[str, int] = {}
    for cid in parent:
        depth, cur, hops = 0, parent[cid], 0
        while cur.startswith("t1_") and hops < 500:
            depth += 1
            cur = parent.get(cur[3:], "")
            hops += 1
        out[cid] = depth
    return out


def _submission_defaults(raw: dict[str, Any], subreddit: str) -> dict[str, Any]:
    rid = str(raw["id"])
    permalink = raw.get("permalink") or f"/r/{subreddit}/comments/{rid}/"
    return {
        **raw,
        "name": raw.get("name") or f"t3_{rid}",
        "subreddit": raw.get("subreddit") or subreddit,
        "selftext": raw.get("selftext") or "",
        "score": raw.get("score") or 0,
        "upvote_ratio": raw.get("upvote_ratio") if raw.get("upvote_ratio") is not None else 1.0,
        "num_comments": raw.get("num_comments") or 0,
        "permalink": permalink,
        "url": raw.get("url") or f"https://www.reddit.com{permalink}",
        "is_self": bool(raw.get("is_self", False)),
        "over_18": bool(raw.get("over_18", False)),
    }


def _comment_defaults(
    raw: dict[str, Any], submission: SubmissionRecord, depth: int
) -> dict[str, Any]:
    rid = str(raw["id"])
    return {
        **raw,
        "name": raw.get("name") or f"t1_{rid}",
        "subreddit": raw.get("subreddit") or submission.subreddit,
        "link_id": raw.get("link_id") or submission.reddit_fullname,
        "parent_id": raw.get("parent_id") or submission.reddit_fullname,
        "score": raw.get("score") or 0,
        "depth": depth,
        "permalink": raw.get("permalink") or f"{submission.permalink.rstrip('/')}/{rid}/",
    }
