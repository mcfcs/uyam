"""Candidate selection: which corpus records are eligible annotation targets.

Every exclusion writes a reason so the dataset card can report exactly what
was dropped and why (thesis preprocessing: bots, deleted, non-text, too short).
"""

from __future__ import annotations

import re
from typing import Any

from uyam.annotate.config import CandidateFiltersConfig
from uyam.annotate.db import AnnotationDatabase

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARKUP_RE = re.compile(r"[*_~`>#|]+")
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ'ñÑ]+")

_PLACEHOLDER_BODIES = {"[deleted]", "[removed]", "[deleted by user]"}


def normalize_text(text: str) -> str:
    """Strip URLs and markdown so length/token filters see actual prose."""
    text = _MD_LINK_RE.sub(r"\1", text)  # before URL stripping: keep the link text
    text = _URL_RE.sub(" ", text)
    text = _MD_MARKUP_RE.sub(" ", text)
    return " ".join(text.split())


def _exclusion_reason(
    record: dict[str, Any], filters: CandidateFiltersConfig
) -> tuple[str | None, str]:
    """Return (exclusion_reason | None, normalized_text)."""
    text = str(record.get("text") or "")
    norm = normalize_text(text)

    if record.get("is_bot"):
        return "bot_author", norm
    if filters.exclude_distinguished_moderator and (
        record.get("distinguished") == "moderator" and record.get("stickied")
    ):
        return "moderator_sticky", norm
    if record.get("author_status") == "deleted":
        return "deleted_author", norm
    if text.strip().lower() in _PLACEHOLDER_BODIES:
        return "deleted_body", norm
    if (
        record.get("record_type") == "submission"
        and not filters.include_link_posts
        and record.get("is_self") == 0
        and not str(record.get("selftext") or "").strip()
    ):
        return "link_post", norm
    if len(norm) < filters.min_chars:
        return "too_short", norm
    if len(_TOKEN_RE.findall(norm)) < filters.min_tokens:
        return "too_few_tokens", norm
    return None, norm


def select_candidates(
    db: AnnotationDatabase, filters: CandidateFiltersConfig
) -> dict[str, int]:
    """(Re)compute eligibility for every indexed record. Idempotent."""
    rows = db.conn.execute("SELECT * FROM corpus_index").fetchall()
    stats = {"eligible": 0, "excluded": 0}
    for row in rows:
        record = dict(row)
        reason, norm = _exclusion_reason(record, filters)
        db.upsert_candidate(
            record["reddit_fullname"],
            eligible=reason is None,
            exclusion_reason=reason,
            text_norm=norm,
        )
        stats["eligible" if reason is None else "excluded"] += 1
    db.commit()
    return stats
