"""Corpus indexing and candidate selection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.annotate_fixtures import _comment, make_config, write_raw_corpus
from uyam.annotate.candidates import is_phatic, normalize_text, select_candidates
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


@pytest.mark.parametrize(
    "text",
    [
        "Happy birthday, OP! 🥳",
        "happyyy birthdayyy op!",
        "Congrats, OP! Nice work!",
        "Thank you so much po!!! 💗",
        "Maraming salamat po!",
        "God bless you po! <3",
        "Merry christmas OP! Hahaha",
        "Good morning guysss",
    ],
)
def test_is_phatic_catches_social_ritual(text: str) -> None:
    assert is_phatic(normalize_text(text), max_tokens=10, max_leftover=2)


@pytest.mark.parametrize(
    "text",
    [
        "Edi congrats sa inyo.",  # irony marker vetoes the rule
        "Thanks for nothing, OP.",
        "Wow ang galing naman, thank you ha",
        "Ikaw na talaga, happy birthday",
        "Welcome to the club, mga sawi",  # "welcome to" is an idiom, not a greeting
        "Sakto lapit lang sa work ko, matry nga dyan. Thanks OP!",  # real content
        "Happy birthday sa nanay ko, sana gumaling na siya sa ospital",
    ],
)
def test_is_phatic_keeps_content_and_irony(text: str) -> None:
    assert not is_phatic(normalize_text(text), max_tokens=10, max_leftover=2)


def _add_phatic_records(cfg: Any) -> None:
    rows = [
        _phatic_comment("hbd1", "Happy birthday, OP! 🥳🎂"),
        _phatic_comment("hbd2", "Happy birthday OP, you deserve the world!"),
    ]
    out = cfg.data_dir / "raw" / "CasualPH" / "2026-08-21.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _phatic_comment(rid: str, body: str) -> dict[str, Any]:
    return _comment(rid, "sub1", "t3_sub1", 0, body=body, created_utc="2026-08-21T09:00:00Z")


def _vote_sarcastic(db: AnnotationDatabase, fullname: str) -> None:
    db.conn.execute(
        """
        INSERT INTO llm_annotations
          (reddit_fullname, model_key, prompt_version, role, sarcastic, created_at)
        VALUES (?, 'a1', 'test', 'annotator', 1, '2026-08-21T00:00:00Z')
        """,
        (fullname,),
    )


def test_phatic_items_excluded_unless_already_voted_sarcastic(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    _add_phatic_records(cfg)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        _vote_sarcastic(db, "t1_hbd2")
        stats = select_candidates(db, cfg.candidate_filters)

        reasons = {
            str(r["reddit_fullname"]): r["exclusion_reason"]
            for r in db.conn.execute("SELECT reddit_fullname, exclusion_reason FROM candidates")
        }
        assert reasons["t1_hbd1"] == "phatic"
        assert db.candidate_counts()["phatic"] == 1
        # The ensemble called hbd2 sarcastic: a human decides, the filter does not.
        assert reasons["t1_hbd2"] is None
        assert stats["phatic_to_review"] == 1
        queued = {q["reddit_fullname"]: q["reason"] for q in db.review_queue_items()}
        assert queued == {"t1_hbd2": "low_confidence"}
        # Ordinary eligibility is untouched.
        assert reasons["t1_c3"] is None


def test_phatic_filter_can_be_disabled(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.candidate_filters.exclude_phatic = False
    write_raw_corpus(cfg.data_dir)
    _add_phatic_records(cfg)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)
        assert "phatic" not in db.candidate_counts()
