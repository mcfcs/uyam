"""Live Reddit source using PRAW (Python Reddit API Wrapper).

This source requires REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT,
and AUTHOR_HMAC_KEY to be set in the environment (loaded from .env).

It is read-only and never performs write operations (voting, posting, moderation).
Rate limiting is handled automatically by PRAW via X-Ratelimit-* headers.
Network-level failures (connection errors, timeouts, transient 5xx) are
retried with bounded exponential backoff + jitter.
"""

from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import requests

from uyam.models import CommentRecord, SubmissionRecord
from uyam.privacy import pseudonymize_author
from uyam.sources.base import CollectionRequest

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_REQUIRED_ENV_VARS = (
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USER_AGENT",
    "AUTHOR_HMAC_KEY",
)

# Exceptions that are safe to retry (network/transient)
_RETRYABLE = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

_MAX_RETRY_ATTEMPTS = 5
_MAX_RETRY_DELAY = 60.0


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _with_retry(fn: Any, *, label: str, max_attempts: int = _MAX_RETRY_ATTEMPTS) -> Any:
    """Run fn() with exponential backoff on retryable network failures."""
    for attempt in range(max_attempts):
        try:
            return fn()
        except _RETRYABLE as exc:
            if attempt == max_attempts - 1:
                raise
            delay = min(_MAX_RETRY_DELAY, (2**attempt) + random.uniform(0, 1))
            logger.info(
                "retry_scheduled",
                extra={
                    "label": label,
                    "attempt": attempt + 1,
                    "max_attempts": max_attempts,
                    "delay_seconds": round(delay, 2),
                    "error_type": type(exc).__name__,
                },
            )
            time.sleep(delay)
    raise RuntimeError("Unreachable")  # pragma: no cover


def _check_env() -> None:
    missing = [v for v in _REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Populate .env before using --source reddit."
        )


class PrawRedditSource:
    """Live Reddit data source backed by PRAW.

    Requires Reddit API credentials in the environment.
    Always operates in read-only mode.
    Proxy support is via requestor_kwargs / requests.Session.
    """

    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        ratelimit_seconds: int = 300,
    ) -> None:
        _check_env()
        import praw  # imported lazily so fixture-only runs never need praw

        session = requests.Session()
        if proxy_url:
            session.proxies = {"http": proxy_url, "https": proxy_url}

        self._reddit = praw.Reddit(
            client_id=os.environ["REDDIT_CLIENT_ID"],
            client_secret=os.environ["REDDIT_CLIENT_SECRET"],
            user_agent=os.environ["REDDIT_USER_AGENT"],
            ratelimit_seconds=ratelimit_seconds,
            requestor_kwargs={"session": session},
        )
        self._reddit.read_only = True
        logger.info(
            "praw_source_initialized",
            extra={"read_only": self._reddit.read_only},
        )

    # ------------------------------------------------------------------
    # Static mapping helpers (also used by tests without a live connection)
    # ------------------------------------------------------------------

    @staticmethod
    def _map_submission(
        sub: Any,
        *,
        collection_run_id: str,
        sampling_strategy: str = "natural",
        matched_query_or_keyword: str | None = None,
    ) -> SubmissionRecord:
        author_name: str | None
        if sub.author is None:
            author_name = None
        else:
            try:
                author_name = sub.author.name
            except AttributeError:
                author_name = None

        author_hash, author_status = pseudonymize_author(author_name)

        return SubmissionRecord(
            collection_run_id=collection_run_id,
            reddit_id=sub.id,
            reddit_fullname=sub.name,
            subreddit=sub.subreddit.display_name,
            title=sub.title,
            selftext=sub.selftext or "",
            created_utc=float(sub.created_utc),
            score=int(sub.score),
            upvote_ratio=float(sub.upvote_ratio),
            num_comments=int(sub.num_comments),
            permalink=sub.permalink,
            url=sub.url,
            is_self=bool(sub.is_self),
            over_18=bool(sub.over_18),
            spoiler=bool(getattr(sub, "spoiler", False)),
            stickied=bool(sub.stickied),
            locked=bool(sub.locked),
            archived=bool(sub.archived),
            distinguished=sub.distinguished,
            link_flair_text=sub.link_flair_text,
            is_original_content=bool(sub.is_original_content),
            num_crossposts=int(sub.num_crossposts),
            gilded=int(getattr(sub, "gilded", 0)),
            author=author_name if author_status == "pseudonymized" else None,
            author_hash=author_hash,
            author_status=author_status,
            retrieved_at_utc=_utc_now(),
            sampling_strategy=sampling_strategy,  # type: ignore[arg-type]
            matched_query_or_keyword=matched_query_or_keyword,
        )

    @staticmethod
    def _map_comment(
        com: Any,
        *,
        collection_run_id: str,
    ) -> CommentRecord:
        author_name: str | None
        if com.author is None:
            author_name = None
        else:
            try:
                author_name = com.author.name
            except AttributeError:
                author_name = None

        author_hash, author_status = pseudonymize_author(author_name)

        link_id: str = com.link_id
        parent_id: str = com.parent_id
        submission_id = link_id[3:] if link_id.startswith("t3_") else link_id
        parent_record_type = "submission" if parent_id.startswith("t3_") else "comment"

        return CommentRecord(
            collection_run_id=collection_run_id,
            reddit_id=com.id,
            reddit_fullname=com.name,
            submission_id=submission_id,
            link_id=link_id,
            parent_id=parent_id,
            parent_record_type=parent_record_type,  # type: ignore[arg-type]
            subreddit=com.subreddit.display_name,
            body=com.body,
            created_utc=float(com.created_utc),
            score=int(com.score),
            depth=int(getattr(com, "depth", 0)),
            is_submitter=bool(com.is_submitter),
            distinguished=com.distinguished,
            stickied=bool(com.stickied),
            gilded=int(getattr(com, "gilded", 0)),
            controversiality=int(com.controversiality),
            permalink=com.permalink,
            author=author_name if author_status == "pseudonymized" else None,
            author_hash=author_hash,
            author_status=author_status,
            retrieved_at_utc=_utc_now(),
        )

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def iter_submissions(
        self,
        request: CollectionRequest,
    ) -> Iterable[SubmissionRecord]:
        subreddit = self._reddit.subreddit(request.subreddit)
        limit = request.limit

        if request.listing_type == "search" and request.search_query:
            listing = subreddit.search(
                request.search_query,
                sort=request.sort or "relevance",
                time_filter=request.time_filter or "all",
                limit=limit,
            )
        elif request.listing_type == "new":
            listing = subreddit.new(limit=limit)
        elif request.listing_type == "hot":
            listing = subreddit.hot(limit=limit)
        elif request.listing_type == "top":
            listing = subreddit.top(
                time_filter=request.time_filter or "all",
                limit=limit,
            )
        else:
            raise ValueError(f"Unsupported listing_type: {request.listing_type!r}")

        for raw_sub in listing:
            record = _with_retry(
                lambda s=raw_sub: self._map_submission(
                    s,
                    collection_run_id=request.collection_run_id,
                    sampling_strategy=request.sampling_strategy,
                    matched_query_or_keyword=request.matched_query_or_keyword,
                ),
                label=f"map_submission:{raw_sub.id}",
            )
            logger.debug(
                "submission_seen",
                extra={
                    "reddit_fullname": record.reddit_fullname,
                    "subreddit": record.subreddit,
                    "source": "reddit",
                    "collection_run_id": request.collection_run_id,
                },
            )
            yield record

    def iter_comments(
        self,
        submission: SubmissionRecord,
        request: CollectionRequest,
    ) -> Iterable[CommentRecord]:
        import praw.models

        raw_sub = _with_retry(
            lambda: self._reddit.submission(id=submission.reddit_id),
            label=f"fetch_submission:{submission.reddit_id}",
        )

        replace_more_limit = request.replace_more_limit
        _with_retry(
            lambda: raw_sub.comments.replace_more(limit=replace_more_limit),
            label=f"replace_more:{submission.reddit_id}",
        )

        limit = request.max_comments_per_submission
        count = 0

        for com in raw_sub.comments.list():
            if not isinstance(com, praw.models.Comment):
                continue
            if limit is not None and count >= limit:
                break
            if request.max_depth is not None and getattr(com, "depth", 0) > request.max_depth:
                continue
            if not request.include_deleted and com.body in ("[deleted]", "[removed]"):
                continue

            record = _with_retry(
                lambda c=com: self._map_comment(
                    c, collection_run_id=request.collection_run_id
                ),
                label=f"map_comment:{com.id}",
            )
            logger.debug(
                "comment_seen",
                extra={
                    "reddit_fullname": record.reddit_fullname,
                    "source": "reddit",
                    "collection_run_id": request.collection_run_id,
                },
            )
            count += 1
            yield record
