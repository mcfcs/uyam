"""Source interface for the Uyám Reddit data-collection pipeline.

Downstream pipeline code depends only on these abstractions.
It never imports FixtureRedditSource or PrawRedditSource directly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from uyam.models import CommentRecord, SubmissionRecord


@dataclass
class CollectionRequest:
    """Parameters for a single subreddit collection pass."""

    subreddit: str
    listing_type: str  # "new" | "hot" | "top" | "search"
    collection_run_id: str

    limit: int | None = None
    sort: str | None = None
    time_filter: str = "all"
    search_query: str | None = None

    max_comments_per_submission: int | None = 100
    max_depth: int | None = None
    include_deleted: bool = False
    comment_sort: str = "confidence"
    replace_more_limit: int = 32  # passed to PRAW replace_more(limit=N)

    sampling_strategy: str = "natural"
    matched_query_or_keyword: str | None = None

    # Wall-clock cap for one collection pass. None or <=0 means no limit.
    max_seconds: float | None = None

    # Calendar window (UTC days). When set with posts_per_day, the source
    # walks timestamp-search slices instead of the live /new feed.
    calendar_since: str | None = None  # YYYY-MM-DD
    calendar_until: str | None = None  # YYYY-MM-DD inclusive; None = today UTC
    posts_per_day: int | None = None

    # Optional fullname → already-collected? Used to skip comment harvest.
    is_seen: Callable[[str], bool] | None = field(default=None, repr=False, compare=False)

    extra: dict[str, object] = field(default_factory=dict)


class RedditSource(Protocol):
    """Abstract source of Reddit data.

    Both FixtureRedditSource and PrawRedditSource implement this protocol.
    The pipeline never knows which implementation it is using.
    """

    def iter_submissions(
        self,
        request: CollectionRequest,
    ) -> Iterable[SubmissionRecord]:
        """Yield normalized submissions for the given request."""
        ...

    def iter_comments(
        self,
        submission: SubmissionRecord,
        request: CollectionRequest,
    ) -> Iterable[CommentRecord]:
        """Yield normalized comments for a submission, preserving tree relationships."""
        ...
