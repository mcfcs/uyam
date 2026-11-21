"""Load reviewer-assist suggestions into assist_suggestions.

Suggestions are produced outside the pipeline (Claude Code agents following
docs/GUIDELINE.md, blind to the ensemble votes and to any human label) as
JSON Lines, one object per item:

    {"reddit_fullname": "t1_...", "sarcastic": true|false,
     "sarcastic_span": "<exact sentence(s) of the target, joined by \\n>" | null,
     "rationale": "...", "confidence": 0.0-1.0}

or the full-label form (2026-11-22), which adds every field the reviewer sets,
so the review form can be prefilled with Claude's take:

    ... "language": "english"|"tagalog"|"taglish",
        "literal_sentiment": "positive"|"neutral"|"negative",
        "intended_sentiment": "positive"|"neutral"|"negative",
        "cues": {"polarity_inversion": bool, "rhetorical_intent": bool,
                 "contextual_incongruity": bool, "hyperbole": bool}

In the full form all cues must be false when sarcastic is false (GUIDELINE §3
step 7).

The review tab shows a suggestion only after the reviewer has saved a blind
first-pass label (review_workflow.revealable_assist), and nothing printed while
loading (counts, error messages) hints at any item's verdict. This module only
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
_FULL_KEYS = _KEYS | {"language", "literal_sentiment", "intended_sentiment", "cues"}
_LANGUAGES = {"english", "tagalog", "taglish"}
_SENTIMENTS = {"positive", "neutral", "negative"}
CUE_COLUMNS = {
    "polarity_inversion": "cue_polarity_inversion",
    "rhetorical_intent": "cue_rhetorical_intent",
    "contextual_incongruity": "cue_contextual_incongruity",
    "hyperbole": "cue_hyperbole",
}


class AssistValidationError(ValueError):
    """The suggestion files are malformed; nothing was written."""


def _validate(item: Any, where: str, text: str | None) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) not in (_KEYS, _FULL_KEYS):
        raise AssistValidationError(
            f"{where}: expected exactly the keys {sorted(_KEYS)}, or those plus "
            "language, literal_sentiment, intended_sentiment and cues"
        )
    if text is None:
        raise AssistValidationError(f"{where}: {item['reddit_fullname']!r} is not in the corpus")
    if not isinstance(item["sarcastic"], bool):
        raise AssistValidationError(f"{where}: sarcastic must be true or false")
    span = item["sarcastic_span"]
    if not item["sarcastic"] and span is not None:
        raise AssistValidationError(f"{where}: sarcastic and sarcastic_span disagree")
    if span is not None:
        if not isinstance(span, str) or not span.strip():
            raise AssistValidationError(f"{where}: sarcastic_span must be a non-empty string")
        for part in span.split("\n"):
            if part.strip() and part not in text:
                # No span text in the message: it would hint at the verdict.
                raise AssistValidationError(
                    f"{where}: a sarcastic_span part is not an exact substring of the target"
                )
    conf = item["confidence"]
    if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not 0.0 <= conf <= 1.0:
        raise AssistValidationError(f"{where}: confidence must be a number in [0, 1]")
    if not isinstance(item["rationale"], str) or not item["rationale"].strip():
        raise AssistValidationError(f"{where}: rationale must be a non-empty string")
    if "cues" in item:
        if item["language"] not in _LANGUAGES:
            raise AssistValidationError(f"{where}: language must be one of {sorted(_LANGUAGES)}")
        for field in ("literal_sentiment", "intended_sentiment"):
            if item[field] not in _SENTIMENTS:
                raise AssistValidationError(
                    f"{where}: {field} must be one of {sorted(_SENTIMENTS)}"
                )
        cues = item["cues"]
        if (
            not isinstance(cues, dict)
            or set(cues) != set(CUE_COLUMNS)
            or not all(isinstance(v, bool) for v in cues.values())
        ):
            raise AssistValidationError(
                f"{where}: cues must map exactly {sorted(CUE_COLUMNS)} to true/false"
            )
        if not item["sarcastic"] and any(cues.values()):
            # Generic wording: naming the verdict would hint at it (blind-first).
            raise AssistValidationError(f"{where}: sarcastic and cues disagree")
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
        cues = item.get("cues")
        db.conn.execute(
            """
            INSERT OR REPLACE INTO assist_suggestions
              (reddit_fullname, source, guideline_version, sarcastic, sarcastic_span,
               rationale, confidence, created_at, language, literal_sentiment,
               intended_sentiment, cue_polarity_inversion, cue_rhetorical_intent,
               cue_contextual_incongruity, cue_hyperbole)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                item.get("language"),
                item.get("literal_sentiment"),
                item.get("intended_sentiment"),
                *(
                    (int(cues[name]) for name in CUE_COLUMNS)
                    if isinstance(cues, dict)
                    else (None, None, None, None)
                ),
            ),
        )
    db.commit()
    return {
        "loaded": len(items),
        "sarcastic": sum(1 for i in items if i["sarcastic"]),
        "files": len(paths),
    }
