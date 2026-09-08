"""Corpus indexer: raw JSONL -> corpus_index table.

Bot accounts are recognised by the HMAC digest of their username, so the raw
JSONL never needs a plaintext author (files written before the scrub still
carry one; it is honoured but never stored downstream).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from uyam.annotate.db import AnnotationDatabase
from uyam.storage import record_identity

logger = logging.getLogger(__name__)

_BOT_AUTHORS = {"automoderator"}


def bot_author_hashes(bot_authors: set[str]) -> set[str]:
    """HMAC digests of the bot usernames (empty when no AUTHOR_HMAC_KEY is set)."""
    from uyam.config import load_env
    from uyam.privacy import pseudonymize_author

    load_env()
    hashes: set[str] = set()
    for name in bot_authors:
        try:
            digest, _ = pseudonymize_author(name)
        except RuntimeError:
            logger.warning("bot_hashes_unavailable", extra={"reason": "AUTHOR_HMAC_KEY missing"})
            return set()
        if digest:
            hashes.add(digest)
    return hashes


def _corpus_row(
    record: dict[str, Any],
    jsonl_path: str,
    bot_authors: set[str],
    bot_hashes: set[str] | None = None,
) -> dict[str, Any] | None:
    record_type = record.get("record_type")
    fullname = record_identity(record)
    if not fullname or record_type not in ("submission", "comment"):
        return None

    author = record.get("author")  # legacy files only; new records never carry it
    author_hash = record.get("author_hash")
    is_bot = (isinstance(author, str) and author.lower() in bot_authors) or (
        isinstance(author_hash, str) and author_hash in (bot_hashes or set())
    )

    if record_type == "submission":
        title = str(record.get("title") or "")
        selftext = str(record.get("selftext") or "")
        text = f"{title}\n\n{selftext}".strip()
        submission_fullname = fullname
        parent_fullname = None
        depth = None
        body = None
        is_self = record.get("is_self")
        is_submitter = None
    else:
        title = None
        selftext = None
        body = str(record.get("body") or "")
        text = body
        submission_id = str(record.get("submission_id") or "")
        submission_fullname = f"t3_{submission_id}" if submission_id else None
        parent_fullname = record.get("parent_id")
        depth = record.get("depth")
        is_self = None
        is_submitter = record.get("is_submitter")

    return {
        "reddit_fullname": fullname,
        "record_type": record_type,
        "reddit_id": str(record.get("reddit_id") or record.get("id") or ""),
        "subreddit": str(record.get("subreddit") or ""),
        "submission_fullname": submission_fullname,
        "parent_fullname": parent_fullname,
        "depth": depth,
        "created_utc": record.get("created_utc"),
        "author_hash": record.get("author_hash"),
        "author_status": record.get("author_status"),
        "is_submitter": None if is_submitter is None else int(bool(is_submitter)),
        "is_bot": int(is_bot),
        "distinguished": record.get("distinguished"),
        "stickied": int(bool(record.get("stickied"))),
        "is_self": None if is_self is None else int(bool(is_self)),
        "score": record.get("score"),
        "sampling_strategy": record.get("sampling_strategy"),
        "matched_query_or_keyword": record.get("matched_query_or_keyword"),
        "title": title,
        "selftext": selftext,
        "body": body,
        "text": text,
        "permalink": record.get("permalink"),
        "jsonl_path": jsonl_path,
    }


def index_corpus(
    db: AnnotationDatabase,
    data_dir: Path,
    *,
    extra_bot_authors: list[str] | None = None,
) -> dict[str, int]:
    """Index every JSONL record under data/raw into corpus_index (idempotent)."""
    bot_authors = _BOT_AUTHORS | {a.lower() for a in (extra_bot_authors or [])}
    bot_hashes = bot_author_hashes(bot_authors)
    raw_dir = data_dir / "raw"
    stats = {"files": 0, "records": 0, "skipped": 0}
    seen: set[str] = set()

    if not raw_dir.exists():
        logger.warning("raw_dir_missing", extra={"path": str(raw_dir)})
        return stats

    for filepath in sorted(raw_dir.glob("**/*.jsonl")):
        try:
            text = filepath.read_text(encoding="utf-8")
        except OSError:
            continue
        stats["files"] += 1
        rel_path = str(filepath.relative_to(data_dir))
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                stats["skipped"] += 1
                continue
            if not isinstance(parsed, dict):
                stats["skipped"] += 1
                continue
            row = _corpus_row(parsed, rel_path, bot_authors, bot_hashes)
            if row is None:
                stats["skipped"] += 1
                continue
            if row["reddit_fullname"] in seen:
                continue
            seen.add(row["reddit_fullname"])
            db.upsert_corpus_record(row)
            stats["records"] += 1
    # Comments written before 2026-09-08 carry no sampling_strategy: inherit
    # the parent submission's tag so keyword-oversampled threads stay marked.
    cur = db.conn.execute(
        """
        UPDATE corpus_index SET
            sampling_strategy = (SELECT s.sampling_strategy FROM corpus_index s
                                 WHERE s.reddit_fullname = corpus_index.submission_fullname),
            matched_query_or_keyword = (SELECT s.matched_query_or_keyword FROM corpus_index s
                                        WHERE s.reddit_fullname = corpus_index.submission_fullname)
        WHERE record_type = 'comment' AND sampling_strategy IS NULL
          AND submission_fullname IN
              (SELECT reddit_fullname FROM corpus_index WHERE record_type = 'submission')
        """
    )
    stats["comments_inherited_sampling"] = int(cur.rowcount or 0)
    db.commit()
    return stats
