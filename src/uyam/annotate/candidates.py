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

# Phatic items: social-ritual text (birthday greetings, thanks, congratulations,
# well-wishes, follow-bait) with no propositional content to be sarcastic about.
# The rule is deliberately conservative — a missed phatic item only costs one
# annotation, a dropped sarcastic one costs a scarce positive:
#   - a phatic phrase must match, the item must be short, and at most
#     `phatic_max_leftover` content tokens may remain once phrases, fillers
#     and laughter are removed;
#   - any irony marker vetoes the rule ("edi congrats", "thanks for nothing",
#     "ikaw na" …). "ang galing naman" / "wow" are oversampling markers, so
#     they are guards here, never phatic phrases.
_ELONGATION_RE = re.compile(r"([a-zñ])\1{2,}")  # "youuuu" -> "you", "thankssss" -> "thanks"
_PHATIC_RE = re.compile(
    r"\b(?:"
    r"happy\s+(?:belated\s+)?(?:birthday|bday|b-day)|hbd|maligayang\s+kaarawan"
    r"|congrat(?:s|ulations)?|thank\s*(?:s+|you|u|yo)|tysm|ty|salamat|maraming\s+salamat"
    r"|good\s+(?:morning|afternoon|evening|night|luck)|god\s*bless|ingat"
    r"|merry\s+christmas|happy\s+(?:new\s+year|holidays)|welcome(?!\s+to\b)"
    r"|condolences|get\s+well\s+soon|commenting\s+to\s+follow|following"
    r")\b",
    re.IGNORECASE,
)
_PHATIC_FILLER = frozenset(
    {
        "po", "poh", "op", "ate", "kuya", "sir", "maam", "ma'am", "guys", "everyone", "all",
        "sa", "mo", "ko", "you", "u", "ya", "so", "much", "very", "too", "din", "rin", "again",
        "ulit", "ha", "ah", "oh", "aw", "aww", "yay", "yey", "omg", "the", "a", "to", "and",
        "my", "your", "me", "us", "kayo", "ka", "day", "enjoy", "more", "many", "come",
        "blessings", "blessed", "best", "wishes", "cheers", "stay", "safe", "always", "nice",
        "work"
    }
)
_LAUGH_RE = re.compile(r"^(?:(?:ha|he|hi|hu|ah|eh)+h?|l+o+l+|lmao+|x+d+)$")
_IRONY_GUARD = frozenset(
    {
        "edi", "sana", "wow", "galing", "talaga", "daw", "raw", "kuno", "naman", "pala", "sure",
        "yeah", "right", "great", "totally", "ikaw", "lang", "nothing", "wala", "ever",
        "sarcasm"
    }
)


def is_phatic(norm: str, *, max_tokens: int, max_leftover: int) -> bool:
    """True when normalized text is a short social ritual with nothing else in it."""
    text = _ELONGATION_RE.sub(r"\1", norm.lower())
    tokens = _TOKEN_RE.findall(text)
    if not tokens or len(tokens) > max_tokens:
        return False
    if any(t in _IRONY_GUARD for t in tokens) or not _PHATIC_RE.search(text):
        return False
    leftover = [
        t
        for t in _TOKEN_RE.findall(_PHATIC_RE.sub(" ", text))
        if t not in _PHATIC_FILLER and not _LAUGH_RE.match(t)
    ]
    return len(leftover) <= max_leftover


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
    if filters.exclude_phatic and is_phatic(
        norm, max_tokens=filters.phatic_max_tokens, max_leftover=filters.phatic_max_leftover
    ):
        return "phatic", norm
    return None, norm


def _has_sarcastic_vote(db: AnnotationDatabase, reddit_fullname: str) -> bool:
    row = db.conn.execute(
        """
        SELECT 1 FROM llm_annotations
        WHERE reddit_fullname = ? AND role = 'annotator' AND sarcastic = 1 LIMIT 1
        """,
        (reddit_fullname,),
    ).fetchone()
    return row is not None


def select_candidates(
    db: AnnotationDatabase, filters: CandidateFiltersConfig
) -> dict[str, int]:
    """(Re)compute eligibility for every indexed record. Idempotent.

    A phatic item that an annotator already voted sarcastic is not dropped:
    it stays eligible and goes to the human low_confidence queue, so the
    filter can never silently delete a positive the ensemble found.
    """
    rows = db.conn.execute("SELECT * FROM corpus_index").fetchall()
    stats = {"eligible": 0, "excluded": 0, "phatic_to_review": 0}
    for row in rows:
        record = dict(row)
        reason, norm = _exclusion_reason(record, filters)
        if reason == "phatic" and _has_sarcastic_vote(db, record["reddit_fullname"]):
            reason = None
            db.enqueue_review(record["reddit_fullname"], "low_confidence")
            stats["phatic_to_review"] += 1
        db.upsert_candidate(
            record["reddit_fullname"],
            eligible=reason is None,
            exclusion_reason=reason,
            text_norm=norm,
        )
        stats["eligible" if reason is None else "excluded"] += 1
    db.commit()
    return stats
