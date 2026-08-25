"""Thread-context builder: what an annotator sees alongside the target.

For a comment target: the submission (title + truncated selftext), the parent
comment chain (nearest ancestors kept fullest), and up to N direct replies to
the target (reception often reveals sarcasm).
For a submission target: the submission IS the target; context is its first
top-level replies.

The rendered block AND a structured JSON snapshot are persisted per item so
the export ships exactly what the annotators saw, even if the corpus grows.
"""

from __future__ import annotations

from typing import Any

from uyam.annotate.config import ContextConfig
from uyam.annotate.db import AnnotationDatabase

_SELFTEXT_HEAD = 1200
_SELFTEXT_TAIL = 300
_PARENT_NEAR_CHARS = 500  # the two ancestors closest to the target
_PARENT_FAR_CHARS = 300
_REPLY_CHARS = 200


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _truncate_selftext(text: str) -> str:
    text = text.strip()
    if len(text) <= _SELFTEXT_HEAD + _SELFTEXT_TAIL:
        return text
    return (
        text[:_SELFTEXT_HEAD].rstrip()
        + "\n[…]\n"
        + text[-_SELFTEXT_TAIL:].lstrip()
    )


def _entry(record: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "reddit_fullname": record["reddit_fullname"],
        "author_hash": record.get("author_hash"),
        "is_submitter": record.get("is_submitter"),
        "depth": record.get("depth"),
        "created_utc": record.get("created_utc"),
        "text": text,
    }


def build_context(
    db: AnnotationDatabase, record: dict[str, Any], cfg: ContextConfig
) -> tuple[str, dict[str, Any]]:
    """Return (rendered context block, structured context snapshot)."""
    lines: list[str] = ["=== THREAD CONTEXT ==="]
    snapshot: dict[str, Any] = {"submission": None, "parent_chain": [], "replies": []}
    fullname = str(record["reddit_fullname"])
    subreddit = record.get("subreddit") or ""

    if record["record_type"] == "comment":
        submission = (
            db.get_record(str(record["submission_fullname"]))
            if record.get("submission_fullname")
            else None
        )
        if submission is not None:
            selftext = _truncate_selftext(str(submission.get("selftext") or ""))
            lines.append(f"[SUBMISSION r/{subreddit}] {submission.get('title') or ''}")
            if selftext:
                lines.append(selftext)
            snapshot["submission"] = {
                "reddit_fullname": submission["reddit_fullname"],
                "author_hash": submission.get("author_hash"),
                "created_utc": submission.get("created_utc"),
                "title": submission.get("title"),
                "selftext": selftext,
            }
        else:
            lines.append(f"[SUBMISSION r/{subreddit}] (submission not collected)")

        chain = db.parent_chain(fullname)
        if len(chain) > cfg.max_parent_chain:
            chain = chain[-cfg.max_parent_chain :]
        for i, parent in enumerate(chain):
            near = i >= len(chain) - 2
            text = _truncate(
                str(parent.get("text") or ""),
                _PARENT_NEAR_CHARS if near else _PARENT_FAR_CHARS,
            )
            marker = " (OP)" if parent.get("is_submitter") else ""
            lines.append(f"[PARENT depth={parent.get('depth')}{marker}] {text}")
            snapshot["parent_chain"].append(_entry(parent, text))
    else:
        # Submission target: title/selftext live in the TARGET block, not here.
        lines.append(f"[SUBMISSION r/{subreddit}] (the TARGET below is this submission)")

    replies = db.children(fullname)[: cfg.max_replies]
    for i, reply in enumerate(replies, start=1):
        text = _truncate(str(reply.get("text") or ""), _REPLY_CHARS)
        marker = " (OP)" if reply.get("is_submitter") else ""
        lines.append(f"[REPLY {i}{marker}] {text}")
        snapshot["replies"].append(_entry(reply, text))

    block = "\n".join(lines)
    if len(block) > cfg.max_context_chars:
        block = block[: cfg.max_context_chars].rstrip() + "…"
    return block, snapshot


def get_or_build_context(
    db: AnnotationDatabase,
    record: dict[str, Any],
    cfg: ContextConfig,
    prompt_version: str,
) -> tuple[str, dict[str, Any]]:
    """Load the persisted context snapshot, or build and persist it.

    The snapshot is keyed per item: every annotator (and the adjudicator) must
    see the identical context, and the export must ship it verbatim.
    """
    fullname = str(record["reddit_fullname"])
    existing = db.get_context(fullname)
    if existing is not None and existing["prompt_version"] == prompt_version:
        import json

        return str(existing["context_text"]), json.loads(str(existing["context_json"]))
    block, snapshot = build_context(db, record, cfg)
    db.save_context(fullname, prompt_version, snapshot, block)
    db.commit()
    return block, snapshot
