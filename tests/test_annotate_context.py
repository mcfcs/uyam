"""Thread-context builder: chain order, replies, truncation, persistence."""

from __future__ import annotations

from pathlib import Path

from tests.annotate_fixtures import make_config, write_raw_corpus
from uyam.annotate.candidates import select_candidates
from uyam.annotate.context import build_context, get_or_build_context
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase


def _db(tmp_path: Path) -> AnnotationDatabase:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    db = AnnotationDatabase(cfg.db_path)
    index_corpus(db, cfg.data_dir)
    select_candidates(db, cfg.candidate_filters)
    return db


def test_comment_context_has_submission_chain_and_replies(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    with _db(tmp_path) as db:
        record = db.get_record("t1_c2")
        assert record is not None
        block, snapshot = build_context(db, record, cfg.context)

        assert snapshot["submission"]["reddit_fullname"] == "t3_sub1"
        assert [p["reddit_fullname"] for p in snapshot["parent_chain"]] == ["t1_c1"]
        assert [r["reddit_fullname"] for r in snapshot["replies"]] == ["t1_c3"]
        assert "[SUBMISSION r/CasualPH]" in block
        assert "[PARENT depth=0]" in block
        assert "[REPLY 1]" in block
        # Only author hashes may appear in context snapshots.
        assert "real_name" not in block


def test_submission_context_lists_top_level_replies(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    with _db(tmp_path) as db:
        record = db.get_record("t3_sub1")
        assert record is not None
        block, snapshot = build_context(db, record, cfg.context)
        assert snapshot["submission"] is None
        assert snapshot["parent_chain"] == []
        assert len(snapshot["replies"]) == cfg.context.max_replies
        assert "the TARGET below is this submission" in block


def test_context_snapshot_is_persisted_and_reused(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    with _db(tmp_path) as db:
        record = db.get_record("t1_c3")
        assert record is not None
        block1, snap1 = get_or_build_context(db, record, cfg.context, cfg.prompt_version)
        # Corpus changes after snapshotting must not alter what annotators see.
        db.conn.execute(
            "UPDATE corpus_index SET text = 'EDITED LATER' WHERE reddit_fullname = 't1_c1'"
        )
        db.commit()
        block2, snap2 = get_or_build_context(db, record, cfg.context, cfg.prompt_version)
        assert block1 == block2
        assert snap1 == snap2


def test_context_respects_char_budget(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.context.max_context_chars = 120
    with _db(tmp_path) as db:
        record = db.get_record("t1_c3")
        assert record is not None
        block, _ = build_context(db, record, cfg.context)
        assert len(block) <= 121  # budget + ellipsis
