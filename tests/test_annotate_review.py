"""Gold-subset sampling: split votes (2-1 / 1-1) are over-represented on purpose."""

from __future__ import annotations

from pathlib import Path

from tests.annotate_fixtures import make_config, write_raw_corpus
from tests.test_annotate_aggregate import _ann
from uyam.annotate.aggregate import run_aggregation
from uyam.annotate.candidates import select_candidates
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.prompts import PROMPT_VERSION
from uyam.annotate.review import is_split_vote, sample_gold_subset


def test_is_split_vote() -> None:
    assert not is_split_vote("3-0")
    assert not is_split_vote("2-0")
    assert is_split_vote("2-1")
    assert is_split_vote("1-1")
    assert is_split_vote("1-0")
    assert not is_split_vote(None)
    assert not is_split_vote("garbage")


def _seed(tmp_path: Path):  # type: ignore[no-untyped-def]
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)
        # Three unanimous sarcastic comments...
        for fullname in ("t1_c1", "t1_c2", "t1_c3"):
            for key in ("a1", "a2", "a3"):
                db.insert_llm_annotation(_ann(fullname, key, sarcastic=1))
        # ...and three 2-1 splits decided sarcastic by the adjudicator.
        for fullname in ("t1_c4", "t1_c5", "t1_short1"):
            db.insert_llm_annotation(_ann(fullname, "a1", sarcastic=1))
            db.insert_llm_annotation(_ann(fullname, "a2", sarcastic=1))
            db.insert_llm_annotation(_ann(fullname, "a3", sarcastic=0))
            db.insert_llm_annotation(
                _ann(fullname, "adj", role="adjudicator", sarcastic=1, confidence=0.9)
            )
    run_aggregation(cfg, PROMPT_VERSION)
    with AnnotationDatabase(cfg.db_path) as db:
        votes = {
            str(r["reddit_fullname"]): (str(r["sarcasm_votes"]), r["resolved_by"])
            for r in db.conn.execute(
                "SELECT reddit_fullname, sarcasm_votes, resolved_by FROM aggregates"
            )
        }
    assert votes["t1_c1"] == ("3-0", "unanimous")
    assert votes["t1_c4"] == ("2-1", "adjudicator")
    return cfg


def test_gold_sample_oversamples_split_votes(tmp_path: Path) -> None:
    cfg = _seed(tmp_path)

    heavy = sample_gold_subset(cfg, PROMPT_VERSION, size=4, split_oversample=100.0)
    assert heavy["sampled"] == 4
    assert heavy["population"] == 6
    assert heavy["split_sampled"] == 3  # every split item, plus one unanimous

    with AnnotationDatabase(cfg.db_path) as db:
        queued = {str(r["reddit_fullname"]) for r in db.review_queue_items()}
        db.conn.execute("DELETE FROM review_queue")
        db.commit()
    assert {"t1_c4", "t1_c5", "t1_short1"} <= queued

    flat = sample_gold_subset(cfg, PROMPT_VERSION, size=4, split_oversample=1.0)
    assert flat["sampled"] == 4
    assert flat["split_sampled"] == 2  # proportional: half and half


def test_gold_sample_default_weight_comes_from_config(tmp_path: Path) -> None:
    cfg = _seed(tmp_path)
    assert cfg.review.gold_split_oversample == 2.0
    stats = sample_gold_subset(cfg, PROMPT_VERSION, size=6)
    assert stats["sampled"] == 6
    assert stats["split_sampled"] == 3
    assert stats["strata"] == 2
