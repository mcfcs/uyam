"""Schema additions reach databases created before them (idempotent migration)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from uyam.annotate.db import AnnotationDatabase


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_old_database_gains_new_columns_and_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db_path)
    # The 2026-09 shapes of the two tables that gained columns.
    conn.executescript(
        """
        CREATE TABLE candidates (
            reddit_fullname TEXT PRIMARY KEY, eligible INTEGER NOT NULL,
            exclusion_reason TEXT, text_norm TEXT, selected_at TEXT NOT NULL);
        CREATE TABLE human_reviews (
            reddit_fullname TEXT PRIMARY KEY, sarcastic INTEGER, language TEXT,
            literal_sentiment TEXT, intended_sentiment TEXT,
            cue_polarity_inversion INTEGER, cue_rhetorical_intent INTEGER,
            cue_contextual_incongruity INTEGER, cue_hyperbole INTEGER,
            notes TEXT, is_gold INTEGER DEFAULT 0, reviewed_at TEXT NOT NULL);
        INSERT INTO human_reviews (reddit_fullname, sarcastic, reviewed_at)
            VALUES ('t1_old', 1, '2026-09-10T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()

    for _ in range(2):  # opening twice must not fail (idempotent)
        AnnotationDatabase(db_path).close()

    assert "phatic" in _columns(db_path, "candidates")
    assert {"sarcastic_span", "unsure", "first_pass_json", "assist_seen"} <= _columns(
        db_path, "human_reviews"
    )
    assert "reddit_fullname" in _columns(db_path, "eval_annotations")
    assert "rationale" in _columns(db_path, "assist_suggestions")
    with AnnotationDatabase(db_path) as db:
        old = db.get_human_review("t1_old")
        assert old is not None and old["sarcastic"] == 1  # existing labels untouched
        assert old["unsure"] == 0


def test_new_columns_are_written(tmp_path: Path) -> None:
    with AnnotationDatabase(tmp_path / "a.sqlite3") as db:
        db.conn.execute(
            "INSERT INTO corpus_index (reddit_fullname, record_type, reddit_id, subreddit, "
            "text, indexed_at) VALUES ('t1_x', 'comment', 'x', 'CasualPH', 'hi', 'now')"
        )
        db.upsert_candidate(
            "t1_x", eligible=True, exclusion_reason=None, text_norm="hi", phatic=True
        )
        db.upsert_human_review(
            {
                "reddit_fullname": "t1_x",
                "sarcastic": 1,
                "sarcastic_span": "Ang galing mo naman",
                "unsure": 1,
                "first_pass_json": '{"sarcastic": 0}',
                "assist_seen": 1,
            }
        )
        cand = db.conn.execute("SELECT phatic FROM candidates").fetchone()
        assert cand["phatic"] == 1
        review = db.get_human_review("t1_x")
        assert review is not None
        assert (review["sarcastic_span"], review["unsure"], review["assist_seen"]) == (
            "Ang galing mo naman",
            1,
            1,
        )
