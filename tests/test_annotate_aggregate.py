"""Aggregation rules: unanimity, majority, escalation, adjudicator, human."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.annotate_fixtures import make_config
from uyam.annotate.aggregate import compute_agreement, run_aggregation
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.prompts import PROMPT_VERSION


def _ann(
    fullname: str,
    model_key: str,
    *,
    role: str = "annotator",
    sarcastic: int = 0,
    language: str = "taglish",
    literal: str = "neutral",
    intended: str = "neutral",
    confidence: float = 0.9,
) -> dict[str, Any]:
    return {
        "reddit_fullname": fullname,
        "model_key": model_key,
        "prompt_version": PROMPT_VERSION,
        "role": role,
        "language": language,
        "literal_sentiment": literal,
        "intended_sentiment": intended,
        "sarcastic": sarcastic,
        "cue_polarity_inversion": sarcastic,
        "cue_rhetorical_intent": 0,
        "cue_contextual_incongruity": 0,
        "cue_hyperbole": 0,
        "confidence": confidence,
        "rationale": "r",
        "raw_json": "{}",
        "attempts": 1,
    }


def _seed(db: AnnotationDatabase) -> None:
    # itemU: unanimous sarcastic
    for key in ("a1", "a2", "a3"):
        db.insert_llm_annotation(
            _ann("t1_itemU", key, sarcastic=1, literal="positive", intended="negative")
        )
    # itemM: unanimous sarcasm, 2-1 language -> majority
    db.insert_llm_annotation(_ann("t1_itemM", "a1", language="taglish"))
    db.insert_llm_annotation(_ann("t1_itemM", "a2", language="taglish"))
    db.insert_llm_annotation(_ann("t1_itemM", "a3", language="english"))
    # itemE: 2-1 sarcasm split -> escalated, no adjudicator yet
    db.insert_llm_annotation(_ann("t1_itemE", "a1", sarcastic=1))
    db.insert_llm_annotation(_ann("t1_itemE", "a2", sarcastic=1))
    db.insert_llm_annotation(_ann("t1_itemE", "a3", sarcastic=0))
    # itemA: 2-1 split + low-confidence adjudicator -> adjudicated + human queue
    db.insert_llm_annotation(_ann("t1_itemA", "a1", sarcastic=1))
    db.insert_llm_annotation(_ann("t1_itemA", "a2", sarcastic=0))
    db.insert_llm_annotation(_ann("t1_itemA", "a3", sarcastic=0))
    db.insert_llm_annotation(
        _ann(
            "t1_itemA",
            "adj",
            role="adjudicator",
            sarcastic=1,
            language="tagalog",
            literal="positive",
            intended="negative",
            confidence=0.5,
        )
    )
    # itemT: three-way language tie -> escalated
    db.insert_llm_annotation(_ann("t1_itemT", "a1", language="english"))
    db.insert_llm_annotation(_ann("t1_itemT", "a2", language="tagalog"))
    db.insert_llm_annotation(_ann("t1_itemT", "a3", language="taglish"))
    # itemH: escalated but human already reviewed (non-gold) -> human final
    db.insert_llm_annotation(_ann("t1_itemH", "a1", sarcastic=1))
    db.insert_llm_annotation(_ann("t1_itemH", "a2", sarcastic=0))
    db.insert_llm_annotation(_ann("t1_itemH", "a3", sarcastic=1))
    db.upsert_human_review(
        {
            "reddit_fullname": "t1_itemH",
            "sarcastic": 0,
            "language": "tagalog",
            "literal_sentiment": "negative",
            "intended_sentiment": "negative",
            "cue_polarity_inversion": 0,
            "cue_rhetorical_intent": 0,
            "cue_contextual_incongruity": 0,
            "cue_hyperbole": 0,
            "notes": None,
            "is_gold": 0,
        }
    )


def test_aggregation_rules(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        _seed(db)

    stats = run_aggregation(cfg, PROMPT_VERSION)
    assert stats["items"] == 6

    with AnnotationDatabase(cfg.db_path) as db:
        agg_u = db.get_aggregate("t1_itemU")
        assert agg_u is not None
        assert agg_u["resolved_by"] == "unanimous"
        assert agg_u["sarcastic_final"] == 1
        assert agg_u["sarcasm_votes"] == "3-0"
        assert agg_u["intended_final"] == "negative"
        assert agg_u["cue_polarity_inversion_final"] == 1

        agg_m = db.get_aggregate("t1_itemM")
        assert agg_m is not None
        assert agg_m["resolved_by"] == "majority"
        assert agg_m["language_final"] == "taglish"
        assert agg_m["needs_adjudication"] == 0

        agg_e = db.get_aggregate("t1_itemE")
        assert agg_e is not None
        assert agg_e["needs_adjudication"] == 1
        assert agg_e["resolved_by"] is None
        assert agg_e["sarcasm_votes"] == "2-1"

        agg_a = db.get_aggregate("t1_itemA")
        assert agg_a is not None
        assert agg_a["resolved_by"] == "adjudicator"
        assert agg_a["sarcastic_final"] == 1  # adjudicator overrides the 2-0 annotator lean
        assert agg_a["language_final"] == "tagalog"
        assert agg_a["needs_human"] == 1  # confidence 0.5 < floor 0.6
        queued = {q["reddit_fullname"] for q in db.review_queue_items()}
        assert "t1_itemA" in queued

        agg_t = db.get_aggregate("t1_itemT")
        assert agg_t is not None
        assert agg_t["needs_adjudication"] == 1
        assert agg_t["language_final"] is None

        agg_h = db.get_aggregate("t1_itemH")
        assert agg_h is not None
        assert agg_h["resolved_by"] == "human"
        assert agg_h["sarcastic_final"] == 0
        assert agg_h["language_final"] == "tagalog"

        # Adjudicator queue = escalated items without an adjudicator row,
        # excluding anything a human already settled.
        pending_adj = db.pending_adjudicator_items("adj", PROMPT_VERSION)
        assert set(pending_adj) == {"t1_itemE", "t1_itemT"}


def test_aggregation_is_idempotent(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        _seed(db)
    first = run_aggregation(cfg, PROMPT_VERSION)
    second = run_aggregation(cfg, PROMPT_VERSION)
    assert first["items"] == second["items"]


def test_compute_agreement_shape(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        _seed(db)
    run_aggregation(cfg, PROMPT_VERSION)
    report = compute_agreement(cfg, PROMPT_VERSION)
    assert set(report["labels"]) == {
        "sarcastic",
        "language",
        "literal_sentiment",
        "intended_sentiment",
    }
    assert report["labels"]["sarcastic"]["raters"] == ["a1", "a2", "a3"]
    assert report["labels"]["sarcastic"]["n_items_all_raters"] == 6
