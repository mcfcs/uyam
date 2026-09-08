"""Credential-free Reddit source using Reddit's public JSON endpoints.

This source reads the same public, unauthenticated `.json` responses that a
web browser receives (e.g. https://www.reddit.com/r/<sub>/new.json). It needs
no Reddit API app and no credentials.

Guardrails (identical spirit to the rest of the project):
  * Only public endpoints Reddit already serves unauthenticated are used.
  * A descriptive User-Agent is always sent.
  * Requests are rate-limited politely (a deliberate minimum interval) and
    HTTP 429 triggers exponential backoff. This RESPECTS Reddit's limits; it
    does not evade them.
  * No browser automation, no CAPTCHA solving, no Pushshift.
  * Author identifiers are pseudonymized exactly as every other source.

Caveats surfaced to the researcher: unauthenticated JSON is heavily throttled
(~10 req/min) and automated access sits in a ToS gray area under Reddit's 2023
Data API Terms. The researcher remains responsible for ToS/ethics compliance.
"""

from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Iterable, Iterator
from typing import Any

import requests

from uyam.models import CommentRecord, SubmissionRecord
from uyam.sources.base import CollectionRequest
from uyam.sources.mapping import map_comment_dict, map_submission_dict

logger = logging.getLogger(__name__)

_BASE = "https://www.reddit.com"
_DEFAULT_USER_AGENT = "python:uyam-collector:v0.1.0 (public-json)"
_PAGE_CAP = 100  # Reddit caps a listing page at 100 items

# Network failures / responses that are safe to retry.
_RETRYABLE_EXC = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

_MAX_RETRY_ATTEMPTS = 5
_MAX_RETRY_DELAY = 60.0


class PublicJsonRedditSource:
    """Live Reddit data source backed by public, unauthenticated JSON endpoints.

    Implements the RedditSource protocol so the pipeline is unaware of it.
    """

    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        min_interval_seconds: float = 6.0,
        timeout_seconds: float = 30.0,
        user_agent: str | None = None,
    ) -> None:
        self._min_interval = max(0.0, min_interval_seconds)
        self._timeout = timeout_seconds
        self._last_request_at: float | None = None

        self._session = requests.Session()
        ua = user_agent or os.environ.get("REDDIT_USER_AGENT") or _DEFAULT_USER_AGENT
        self._session.headers.update({"User-Agent": ua})
        if proxy_url:
            self._session.proxies = {"http": proxy_url, "https": proxy_url}

        logger.info(
            "public_json_source_initialized",
            extra={"min_interval_seconds": self._min_interval},
        )

    # ------------------------------------------------------------------
    # HTTP with politeness + bounded backoff
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        """Sleep so consecutive requests honor the minimum interval."""
        if self._last_request_at is None or self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        wait = self._min_interval - elapsed
        if wait > 0:
            time.sleep(wait)

    def _get_json(self, url: str, *, params: dict[str, Any], label: str) -> Any:
        """GET a public JSON endpoint with polite pacing and bounded backoff."""
        for attempt in range(_MAX_RETRY_ATTEMPTS):
            self._throttle()
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)
                self._last_request_at = time.monotonic()
            except _RETRYABLE_EXC as exc:
                if attempt == _MAX_RETRY_ATTEMPTS - 1:
                    raise
                self._backoff(attempt, label=label, reason=type(exc).__name__)
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                if attempt == _MAX_RETRY_ATTEMPTS - 1:
                    resp.raise_for_status()
                # Honor Retry-After when Reddit provides it.
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                self._backoff(
                    attempt, label=label, reason=f"HTTP {resp.status_code}", floor=retry_after
                )
                continue

            resp.raise_for_status()
            return resp.json()

        raise RuntimeError("Unreachable")  # pragma: no cover

    def _backoff(
        self, attempt: int, *, label: str, reason: str, floor: float | None = None
    ) -> None:
        delay = min(_MAX_RETRY_DELAY, (2**attempt) + random.uniform(0, 1))
        if floor is not None:
            delay = min(_MAX_RETRY_DELAY, max(delay, floor))
        logger.info(
            "public_json_retry_scheduled",
            extra={
                "label": label,
                "attempt": attempt + 1,
                "max_attempts": _MAX_RETRY_ATTEMPTS,
                "delay_seconds": round(delay, 2),
                "reason": reason,
            },
        )
        time.sleep(delay)

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def iter_submissions(
        self,
        request: CollectionRequest,
    ) -> Iterable[SubmissionRecord]:
        limit = request.limit
        yielded = 0
        after: str | None = None

        while True:
            page_size = _PAGE_CAP if limit is None else min(_PAGE_CAP, limit - yielded)
            if limit is not None and page_size <= 0:
                return

            url, params = _listing_endpoint(request, page_size=page_size, after=after)
            payload = self._get_json(url, params=params, label=f"listing:{request.subreddit}")

            children = payload.get("data", {}).get("children", [])
            if not children:
                return

            for child in children:
                if child.get("kind") != "t3":
                    continue
                data = child.get("data", {})
                record = map_submission_dict(
                    data,
                    collection_run_id=request.collection_run_id,
                    sampling_strategy=request.sampling_strategy,
                    matched_query_or_keyword=request.matched_query_or_keyword,
                )
                logger.debug(
                    "submission_seen",
                    extra={
                        "reddit_fullname": record.reddit_fullname,
                        "subreddit": record.subreddit,
                        "source": "public",
                        "collection_run_id": request.collection_run_id,
                    },
                )
                yield record
                yielded += 1
                if limit is not None and yielded >= limit:
                    return

            after = payload.get("data", {}).get("after")
            if not after:
                return

    def iter_comments(
        self,
        submission: SubmissionRecord,
        request: CollectionRequest,
    ) -> Iterable[CommentRecord]:
        url = f"{_BASE}/comments/{submission.reddit_id}.json"
        params: dict[str, Any] = {
            "raw_json": 1,
            "sort": request.comment_sort,
        }
        if request.max_comments_per_submission:
            params["limit"] = request.max_comments_per_submission
        if request.max_depth is not None:
            params["depth"] = request.max_depth

        payload = self._get_json(
            url, params=params, label=f"comments:{submission.reddit_id}"
        )

        # payload is [submission_listing, comment_listing]
        if not isinstance(payload, list) or len(payload) < 2:
            return
        comment_listing = payload[1].get("data", {}).get("children", [])

        limit = request.max_comments_per_submission
        count = 0
        more_skipped = 0

        for raw_com, traversed_depth in _flatten_comment_tree(comment_listing):
            if raw_com is _MORE_SENTINEL:
                more_skipped += 1
                continue
            if limit is not None and count >= limit:
                break

            depth = int(raw_com.get("depth", traversed_depth))
            if request.max_depth is not None and depth > request.max_depth:
                continue
            body = raw_com.get("body")
            if not request.include_deleted and body in ("[deleted]", "[removed]"):
                continue

            # Ensure depth reflects what we observed if Reddit omitted it.
            raw_com.setdefault("depth", traversed_depth)
            record = map_comment_dict(
                raw_com,
                collection_run_id=request.collection_run_id,
                sampling_strategy=submission.sampling_strategy,
                matched_query_or_keyword=submission.matched_query_or_keyword,
            )
            logger.debug(
                "comment_seen",
                extra={
                    "reddit_fullname": record.reddit_fullname,
                    "source": "public",
                    "collection_run_id": request.collection_run_id,
                },
            )
            count += 1
            yield record

        if more_skipped:
            logger.info(
                "public_json_more_truncated",
                extra={
                    "submission_id": submission.reddit_id,
                    "more_nodes_skipped": more_skipped,
                    "collection_run_id": request.collection_run_id,
                },
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MORE_SENTINEL: dict[str, Any] = {"__more__": True}


def _listing_endpoint(
    request: CollectionRequest, *, page_size: int, after: str | None
) -> tuple[str, dict[str, Any]]:
    """Build the (url, params) for a submission listing request."""
    sub = request.subreddit
    params: dict[str, Any] = {"limit": page_size, "raw_json": 1}
    if after:
        params["after"] = after

    if request.listing_type == "search" and request.search_query:
        params.update(
            {
                "q": request.search_query,
                "restrict_sr": 1,
                "sort": request.sort or "relevance",
                "t": request.time_filter or "all",
            }
        )
        return f"{_BASE}/r/{sub}/search.json", params

    if request.listing_type == "top":
        params["t"] = request.time_filter or "all"
        return f"{_BASE}/r/{sub}/top.json", params

    if request.listing_type in ("new", "hot"):
        return f"{_BASE}/r/{sub}/{request.listing_type}.json", params

    raise ValueError(f"Unsupported listing_type: {request.listing_type!r}")


def _flatten_comment_tree(
    children: list[dict[str, Any]], depth: int = 0
) -> Iterator[tuple[dict[str, Any], int]]:
    """Depth-first flatten a nested comment listing.

    Yields (comment_data, depth) for each t1 node in tree order. A `more`
    node yields (_MORE_SENTINEL, depth) so the caller can count truncations.
    """
    for child in children:
        kind = child.get("kind")
        data = child.get("data", {})
        if kind == "more":
            yield _MORE_SENTINEL, depth
            continue
        if kind != "t1":
            continue

        # Detach replies before yielding so the mapped dict stays flat.
        replies = data.get("replies")
        yield data, depth

        if isinstance(replies, dict):
            grandchildren = replies.get("data", {}).get("children", [])
            yield from _flatten_comment_tree(grandchildren, depth + 1)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header value in seconds, if present and numeric."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
