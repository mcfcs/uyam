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

Records are mapped through sources/mapping.py, the one place a raw username is
read (to derive author_hash with the same AUTHOR_HMAC_KEY as every other
source). Archive scores and comment counts are snapshots taken when the
archive ingested the item, not current values.

The API throttles bursts by answering {"data": null, "error": "Timeout. Maybe
slow down a bit"} with an `x-ratelimit-reset` header (seconds); requests are
paced and such answers are retried after the advertised reset.
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

import requests

from uyam.calendar_window import day_bounds_unix, iter_utc_days, parse_iso_date
from uyam.models import CommentRecord, SubmissionRecord
from uyam.sources.base import CollectionRequest
from uyam.sources.mapping import map_comment_dict, map_submission_dict

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://arctic-shift.photon-reddit.com/api"
_PLACEHOLDER_TEXT = {"[deleted]", "[removed]", "[deleted by user]"}
_RETRY_STATUS = {429, 500, 502, 503, 504}
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
        page_size: int = 100,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._session.headers.setdefault(
            "User-Agent", "uyam-collector (thesis research; Taglish sarcasm dataset)"
        )
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._timeout = float(timeout_seconds)
        self._max_retries = max(1, int(max_retries))
        self._page_size = max(1, min(100, int(page_size)))
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _pace(self) -> None:
        if self._last_request is not None:
            wait = self._min_interval - (self._clock() - self._last_request)
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
        url = f"{self._base_url}{path}"
        for attempt in range(self._max_retries):
            self._pace()
            reason: str
            reset: float | None = None
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
                throttled = any(m in error.lower() for m in _THROTTLE_MARKERS)
                if resp.status_code in _RETRY_STATUS or throttled:
                    reason = error[:80] or f"http_{resp.status_code}"
                elif resp.status_code >= 400:
                    raise ArchiveApiError(
                        f"archive {path} returned HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
                    return list(payload["data"])
                else:
                    reason = error[:80] or "invalid_json"
            if attempt == self._max_retries - 1:
                break
            backoff = min(120.0, (2**attempt) + random.uniform(0, 1))
            delay = max(backoff, (reset or 0.0) + 1.0)
            logger.info(
                "archive_retry",
                extra={
                    "path": path,
                    "attempt": attempt + 1,
                    "reason": reason,
                    "delay_seconds": round(delay, 1),
                },
            )
            self._sleep(delay)
        raise ArchiveApiError(f"archive {path} failed after {self._max_retries} attempts")

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
            query = {**params, "after": cursor, "limit": self._page_size, "sort": "asc"}
            if before is not None:
                query["before"] = before
            page = self._get(path, query)
            fresh = [item for item in page if str(item.get("id")) not in seen]
            for item in fresh:
                seen.add(str(item.get("id")))
                yield item
            if len(page) < self._page_size:
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
            for raw in posts:
                yield map_submission_dict(
                    _submission_defaults(raw, request.subreddit),
                    collection_run_id=request.collection_run_id,
                    sampling_strategy=request.sampling_strategy,
                    matched_query_or_keyword=request.matched_query_or_keyword,
                )
                yielded += 1
                if request.limit and yielded >= request.limit:
                    return

    def iter_comments(
        self, submission: SubmissionRecord, request: CollectionRequest
    ) -> Iterable[CommentRecord]:
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
