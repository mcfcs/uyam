"""Annotation EDA: votes, confidence and gold comparisons are computed correctly and read-only."""

from __future__ import annotations

import json
from pathlib import Path

from uyam.annotate.db import AnnotationDatabase
from uyam.annotation_eda import (
    compare_gold_to_all,
    confidence_vs_gold,
    estimate_human_rate,
    load_post_meta,
    load_votes,
    model_summary,
    pairwise_kappas,
    version_table,
    vote_count_vs_gold,
    vote_wide,
)
from uyam.progress_notes import GoldItem

GUIDE = "guide-v1.1"

# (post, qwen3, sealion, llamasealion) votes; confidences are fixed per model.
_VOTES = [
    ("t1_a", 1, 1, 1),
    ("t1_b", 1, 1, 0),
    ("t1_c", 0, 1, 0),
    ("t1_d", 0, 0, 0),
    ("t1_e", 1, 1, 1),
]
_CONF = {"qwen3": 0.9, "sealion": 0.8, "llamasealion": 0.85}
_GOLD = {
    "t1_a": GoldItem(1, True),
    "t1_b": GoldItem(0, True),
    "t1_c": GoldItem(0, False),
    "t1_d": GoldItem(0, True),
}


def _votes_db(path: Path, prompt_version: str = GUIDE) -> Path:
    with AnnotationDatabase(path) as db:
        for name, *vals in _VOTES:
            for key, value in zip(("qwen3", "sealion", "llamasealion"), vals, strict=True):
                db.insert_llm_annotation(
                    {"reddit_fullname": name, "model_key": key, "prompt_version": prompt_version,
                     "role": "annotator", "sarcastic": value, "confidence": _CONF[key],
                     "language": "english", "literal_sentiment": "neutral",
                     "intended_sentiment": "neutral"}
                )
        for name, *vals in _VOTES:
            db.upsert_aggregate(
                {"reddit_fullname": name, "prompt_version": prompt_version,
                 "sarcastic_final": int(sum(vals) >= 2)}
            )
        db.commit()
    return path


def test_votes_wide_and_model_summary(tmp_path: Path) -> None:
    db = _votes_db(tmp_path / "v5.sqlite3")
    before = db.read_bytes()
    votes = load_votes(db, GUIDE)
    assert db.read_bytes() == before  # read-only
    assert len(votes) == 15
    wide = vote_wide(votes)
    assert dict(wide["n_yes"]) == {"t1_a": 3, "t1_b": 2, "t1_c": 1, "t1_d": 0, "t1_e": 3}
    summary = model_summary(votes).set_index("model")
    assert summary.loc["Gemma-SEA-LION 9B", "sarcastic rate"] == 0.8
    assert summary.loc["qwen3 8B", "mean confidence"] == 0.9
    assert load_votes(tmp_path / "missing.sqlite3", GUIDE).empty


def test_pairwise_kappas_cover_every_pair(tmp_path: Path) -> None:
    wide = vote_wide(load_votes(_votes_db(tmp_path / "v5.sqlite3"), GUIDE))
    pairs = pairwise_kappas(wide)
    assert len(pairs) == 3
    row = pairs.set_index("pair").loc["qwen3 8B × Gemma-SEA-LION 9B"]
    assert row["raw agreement"] == 0.8  # they differ only on t1_c


def test_vote_count_vs_gold_and_estimate(tmp_path: Path) -> None:
    wide = vote_wide(load_votes(_votes_db(tmp_path / "v5.sqlite3"), GUIDE))
    table = vote_count_vs_gold(wide, _GOLD).set_index("'sarcastic' votes")
    assert table.loc["3 of 3", "posts (all)"] == 2 and table.loc["3 of 3", "gold posts"] == 1
    assert table.loc["3 of 3", "P(sarcastic per Gio)"] == 1.0
    assert table.loc["2 of 3", "P(sarcastic per Gio)"] == 0.0
    est = estimate_human_rate(table.reset_index())
    # 3-of-3: 2 posts × 1.0; 2-of-3: 1 × 0; 1-of-3: 1 × 0; 0-of-3: 1 × 0 → 2 of 5.
    assert est["estimated human-sarcastic posts"] == 2 and est["estimated human rate"] == 0.4
    assert est["majority-label positives"] == 3
    assert est["estimated precision of the majority label"] == round(2 / 3, 3)
    assert est["estimated recall of the majority label"] == 1.0


def test_confidence_vs_gold(tmp_path: Path) -> None:
    votes = load_votes(_votes_db(tmp_path / "v5.sqlite3"), GUIDE)
    table = confidence_vs_gold(votes, _GOLD).set_index("model")
    # qwen3 (always 0.9) says yes on t1_a (Gio yes) and t1_b (Gio no): half hold.
    assert table.loc["qwen3 8B", "'sarcastic' votes ≥ 0.9 conf"] == 2
    assert table.loc["qwen3 8B", "…of which Gio agrees"] == 0.5


def test_post_meta_compare_and_version_table(tmp_path: Path) -> None:
    jsonl = tmp_path / "dataset-v5.jsonl"
    rows = []
    for name, lang, kind in (
        ("t1_a", "taglish", "image"), ("t1_b", "taglish", "text"), ("t1_c", "english", "text"),
        ("t1_d", "english", "text"), ("t1_e", "tagalog", "video"),
    ):
        rows.append(json.dumps({
            "reddit_fullname": name,
            "media_context": {"post_type": kind, "text_only": kind == "text"},
            "aux": {"lid": {"language": "taglish", "language_rule": lang}},
        }))
    jsonl.write_text("\n".join(rows) + "\n", encoding="utf-8")
    meta = load_post_meta(jsonl)
    assert meta["t1_e"] == {"post_type": "video", "text_only": False, "language": "tagalog"}

    db = _votes_db(tmp_path / "v5.sqlite3")
    wide = vote_wide(load_votes(db, GUIDE))
    lang = compare_gold_to_all(meta, wide, _GOLD, "language").set_index("language")
    assert lang.loc["taglish", "share of all posts"] == 0.4
    assert lang.loc["taglish", "Gio's sarcastic rate (gold)"] == 0.5  # a yes, b no

    table = version_table({"v5": (db, GUIDE)}, _GOLD, meta, {"v5": {"Fleiss κ (sarcasm)": 0.26}})
    row = table.iloc[0]
    assert row["labeled posts"] == 5 and row["sarcastic"].startswith("3 ")
    assert row["Fleiss κ (LLMs)"] == 0.26
    assert row["gold recall"] == 1.0 and row["gold precision"] == 0.5
    assert row["sarcastic: text-only posts"] == "33.3%"  # b of b, c, d


def test_eda_page_renders(tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    db_dir = tmp_path / "db"
    _votes_db(db_dir / "annotation-v5.sqlite3")
    _votes_db(db_dir / "annotation-v3.sqlite3", "sarc-v2")
    with AnnotationDatabase(db_dir / "annotation.sqlite3") as db:
        for name, item in _GOLD.items():
            db.upsert_human_review(
                {"reddit_fullname": name, "sarcastic": item.sarcastic, "is_gold": 1,
                 "first_pass_json": json.dumps({"sarcastic": item.sarcastic}),
                 "assist_before_first_pass": int(not item.blind)}
            )
        db.commit()

    def page(data_dir: str) -> None:
        from pathlib import Path

        from uyam.annotation_eda_ui import render_annotation_eda

        render_annotation_eda(Path(data_dir))

    at = AppTest.from_function(page, args=(str(tmp_path),), default_timeout=30)
    at.run()
    assert not at.exception
    assert at.metric[0].value == "5"
    assert len(at.expander) == 8
