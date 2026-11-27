"""Stricter re-aggregation rules over stored votes, scored on the gold (no model run)."""

from __future__ import annotations

from pathlib import Path

from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.rule_compare import (
    CONFIDENCE_FLOOR,
    all_rule_labels,
    dawid_skene,
    load_votes,
    rule_confidence_floor,
    rule_majority,
    rule_qwen3_and_llama,
    rule_unanimous,
    score_rules,
)
from uyam.progress_notes import GoldItem

GUIDE = "guide-v1.1"


def _v(q: int, s: int, l: int, conf: float = 0.9) -> dict[str, tuple[int, float]]:  # noqa: E741
    return {"qwen3": (q, conf), "sealion": (s, conf), "llamasealion": (l, conf)}


def test_rules_on_hand_made_votes() -> None:
    assert rule_majority(_v(1, 1, 0)) == 1 and rule_majority(_v(1, 0, 0)) == 0
    assert rule_unanimous(_v(1, 1, 1)) == 1 and rule_unanimous(_v(1, 1, 0)) == 0
    assert rule_qwen3_and_llama(_v(1, 0, 1)) == 1  # SEA-LION ignored
    assert rule_qwen3_and_llama(_v(1, 1, 0)) == 0
    low = CONFIDENCE_FLOOR - 0.05
    two_low = {"qwen3": (1, low), "sealion": (1, low), "llamasealion": (0, 0.99)}
    assert rule_majority(two_low) == 1 and rule_confidence_floor(two_low) == 0
    mixed = {"qwen3": (1, 0.9), "sealion": (1, low), "llamasealion": (1, 0.9)}
    assert rule_confidence_floor(mixed) == 1  # two confident yes votes are enough


def test_dawid_skene_learns_reliabilities_without_gold() -> None:
    # A strict annotator (c) says yes only on true cases; a liberal one (b) says yes often.
    votes = {}
    for i in range(200):
        truth = int(i < 40)
        a = truth if i % 10 else 1 - truth  # 90% accurate
        b = 1 if truth or i % 3 == 0 else 0  # liberal: many false positives
        c = truth if i % 4 else 0  # strict: misses some, never a false positive
        votes[f"t1_{i}"] = _v(a, b, c)
    ds = dawid_skene(votes)
    assert ds["specificity"]["llamasealion"] > ds["specificity"]["sealion"]
    assert ds["sensitivity"]["sealion"] > ds["sensitivity"]["llamasealion"]
    assert 0 < ds["prior"] < 1
    assert set(ds["labels"].values()) <= {0, 1}


def test_score_rules_reports_flagged_kappa_and_paired_delta() -> None:
    votes = {
        "t1_a": _v(1, 1, 1),  # Gio yes
        "t1_b": _v(1, 1, 0),  # Gio no: majority false positive, unanimous right
        "t1_c": _v(0, 1, 0),  # Gio no
        "t1_d": _v(0, 0, 0),  # Gio no
        "t1_e": _v(1, 1, 1),  # Gio yes
    }
    gold = {f: GoldItem(int(f in {"t1_a", "t1_e"}), blind=f != "t1_c") for f in votes}
    labels, _ = all_rule_labels(votes)
    rows = {r["rule"]: r for r in score_rules(labels, gold, n_boot=200)}
    assert rows["majority"]["flagged"] == 3 and rows["unanimous"]["flagged"] == 2
    assert rows["unanimous"]["kappa"] == 1.0
    assert rows["unanimous"]["pre-named"] and not rows["qwen3_and_llama"]["pre-named"]
    assert rows["unanimous"]["delta kappa vs majority"] > 0
    assert rows["majority"]["delta CI"] is None  # the reference
    blind = {r["rule"]: r for r in score_rules(labels, gold, blind_only=True, n_boot=200)}
    assert blind["majority"]["gold posts"] == 4


def test_load_votes_is_read_only_and_needs_all_three(tmp_path: Path) -> None:
    db_path = tmp_path / "v5.sqlite3"
    with AnnotationDatabase(db_path) as db:
        for key, value in (("qwen3", 1), ("sealion", 1), ("llamasealion", 0)):
            db.insert_llm_annotation(
                {"reddit_fullname": "t1_full", "model_key": key, "prompt_version": GUIDE,
                 "role": "annotator", "sarcastic": value, "confidence": 0.9,
                 "language": "english", "literal_sentiment": "neutral",
                 "intended_sentiment": "neutral"}
            )
        db.insert_llm_annotation(
            {"reddit_fullname": "t1_partial", "model_key": "qwen3", "prompt_version": GUIDE,
             "role": "annotator", "sarcastic": 1, "confidence": 0.9, "language": "english",
             "literal_sentiment": "neutral", "intended_sentiment": "neutral"}
        )
        db.commit()
    before = db_path.read_bytes()
    votes = load_votes(db_path, GUIDE)
    assert db_path.read_bytes() == before
    assert list(votes) == ["t1_full"]
    assert votes["t1_full"]["llamasealion"] == (0, 0.9)
