"""Normalized domain models for the Uyám Reddit data-collection pipeline.

All models use Pydantic v2. Datetimes are always timezone-aware UTC.
These models are the single source of truth for the JSONL schema.

Raw Reddit usernames are NOT part of the schema: records carry only
`author_hash` (HMAC-SHA256) and `author_status`, so identifiers are
anonymized before storage (thesis §3.1). `uyam scrub-authors` removes the
plaintext field from files written before this rule.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator

SCHEMA_VERSION = "1.0"

AuthorStatus = Literal["pseudonymized", "deleted", "unavailable"]
SamplingStrategy = Literal["natural", "keyword_oversampled"]
RecordType = Literal["submission", "comment"]
ParentRecordType = Literal["submission", "comment"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SubmissionRecord(BaseModel):
    """Normalized Reddit submission (post)."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    record_type: Literal["submission"] = "submission"
    collection_run_id: str

    reddit_id: str
    reddit_fullname: str  # t3_{reddit_id}
    subreddit: str
    title: str
    selftext: str
    created_utc: datetime

    score: int
    upvote_ratio: float
    num_comments: int

    permalink: str
    url: str
    is_self: bool

    over_18: bool
    spoiler: bool
    stickied: bool
    locked: bool
    archived: bool

    distinguished: str | None = None
    link_flair_text: str | None = None

    is_original_content: bool
    num_crossposts: int
    gilded: int = 0

    author_hash: str | None = None
    author_status: AuthorStatus

    retrieved_at_utc: datetime = Field(default_factory=_utc_now)

    sampling_strategy: SamplingStrategy = "natural"
    matched_query_or_keyword: str | None = None

    @field_validator("created_utc", "retrieved_at_utc", mode="before")
    @classmethod
    def _ensure_utc(cls, v: object) -> datetime:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=UTC)
        if isinstance(v, datetime):
            if v.tzinfo is None:
                return v.replace(tzinfo=UTC)
            return v
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        raise ValueError(f"Cannot convert {v!r} to datetime")

    @field_validator("reddit_fullname")
    @classmethod
    def _fullname_prefix(cls, v: str) -> str:
        if not v.startswith("t3_"):
            raise ValueError(f"Submission fullname must start with 't3_', got {v!r}")
        return v

    @computed_field
    @property
    def collected_at(self) -> datetime:
        """When this record was scraped (UTC). Alias of retrieved_at_utc."""
        return self.retrieved_at_utc

    @computed_field
    @property
    def id(self) -> str:
        """Reddit submission id (same value as reddit_id)."""
        return self.reddit_id

    @computed_field
    @property
    def post_url(self) -> str:
        """Absolute URL to this submission."""
        if self.permalink.startswith("http"):
            return self.permalink
        return "https://www.reddit.com" + self.permalink

    model_config = {"frozen": True}


class CommentRecord(BaseModel):
    """Normalized Reddit comment."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    record_type: Literal["comment"] = "comment"
    collection_run_id: str

    reddit_id: str
    reddit_fullname: str  # t1_{reddit_id}
    submission_id: str  # bare reddit_id of parent submission (no prefix)
    link_id: str  # t3_{submission_id} — raw Reddit link_id

    parent_id: str  # t3_{id} if top-level, t1_{id} if reply
    parent_record_type: ParentRecordType

    subreddit: str

    body: str
    created_utc: datetime
    score: int

    depth: int

    is_submitter: bool
    distinguished: str | None = None
    stickied: bool
    gilded: int = 0
    controversiality: int

    permalink: str

    author_hash: str | None = None
    author_status: AuthorStatus

    retrieved_at_utc: datetime = Field(default_factory=_utc_now)

    @field_validator("created_utc", "retrieved_at_utc", mode="before")
    @classmethod
    def _ensure_utc(cls, v: object) -> datetime:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=UTC)
        if isinstance(v, datetime):
            if v.tzinfo is None:
                return v.replace(tzinfo=UTC)
            return v
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        raise ValueError(f"Cannot convert {v!r} to datetime")

    @field_validator("reddit_fullname")
    @classmethod
    def _fullname_prefix(cls, v: str) -> str:
        if not v.startswith("t1_"):
            raise ValueError(f"Comment fullname must start with 't1_', got {v!r}")
        return v

    @field_validator("link_id")
    @classmethod
    def _link_id_prefix(cls, v: str) -> str:
        if not v.startswith("t3_"):
            raise ValueError(f"link_id must start with 't3_', got {v!r}")
        return v

    @field_validator("parent_id")
    @classmethod
    def _parent_id_prefix(cls, v: str) -> str:
        if not (v.startswith("t1_") or v.startswith("t3_")):
            raise ValueError(f"parent_id must start with 't1_' or 't3_', got {v!r}")
        return v

    @computed_field
    @property
    def collected_at(self) -> datetime:
        """When this record was scraped (UTC). Alias of retrieved_at_utc."""
        return self.retrieved_at_utc

    @computed_field
    @property
    def id(self) -> str:
        """Reddit comment id (same value as reddit_id)."""
        return self.reddit_id

    @computed_field
    @property
    def post_id(self) -> str:
        """Bare id of the parent submission (same as submission_id)."""
        return self.submission_id

    @computed_field
    @property
    def parent_comment_id(self) -> str | None:
        """Bare id of the parent comment, or None if this is a top-level reply to the post."""
        if self.parent_id.startswith("t1_"):
            return self.parent_id[3:]
        return None

    @computed_field
    @property
    def reply_to(self) -> str:
        """'post' for a top-level comment, 'comment' for a reply-to-reply."""
        return "post" if self.parent_record_type == "submission" else "comment"

    @computed_field
    @property
    def post_url(self) -> str:
        """Absolute URL of the parent submission."""
        return f"https://www.reddit.com/r/{self.subreddit}/comments/{self.submission_id}/"

    @computed_field
    @property
    def parent_url(self) -> str:
        """Absolute URL of the immediate parent (post or comment)."""
        if self.parent_record_type == "submission":
            return self.post_url
        return (
            f"https://www.reddit.com/r/{self.subreddit}/comments/"
            f"{self.submission_id}/comment/{self.parent_id[3:]}/"
        )

    @computed_field
    @property
    def comment_url(self) -> str:
        """Absolute URL of this comment."""
        if self.permalink.startswith("http"):
            return self.permalink
        return "https://www.reddit.com" + self.permalink

    model_config = {"frozen": True}


class CollectionContext(BaseModel):
    """Run-level provenance for a single collection run.

    Doubles as the content of the manifest JSON written per run.
    """

    collection_run_id: str
    source_type: str  # "fixture" | "reddit"
    started_at_utc: datetime
    finished_at_utc: datetime | None = None

    subreddit: str
    listing_type: str  # "new" | "hot" | "top" | "search"
    sort_method: str | None = None
    search_query: str | None = None

    requested_limit: int | None = None
    actual_submissions_seen: int = 0
    actual_submissions_stored: int = 0

    comments_seen: int = 0
    comments_stored: int = 0

    duplicates_skipped: int = 0
    validation_failures: int = 0

    collector_version: str
    schema_version: str = SCHEMA_VERSION
    collector_git_commit: str | None = None

    sampling_strategy: SamplingStrategy = "natural"
    matched_query_or_keyword: str | None = None

    errors: list[str] = Field(default_factory=list)
