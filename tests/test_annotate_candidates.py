"""Corpus indexing and candidate selection."""

from __future__ import annotations

from pathlib import Path

from tests.annotate_fixtures import make_config, write_raw_corpus
from uyam.annotate.candidates import normalize_text, select_candidates
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase


def _build(tmp_path: Path) -> AnnotationDatabase:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    db = AnnotationDatabase(cfg.db_path)
    index_corpus(db, cfg.data_dir)
    select_candidates(db, cfg.candidate_filters)
    return db


def test_index_builds_thread_structure(tmp_path: Path) -> None:
    with _build(tmp_path) as db:
        counts = db.corpus_counts()
        assert counts["submissions"] == 2
        assert counts["comments"] == 9

        c3 = db.get_record("t1_c3")
        assert c3 is not None
        assert c3["submission_fullname"] == "t3_sub1"
        assert c3["parent_fullname"] == "t1_c2"

        chain = db.parent_chain("t1_c3")
        assert [r["reddit_fullname"] for r in chain] == ["t1_c1", "t1_c2"]

        replies = db.children("t1_c1")
        assert [r["reddit_fullname"] for r in replies] == ["t1_c2", "t1_c4", "t1_c5"]


def test_index_never_stores_raw_author(tmp_path: Path) -> None:
    with _build(tmp_path) as db:
        columns = {row[1] for row in db.conn.execute("PRAGMA table_info(corpus_index)")}
        assert "author" not in columns
        assert "author_hash" in columns


def test_candidate_filters(tmp_path: Path) -> None:
    with _build(tmp_path) as db:
        reasons = {
            str(r["reddit_fullname"]): r["exclusion_reason"]
            for r in db.conn.execute("SELECT reddit_fullname, exclusion_reason FROM candidates")
        }
        assert reasons["t3_sub1"] is None
        assert reasons["t1_c1"] is None
        assert reasons["t1_c3"] is None
        assert reasons["t1_bot1"] == "bot_author"
        assert reasons["t1_short1"] == "too_short"
        assert reasons["t1_del1"] == "deleted_author"
        assert reasons["t1_url1"] == "too_short"  # URL stripped before length check
        assert reasons["t3_link1"] == "link_post"

        eligible = set(db.eligible_fullnames())
        assert "t1_bot1" not in eligible
        assert "t1_c1" in eligible


def test_normalize_text_strips_urls_and_markdown() -> None:
    assert normalize_text("check https://x.com/a **bold** [link](https://y.com)") == (
        "check bold link"
    )
