"""Export fields added for the leische handoff: per-label resolution, context
source, text-only flag, LID alias, and the card's readiness / window / temporal
coverage blocks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.annotate_fixtures import make_config
from tests.test_annotate_export import _run_pipeline
from uyam.annotate.aggregate import per_label_resolution
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.export import is_text_only, run_export, temporal_context_coverage
from uyam.annotate.prompts import PROMPT_VERSION


def _ann(sarc: int, lang: str, lit: str, intd: str) -> dict[str, Any]:
    return {
        "sarcastic": sarc,
        "language": lang,
        "literal_sentiment": lit,
        "intended_sentiment": intd,
    }


def test_per_label_resolution_rules() -> None:
    rows = [
        _ann(1, "taglish", "neutral", "negative"),
        _ann(1, "taglish", "neutral", "negative"),
        _ann(0, "english", "neutral", "negative"),
    ]
    assert per_label_resolution(rows, [], None) == {
        "sarcastic": "unresolved",  # 2-1 split escalates
        "language": "majority",
        "literal_sentiment": "unanimous",
        "intended_sentiment": "unanimous",
    }
    adjudicated = per_label_resolution(rows, [_ann(1, "taglish", "neutral", "negative")], None)
    assert set(adjudicated.values()) == {"adjudicator"}
    human = per_label_resolution(rows, [], {"is_gold": 0, "sarcastic": 1})
    assert set(human.values()) == {"human"}
    # Gold labels never decide anything.
    assert per_label_resolution(rows, [], {"is_gold": 1})["sarcastic"] == "unresolved"
    three_way = [
        _ann(0, "english", "neutral", "neutral"),
        _ann(0, "tagalog", "neutral", "neutral"),
        _ann(0, "taglish", "neutral", "neutral"),
    ]
    assert per_label_resolution(three_way, [], None)["language"] == "unresolved"
    assert per_label_resolution(three_way, [], None)["sarcastic"] == "unanimous"


def test_is_text_only_rule() -> None:
    assert is_text_only({"record_type": "comment"})
    assert is_text_only({"record_type": "submission", "is_self": 1, "selftext": ""})
    assert is_text_only({"record_type": "submission", "is_self": 0, "selftext": "has a body"})
    assert not is_text_only({"record_type": "submission", "is_self": 0, "selftext": "  "})
    assert not is_text_only({"record_type": "submission", "is_self": 0, "selftext": None})


def test_export_rows_and_card_carry_handoff_fields(tmp_path: Path) -> None:
    cfg = _run_pipeline(tmp_path)
    result = run_export(cfg, PROMPT_VERSION, dataset_version="v9", formats=("jsonl",))
    rows = [
        json.loads(line)
        for line in Path(result["jsonl"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    for row in rows:
        assert row["context"]["source"] == "annotator_snapshot"
        assert set(row["context"]) == {"source", "submission", "parent_chain", "replies"}
        assert row["is_text_only"] is True
        assert row["reliability"]["per_label"] == {
            "sarcastic": "unanimous",
            "language": "unanimous",
            "literal_sentiment": "unanimous",
            "intended_sentiment": "unanimous",
        }
        assert row["sampling_strategy"] == "natural"  # comments inherit, never blank
        if row["aux"]["lid"] is not None:
            assert row["aux"]["lid"]["auto_label"] == row["aux"]["lid"]["language"]

    card = json.loads(Path(result["card"]).read_text(encoding="utf-8"))
    assert card["readiness"]["exported_rows"] == len(rows)
    assert card["readiness"]["rows_with_annotator_snapshot_context"] == len(rows)
    assert card["readiness"]["gold_items_labeled"] == 0
    assert card["readiness"]["min_language_x_sarcastic_cell"] >= 1
    assert card["collection_window"]["corpus"]["from"] is not None
    assert card["collection_window"]["exported_rows"]["from"] is not None
    assert card["temporal_context_coverage"]["n_rows"] == len(rows)
    assert "lid_vs_human_gold_language" in card["agreement"]
    assert card["label_distributions"]["sarcastic_per_label_resolution"] == {
        "unanimous": len(rows)
    }
    assert card["label_distributions"]["is_text_only"] == {"True": len(rows)}


def test_temporal_context_coverage_counts_same_author_history(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    base = {"record_type": "comment", "reddit_id": "x", "subreddit": "CasualPH", "text": "t"}
    with AnnotationDatabase(cfg.db_path) as db:
        for fullname, author, ts in [
            ("t1_old", "A", "2026-08-01T10:00:00Z"),  # outside the 48 h window
            ("t1_a1", "A", "2026-08-10T10:00:00Z"),
            ("t1_a2", "A", "2026-08-11T10:00:00Z"),
            ("t1_a3", "A", "2026-08-11T20:00:00Z"),  # target: a1 + a2 qualify
            ("t1_b1", "B", "2026-08-11T20:00:00Z"),  # target: nothing before it
        ]:
            db.upsert_corpus_record(
                {**base, "reddit_fullname": fullname, "author_hash": author, "created_utc": ts}
            )
        db.commit()
        cov = temporal_context_coverage(db, ["t1_a3", "t1_b1"])
    assert cov["n_rows"] == 2
    assert cov["share_with_at_least_1"] == 0.5
    assert cov["share_with_at_least_3"] == 0.0
    assert cov["share_with_full_5"] == 0.0
    assert cov["mean_available"] == 1.0
