"""False-positive tagging: why the LLM label called a gold post sarcastic when Gio didn't.

For the qualitative false-positive analysis (consultation 2026-11-25, leische
reply 2026-11-27): Gio tags each gold post the v5 label flags as sarcastic but
he labeled not sarcastic, by guideline hard case. Tags live in
``data/fp_tags.json`` (gitignored; the posts are Reddit text). They never touch
the gold labels or the database: the database is only read.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uyam.progress_notes import load_gold

TAGS: dict[str, str] = {
    "H10": "H10 · plain complaint, insult, hate speech or slur (negative and literal)",
    "H14": "H14 · situational irony noted sincerely (irony, not sarcasm)",
    "D1": "D1 over-applied · hugot / self-pity / playful praise that is meant literally",
    "H4": "H4 · a joke or banter that means what it says",
    "H5": "H5 · a rhetorical or leading question, no pretended attitude",
    "H9": "H9 · praise with a twist / misfortune framed positively, but sincere",
    "H13": "H13 · would need the unseen image, video or link",
    "H11": "H11 · phatic (greeting, thanks, wishes)",
    "doubt": "On reflection I'd call it sarcastic or borderline (my label may be wrong)",
    "other": "Other (say why in the note)",
}


def false_positives(db: Path, prompt_version: str) -> list[str]:
    """Gold posts whose shipped label says sarcastic and Gio's first pass says not."""
    gold, _ = load_gold(db)
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        flagged = {
            str(r[0])
            for r in conn.execute(
                "SELECT reddit_fullname FROM aggregates WHERE prompt_version = ? "
                "AND sarcastic_final = 1",
                (prompt_version,),
            )
        }
    finally:
        conn.close()
    return sorted(f for f, g in gold.items() if f in flagged and g.sarcastic == 0)


def model_votes(db: Path, prompt_version: str, fullname: str) -> list[dict[str, Any]]:
    """Each annotator's vote, confidence and one-line rationale for one post."""
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute(
            "SELECT model_key, sarcastic, confidence, rationale FROM llm_annotations "
            "WHERE reddit_fullname = ? AND prompt_version = ? AND role = 'annotator' "
            "ORDER BY model_key",
            (fullname, prompt_version),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"model": str(k), "sarcastic": bool(s), "confidence": c, "rationale": r}
        for k, s, c, r in rows
    ]


def load_tags(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_tag(path: Path, fullname: str, tags: list[str], note: str) -> None:
    """Write one post's tags atomically (temp file + replace), keeping the others."""
    unknown = [t for t in tags if t not in TAGS]
    if unknown:
        raise ValueError(f"unknown tags: {unknown}")
    data = load_tags(path)
    data[fullname] = {
        "tags": sorted(set(tags), key=list(TAGS).index),
        "note": note.strip(),
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def summarize(
    items: list[str], tags: dict[str, dict[str, Any]], media: dict[str, bool | None]
) -> dict[str, Any]:
    """Counts per tag over the tagged false positives, plus the unseen-media share (from data)."""
    tagged = [f for f in items if (tags.get(f) or {}).get("tags")]
    counts = {key: sum(1 for f in tagged if key in tags[f]["tags"]) for key in TAGS}
    return {
        "false positives": len(items),
        "tagged": len(tagged),
        "per tag": counts,
        "replies to unseen media (data flag)": sum(1 for f in items if media.get(f) is False),
    }
