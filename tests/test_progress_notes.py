"""Progress notes tab: live figures are read-only and score the gold correctly."""

from __future__ import annotations

import json
from pathlib import Path

from uyam.annotate.db import AnnotationDatabase
from uyam.progress_notes import (
    GUIDE,
    SARC_V2,
    card_facts,
    cohen_kappa,
    default_sources,
    f1,
    gold_agreement,
    load_gold,
    run_progress,
)

# Four gold items: two blind, one Claude-assisted, one with votes shown (left out).
_GOLD = [
    ("t1_a", 1, {"blind": True}),
    ("t1_b", 0, {"blind": True}),
    ("t1_c", 1, {"blind": False}),
    ("t1_d", 0, {"votes_seen": True}),
]


def _main_db(tmp_path: Path) -> Path:
    db_dir = tmp_path / "db"
    path = db_dir / "annotation.sqlite3"
    with AnnotationDatabase(path) as db:
        for fullname, value, opts in _GOLD:
            db.enqueue_review(fullname, "gold")
            first = {"sarcastic": value}
            db.upsert_human_review(
                {
                    "reddit_fullname": fullname,
                    # The revised label differs: scores must use the first pass.
                    "sarcastic": 1 - value,
                    "is_gold": 1,
                    "first_pass_json": json.dumps(first),
                    "votes_seen": int(bool(opts.get("votes_seen"))),
                    "assist_before_first_pass": int(opts.get("blind") is False),
                }
            )
        db.enqueue_review("t1_e", "gold")  # in the queue, not labeled yet
        for fullname, value in (("t1_a", 1), ("t1_b", 0), ("t1_c", 0), ("t1_d", 1)):
            db.upsert_aggregate(
                {"reddit_fullname": fullname, "prompt_version": SARC_V2, "sarcastic_final": value}
            )
        db.commit()
    return db_dir


def test_load_gold_uses_first_passes_and_drops_votes_seen(tmp_path: Path) -> None:
    db_dir = _main_db(tmp_path)
    gold, stats = load_gold(db_dir / "annotation.sqlite3")
    assert stats == {"queue": 5, "labeled": 4, "votes_seen": 1}
    assert {f: (g.sarcastic, g.blind) for f, g in gold.items()} == {
        "t1_a": (1, True),
        "t1_b": (0, True),
        "t1_c": (1, False),
    }


def test_gold_agreement_scores_all_and_blind_and_never_writes(tmp_path: Path) -> None:
    db_dir = _main_db(tmp_path)
    main = db_dir / "annotation.sqlite3"
    before = main.read_bytes()
    gold, _ = load_gold(main)
    rows = gold_agreement(gold, default_sources(db_dir))
    assert main.read_bytes() == before  # read-only
    [label] = [r for r in rows if r["source"].startswith("v1/v2")]
    # human a=1 b=0 c=1 vs label a=1 b=0 c=0: one miss on the assisted item.
    assert label["gold items"] == 3 and label["human +"] == 2 and label["model +"] == 1
    assert label["F1 vs gold"] == round(f1([1, 0, 1], [1, 0, 0]) or 0, 3)
    assert label["κ vs gold (all)"] == round(cohen_kappa([1, 0, 1], [1, 0, 0]) or 0, 3)
    assert label["blind items"] == 2 and label["κ vs gold (blind)"] == 1.0
    # Databases that don't exist (v3/v4/v5 here) are skipped, not errors.
    assert all(r["source"].startswith("v1/v2") for r in rows)


def test_kappa_and_f1_edge_cases() -> None:
    assert cohen_kappa([1, 0], [1, 0]) == 1.0
    assert cohen_kappa([1], [1]) is None
    assert cohen_kappa([0, 0], [0, 0]) is None
    assert f1([0, 0], [0, 0]) is None
    assert f1([1, 1, 0], [1, 0, 1]) == 0.5


def test_run_progress_counts_votes_toward_the_target(tmp_path: Path) -> None:
    path = tmp_path / "v5.sqlite3"
    assert run_progress(path, GUIDE, ["a"], 10) == {}
    with AnnotationDatabase(path) as db:
        for i in range(4):
            db.insert_llm_annotation(
                {"reddit_fullname": f"t1_{i}", "model_key": "a", "prompt_version": GUIDE,
                 "role": "annotator", "sarcastic": 0, "language": "english",
                 "literal_sentiment": "neutral", "intended_sentiment": "neutral"}
            )
    prog = run_progress(path, GUIDE, ["a", "b"], 10)
    assert prog["models"] == {"a": 4, "b": 0} and prog["share"] == 0.2


def test_card_facts_reads_versioned_cards(tmp_path: Path) -> None:
    card = {
        "dataset_version": "v9",
        "counts": {"exported_rows": 5, "corpus_dump_rows": 7, "candidates": {"phatic": 1}},
        "readiness": {"sarcastic_positives": 2},
        "temporal_context_coverage": {"by_window": {"48h": {"share_with_at_least_1": 0.5}}},
        "agreement": {"labels": {"sarcastic": {"fleiss_kappa": 0.4}}},
    }
    (tmp_path / "dataset_card-v9.json").write_text(json.dumps(card), encoding="utf-8")
    (tmp_path / "dataset_card-v8.json").write_text("not json", encoding="utf-8")
    facts = card_facts(tmp_path)
    assert list(facts) == ["v9"]
    assert facts["v9"]["rows"] == 5 and facts["v9"]["history 48 h"] == 0.5
    assert facts["v9"]["exclusions"] == {"phatic": 1}


def test_diagrams_are_well_formed_dot() -> None:
    from uyam.progress_notes_ui import ALL_DIAGRAMS

    for name, dot in ALL_DIAGRAMS.items():
        assert dot.startswith("digraph "), name
        assert dot.count("{") == dot.count("}"), name
        assert dot.count('"') % 2 == 0, name


def test_progress_tab_renders_without_errors(tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    db_dir = _main_db(tmp_path)

    def page(data_dir: str) -> None:
        from pathlib import Path

        from uyam.progress_notes_ui import render_progress_notes

        render_progress_notes(Path(data_dir))

    at = AppTest.from_function(page, args=(str(db_dir.parent),), default_timeout=30)
    at.run()
    assert not at.exception
    assert at.metric[0].value == "4 / 5"  # gold labeled / queue
    assert len(at.expander) == 11
