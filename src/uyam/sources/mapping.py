"""Shared raw-dict -> normalized record mappers for the Uyám pipeline.

Reddit's public JSON `data` objects and the fixture JSON entries share the
same field names, so a single normalization path serves both the fixture
source and the public-JSON source. These functions are the single source of
truth for turning a raw Reddit-shaped dict into a normalized domain record.

Raw usernames are consumed here only to derive author_hash (HMAC-SHA256)
and author_status; they are never stored on the record and never logged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from uyam.models import CommentRecord, SubmissionRecord
from uyam.privacy import pseudonymize_author


def _utc_now() -> datetime:
    return datetime.now(UTC)


def map_submission_dict(
    raw: dict[str, Any],
    *,
    collection_run_id: str,
    sampling_strategy: str = "natural",
    matched_query_or_keyword: str | None = None,
) -> SubmissionRecord:
    """Normalize a raw Reddit-shaped submission dict into a SubmissionRecord."""
    raw_author = raw.get("author")
    author_hash, author_status = pseudonymize_author(
        raw_author if isinstance(raw_author, str) else None
    )
    return SubmissionRecord(
        collection_run_id=collection_run_id,
        reddit_id=raw["id"],
        reddit_fullname=raw["name"],
        subreddit=raw["subreddit"],
        title=raw["title"],
        selftext=raw.get("selftext", ""),
        created_utc=raw["created_utc"],
        score=int(raw["score"]),
        upvote_ratio=float(raw["upvote_ratio"]),
        num_comments=int(raw["num_comments"]),
        permalink=raw["permalink"],
        url=raw["url"],
        is_self=bool(raw["is_self"]),
        over_18=bool(raw["over_18"]),
        spoiler=bool(raw.get("spoiler", False)),
        stickied=bool(raw.get("stickied", False)),
        locked=bool(raw.get("locked", False)),
        archived=bool(raw.get("archived", False)),
        distinguished=raw.get("distinguished"),
        link_flair_text=raw.get("link_flair_text"),
        is_original_content=bool(raw.get("is_original_content", False)),
        num_crossposts=int(raw.get("num_crossposts", 0)),
        gilded=int(raw.get("gilded", 0)),
        author_hash=author_hash,
        author_status=author_status,
        retrieved_at_utc=_utc_now(),
        sampling_strategy=sampling_strategy,  # type: ignore[arg-type]
        matched_query_or_keyword=matched_query_or_keyword,
    )


def map_comment_dict(
    raw: dict[str, Any],
    *,
    collection_run_id: str,
    sampling_strategy: str = "natural",
    matched_query_or_keyword: str | None = None,
) -> CommentRecord:
    """Normalize a raw Reddit-shaped comment dict into a CommentRecord.

    `sampling_strategy` / `matched_query_or_keyword` are the parent
    submission's values (comments inherit how their thread was found).
    """
    raw_author = raw.get("author")
    author_hash, author_status = pseudonymize_author(
        raw_author if isinstance(raw_author, str) else None
    )
    parent_id: str = raw["parent_id"]
    parent_record_type = "submission" if parent_id.startswith("t3_") else "comment"
    link_id: str = raw["link_id"]
    submission_id = link_id[3:] if link_id.startswith("t3_") else link_id

    return CommentRecord(
        collection_run_id=collection_run_id,
        reddit_id=raw["id"],
        reddit_fullname=raw["name"],
        submission_id=submission_id,
        link_id=link_id,
        parent_id=parent_id,
        parent_record_type=parent_record_type,  # type: ignore[arg-type]
        subreddit=raw["subreddit"],
        body=raw["body"],
        created_utc=raw["created_utc"],
        score=int(raw["score"]),
        depth=int(raw.get("depth", 0)),
        is_submitter=bool(raw.get("is_submitter", False)),
        distinguished=raw.get("distinguished"),
        stickied=bool(raw.get("stickied", False)),
        gilded=int(raw.get("gilded", 0)),
        controversiality=int(raw.get("controversiality", 0)),
        permalink=raw["permalink"],
        author_hash=author_hash,
        author_status=author_status,
        retrieved_at_utc=_utc_now(),
        sampling_strategy=sampling_strategy,  # type: ignore[arg-type]
        matched_query_or_keyword=matched_query_or_keyword,
    )
