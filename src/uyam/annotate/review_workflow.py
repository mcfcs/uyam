"""Blind-first review workflow helpers for the "Annotation Review" tab.

Kept free of Streamlit so the rules are unit-testable:

- ``split_sentences``: the sentence splitter behind the sarcastic-span picker
- ``build_review_row``: the human_reviews row for a save, which keeps the
  ORIGINAL first-pass labels and the assist_seen flag on every re-save
- ``revealable_assist`` / ``reveal_assist``: the only way the UI reads
  assist_suggestions, and both refuse until a first-pass label is saved
- ``highlight_spans``: marks an assist span inside the target text

first_pass_json shape (flat, keys = human_reviews column names)::

    {"sarcastic": 0|1, "language": str, "literal_sentiment": str,
     "intended_sentiment": str, "cue_polarity_inversion": 0|1,
     "cue_rhetorical_intent": 0|1, "cue_contextual_incongruity": 0|1,
     "cue_hyperbole": 0|1, "sarcastic_span": str|null, "unsure": 0|1}
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from uyam.annotate.db import AnnotationDatabase

# The labels a reviewer decides; also the keys of first_pass_json, in order.
LABEL_KEYS: tuple[str, ...] = (
    "sarcastic",
    "language",
    "literal_sentiment",
    "intended_sentiment",
    "cue_polarity_inversion",
    "cue_rhetorical_intent",
    "cue_contextual_incongruity",
    "cue_hyperbole",
    "sarcastic_span",
    "unsure",
)

# A sentence ends at a run of . ! ? or … (optionally followed by one closing
# quote/bracket) that is followed by whitespace. Two fixed-width lookbehinds
# because Python's re has no variable-width lookbehind.
_SENTENCE_BREAK = re.compile(r"(?:(?<=[.!?…])|(?<=[.!?…][\"'”’)\]]))\s+")


def split_sentences(text: str) -> list[str]:
    """Split a target into sentences for the span picker.

    Rule: every line break ends a sentence, and inside a line a run of
    ``. ! ? …`` (plus an optional closing quote/bracket) followed by
    whitespace ends one. Pieces are stripped and empty pieces dropped, so
    each returned sentence is an exact substring of ``text``.

    Deliberately simple: informal Taglish often has no punctuation at all, so
    a whole comment coming back as one "sentence" is expected; abbreviations
    like "Dr. Cruz" over-split, which only adds a checkbox.
    """
    sentences: list[str] = []
    for line in text.splitlines():
        for piece in _SENTENCE_BREAK.split(line):
            piece = piece.strip()
            if piece:
                sentences.append(piece)
    return sentences


def span_from_selection(sarcastic: bool, sentences: list[str], ticked: list[bool]) -> str | None:
    """The stored sarcastic_span: ticked sentences joined by "\\n"; NULL unless sarcastic."""
    if not sarcastic:
        return None
    chosen = [s for s, on in zip(sentences, ticked, strict=True) if on]
    return "\n".join(chosen) if chosen else None


def labels_json(labels: dict[str, Any]) -> str:
    """Serialize the reviewer's labels as stored in first_pass_json."""
    return json.dumps({k: labels.get(k) for k in LABEL_KEYS}, ensure_ascii=False)


def build_review_row(
    fullname: str,
    labels: dict[str, Any],
    *,
    notes: str | None,
    is_gold: bool,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    """The human_reviews row to upsert for a save of ``labels``.

    upsert_human_review is INSERT OR REPLACE, so this carries the first-pass
    snapshot and assist_seen forward:
    - no existing row: this IS the first pass -> first_pass_json = labels
    - existing row with first_pass_json: keep it byte-for-byte
    - existing legacy row without it (labeled before this column existed):
      its stored labels were the blind first pass, so snapshot those, never
      the revised ones
    """
    if existing is None:
        first_pass = labels_json(labels)
        assist_seen = 0
    else:
        first_pass = existing.get("first_pass_json") or labels_json(existing)
        assist_seen = int(existing.get("assist_seen") or 0)
    return {
        "reddit_fullname": fullname,
        **{k: labels.get(k) for k in LABEL_KEYS},
        "notes": notes or None,
        "is_gold": int(is_gold),
        "first_pass_json": first_pass,
        "assist_seen": assist_seen,
    }


def revealable_assist(db: AnnotationDatabase, fullname: str) -> dict[str, Any] | None:
    """The assist suggestion for ``fullname``, or None.

    Blind-first gate: returns None while no human label is saved for the item,
    whether or not a suggestion exists, so nothing can leak before the first
    pass. The review tab reads assist_suggestions only through this function.
    """
    if db.get_human_review(fullname) is None:
        return None
    row = db.conn.execute(
        "SELECT * FROM assist_suggestions WHERE reddit_fullname = ?", (fullname,)
    ).fetchone()
    return dict(row) if row else None


def reveal_assist(db: AnnotationDatabase, fullname: str) -> dict[str, Any] | None:
    """Record that the reviewer revealed the suggestion, then return it.

    Persists assist_seen = 1 immediately (and, for a legacy row, snapshots its
    blind labels into first_pass_json first). Refuses (returns None, writes
    nothing) before a first-pass label exists or when there is no suggestion.
    """
    assist = revealable_assist(db, fullname)
    existing = db.get_human_review(fullname)
    if assist is None or existing is None:
        return None
    db.conn.execute(
        "UPDATE human_reviews SET assist_seen = 1, "
        "first_pass_json = COALESCE(first_pass_json, ?) WHERE reddit_fullname = ?",
        (labels_json(existing), fullname),
    )
    db.commit()
    return assist


def highlight_spans(text: str, span: str | None) -> tuple[str, list[str]]:
    """HTML of ``text`` with each "\\n"-separated part of ``span`` wrapped in <mark>.

    Everything is HTML-escaped. Returns (html, parts that are not an exact
    substring of the text) so the caller can show those separately.
    """
    parts = [p for p in (span or "").split("\n") if p.strip()]
    intervals: list[tuple[int, int]] = []
    missing: list[str] = []
    for part in parts:
        start = text.find(part)
        if start < 0:
            missing.append(part)
        else:
            intervals.append((start, start + len(part)))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    out: list[str] = []
    pos = 0
    for start, end in merged:
        out.append(html.escape(text[pos:start]))
        out.append(f"<mark>{html.escape(text[start:end])}</mark>")
        pos = end
    out.append(html.escape(text[pos:]))
    return "".join(out).replace("\n", "<br>"), missing
