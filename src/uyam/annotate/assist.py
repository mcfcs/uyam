"""Load reviewer-assist suggestions into assist_suggestions.

Suggestions are produced outside the pipeline (Claude Code agents following
docs/GUIDELINE.md, blind to the ensemble votes and to any human label) as
JSON Lines, one object per item:

    {"reddit_fullname": "t1_...", "sarcastic": true|false,
     "sarcastic_span": "<exact sentence(s) of the target, joined by \\n>" | null,
     "rationale": "...", "confidence": 0.0-1.0}

The review tab shows a suggestion only after the reviewer has saved a blind
first-pass label (review_workflow.revealable_assist). This module only
validates and stores; it never touches labels, votes, or the review queue.
The whole file set is validated before anything is written.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uyam.annotate.db import AnnotationDatabase

_KEYS = {"reddit_fullname", "sarcastic", "sarcastic_span", "rationale", "confidence"}


class AssistValidationError(ValueError):
    """The suggestion files are malformed; nothing was written."""


def _validate(item: Any, where: str, text: str | None) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) != _KEYS:
        raise AssistValidationError(f"{where}: expected exactly the keys {sorted(_KEYS)}")
    if text is None:
        raise AssistValidationError(f"{where}: {item['reddit_fullname']!r} is not in the corpus")
    if not isinstance(item["sarcastic"], bool):
        raise AssistValidationError(f"{where}: sarcastic must be true or false")
    span = item["sarcastic_span"]
    if not item["sarcastic"] and span is not None:
        raise AssistValidationError(f"{where}: a span is given but sarcastic is false")
    if span is not None:
        if not isinstance(span, str) or not span.strip():
            raise AssistValidationError(f"{where}: sarcastic_span must be a non-empty string")
        for part in span.split("\n"):
            if part.strip() and part not in text:
                raise AssistValidationError(
                    f"{where}: span part is not an exact substring of the target: {part[:60]!r}"
                )
    conf = item["confidence"]
    if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not 0.0 <= conf <= 1.0:
        raise AssistValidationError(f"{where}: confidence must be a number in [0, 1]")
    if not isinstance(item["rationale"], str) or not item["rationale"].strip():
        raise AssistValidationError(f"{where}: rationale must be a non-empty string")
    return item


def load_assist_suggestions(
    db: AnnotationDatabase,
    paths: list[Path],
    *,
    source: str,
    guideline_version: str,
) -> dict[str, int]:
    """Validate every file, then upsert all suggestions in one transaction."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            where = f"{path.name}:{lineno}"
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssistValidationError(f"{where}: invalid JSON ({exc})") from exc
            fullname = item.get("reddit_fullname") if isinstance(item, dict) else None
            if not isinstance(fullname, str):
                raise AssistValidationError(f"{where}: missing reddit_fullname")
            if fullname in seen:
                raise AssistValidationError(f"{where}: {fullname} appears twice")
            seen.add(fullname)
            record = db.get_record(fullname)
            items.append(_validate(item, where, record["text"] if record else None))

    now = datetime.now(UTC).isoformat()
    for item in items:
        db.conn.execute(
            """
            INSERT OR REPLACE INTO assist_suggestions
              (reddit_fullname, source, guideline_version, sarcastic, sarcastic_span,
               rationale, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["reddit_fullname"],
                source,
                guideline_version,
                int(item["sarcastic"]),
                item["sarcastic_span"],
                item["rationale"],
                float(item["confidence"]),
                now,
            ),
        )
    db.commit()
    return {
        "loaded": len(items),
        "sarcastic": sum(1 for i in items if i["sarcastic"]),
        "files": len(paths),
    }
