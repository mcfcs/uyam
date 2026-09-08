"""Fixture-based Reddit source for the Uyám pipeline.

Reads from fixtures/sample_posts.json and produces the same normalized
SubmissionRecord / CommentRecord types as PrawRedditSource.

Fixture data is validated strictly before any records are yielded.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from uyam.models import CommentRecord, SubmissionRecord
from uyam.sources.base import CollectionRequest
from uyam.sources.mapping import map_comment_dict, map_submission_dict

logger = logging.getLogger(__name__)

_FULLNAME_RE = re.compile(r"^t[1-6]_\S+$")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class FixtureValidationError(Exception):
    """Raised when fixture data fails strict validation."""


class FixtureValidationWarning(UserWarning):
    """Issued for non-fatal validation issues in lenient mode."""


def _require(condition: bool, msg: str) -> None:
    if not condition:
        raise FixtureValidationError(msg)


def validate_fixture(data: dict[str, Any], *, lenient: bool = False) -> list[str]:
    """Validate fixture data.

    Strict mode (lenient=False): raises FixtureValidationError on any problem.
    Lenient mode (lenient=True): collects warnings and returns them.
    Returns a list of warning strings (empty when all is well).
    """
    import warnings

    warnings_list: list[str] = []

    def _warn_or_raise(msg: str) -> None:
        if lenient:
            warnings_list.append(msg)
            warnings.warn(msg, FixtureValidationWarning, stacklevel=3)
        else:
            raise FixtureValidationError(msg)

    _require(isinstance(data, dict), "Fixture root must be a JSON object")
    _require("submissions" in data, "Fixture must contain a 'submissions' key")
    _require(isinstance(data["submissions"], list), "'submissions' must be a list")

    seen_fullnames: set[str] = set()
    submission_ids: set[str] = set()

    for idx, sub in enumerate(data["submissions"]):
        prefix = f"submissions[{idx}]"

        for field in ("id", "name", "subreddit", "title", "selftext", "author"):
            if field not in sub:
                _warn_or_raise(f"{prefix}: missing required field '{field}'")
            elif not isinstance(sub.get(field), str):
                _warn_or_raise(f"{prefix}.{field}: expected string")

        for field in (
            "created_utc", "score", "num_comments", "num_crossposts"
        ):
            if field not in sub:
                _warn_or_raise(f"{prefix}: missing required field '{field}'")
            elif not isinstance(sub.get(field), (int, float)):
                _warn_or_raise(f"{prefix}.{field}: expected number")

        for field in (
            "upvote_ratio",
        ):
            if field not in sub:
                _warn_or_raise(f"{prefix}: missing required field '{field}'")
            elif not isinstance(sub.get(field), (int, float)):
                _warn_or_raise(f"{prefix}.{field}: expected number")

        for field in (
            "is_self", "over_18", "spoiler", "stickied",
            "locked", "archived", "is_original_content",
        ):
            if field not in sub:
                _warn_or_raise(f"{prefix}: missing required field '{field}'")
            elif not isinstance(sub.get(field), bool):
                _warn_or_raise(f"{prefix}.{field}: expected boolean")

        for field in ("permalink", "url"):
            if field not in sub:
                _warn_or_raise(f"{prefix}: missing required field '{field}'")

        sub_id = sub.get("id", "")
        fullname = sub.get("name", "")

        if fullname:
            if not fullname.startswith("t3_"):
                _warn_or_raise(
                    f"{prefix}: 'name' must start with 't3_', got {fullname!r}"
                )
            if fullname in seen_fullnames:
                _warn_or_raise(
                    f"{prefix}: duplicate reddit_fullname {fullname!r}"
                )
            seen_fullnames.add(fullname)
            submission_ids.add(sub_id)

        comments_raw = sub.get("comments", [])
        if not isinstance(comments_raw, list):
            _warn_or_raise(f"{prefix}.comments: expected list")
            continue

        seen_comment_fullnames: set[str] = set()
        for cidx, com in enumerate(comments_raw):
            cprefix = f"{prefix}.comments[{cidx}]"

            for field in ("id", "name", "subreddit", "body", "author",
                          "link_id", "parent_id", "permalink"):
                if field not in com:
                    _warn_or_raise(f"{cprefix}: missing required field '{field}'")
                elif not isinstance(com.get(field), str):
                    _warn_or_raise(f"{cprefix}.{field}: expected string")

            for field in ("created_utc", "score", "depth", "controversiality"):
                if field not in com:
                    _warn_or_raise(f"{cprefix}: missing required field '{field}'")
                elif not isinstance(com.get(field), (int, float)):
                    _warn_or_raise(f"{cprefix}.{field}: expected number")

            for field in ("is_submitter", "stickied"):
                if field not in com:
                    _warn_or_raise(f"{cprefix}: missing required field '{field}'")
                elif not isinstance(com.get(field), bool):
                    _warn_or_raise(f"{cprefix}.{field}: expected boolean")

            com_fullname = com.get("name", "")
            if com_fullname:
                if not com_fullname.startswith("t1_"):
                    _warn_or_raise(
                        f"{cprefix}: 'name' must start with 't1_', got {com_fullname!r}"
                    )
                if com_fullname in seen_fullnames or com_fullname in seen_comment_fullnames:
                    _warn_or_raise(
                        f"{cprefix}: duplicate reddit_fullname {com_fullname!r}"
                    )
                seen_comment_fullnames.add(com_fullname)
                seen_fullnames.add(com_fullname)

            # Validate parent_id references exist within fixture
            parent_id = com.get("parent_id", "")
            if parent_id and not lenient and parent_id.startswith("t3_"):
                referenced_sub_id = parent_id[3:]
                if referenced_sub_id != sub_id:
                    _warn_or_raise(
                        f"{cprefix}: parent_id {parent_id!r} references "
                        f"submission {referenced_sub_id!r} but comment is "
                        f"under submission {sub_id!r}"
                    )
            # t1_ parent references are validated post-collection since
            # comments may appear before their parents in list order

            # subreddit must match parent submission
            com_subreddit = com.get("subreddit", "")
            sub_subreddit = sub.get("subreddit", "")
            if com_subreddit and sub_subreddit and com_subreddit != sub_subreddit:
                _warn_or_raise(
                    f"{cprefix}: comment subreddit {com_subreddit!r} != "
                    f"submission subreddit {sub_subreddit!r}"
                )

    return warnings_list


# ---------------------------------------------------------------------------
# Normalization helpers
#
# The raw-dict -> record mapping lives in sources/mapping.py so the fixture
# source and the public-JSON source share one normalization path. These
# module-level aliases preserve the existing internal call sites.
# ---------------------------------------------------------------------------

_map_submission = map_submission_dict
_map_comment = map_comment_dict


# ---------------------------------------------------------------------------
# Source implementation
# ---------------------------------------------------------------------------

class FixtureRedditSource:
    """Reads fixture JSON and feeds records through the same pipeline as live Reddit data."""

    def __init__(
        self,
        fixture_path: Path | None = None,
        *,
        lenient_validation: bool = False,
    ) -> None:
        if fixture_path is None:
            fixture_path = (
                Path(__file__).parent.parent.parent.parent / "fixtures" / "sample_posts.json"
            )
        self._fixture_path = fixture_path
        self._lenient = lenient_validation
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            raw = self._fixture_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            warnings = validate_fixture(data, lenient=self._lenient)
            if warnings:
                for w in warnings:
                    logger.warning("fixture_validation_warning: %s", w)
            self._data = data
        return self._data

    def iter_submissions(
        self,
        request: CollectionRequest,
    ) -> Iterable[SubmissionRecord]:
        data = self._load()
        count = 0
        for raw_sub in data["submissions"]:
            if raw_sub["subreddit"] != request.subreddit:
                continue
            if request.limit is not None and count >= request.limit:
                break

            sub = _map_submission(
                raw_sub,
                collection_run_id=request.collection_run_id,
                sampling_strategy=request.sampling_strategy,
                matched_query_or_keyword=request.matched_query_or_keyword,
            )
            logger.debug(
                "submission_seen",
                extra={
                    "reddit_fullname": sub.reddit_fullname,
                    "subreddit": sub.subreddit,
                    "source": "fixture",
                    "collection_run_id": request.collection_run_id,
                },
            )
            count += 1
            yield sub

    def iter_comments(
        self,
        submission: SubmissionRecord,
        request: CollectionRequest,
    ) -> Iterable[CommentRecord]:
        data = self._load()
        raw_sub = next(
            (s for s in data["submissions"] if s["id"] == submission.reddit_id),
            None,
        )
        if raw_sub is None:
            return

        comments_raw = raw_sub.get("comments", [])
        limit = request.max_comments_per_submission
        count = 0

        for raw_com in comments_raw:
            if limit is not None and count >= limit:
                break
            if request.max_depth is not None and raw_com.get("depth", 0) > request.max_depth:
                continue
            if not request.include_deleted and raw_com.get("body") in ("[deleted]", "[removed]"):
                continue

            com = _map_comment(
                raw_com,
                collection_run_id=request.collection_run_id,
                sampling_strategy=submission.sampling_strategy,
                matched_query_or_keyword=submission.matched_query_or_keyword,
            )
            logger.debug(
                "comment_seen",
                extra={
                    "reddit_fullname": com.reddit_fullname,
                    "subreddit": com.subreddit,
                    "source": "fixture",
                    "collection_run_id": request.collection_run_id,
                },
            )
            count += 1
            yield com
