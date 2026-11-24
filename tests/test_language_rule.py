"""Rule-based language (GUIDELINE L2-L4b) on token tags; offline, no fastText model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.annotate_fixtures import make_config, write_raw_corpus
from uyam.annotate import lid as lid_module
from uyam.annotate.aggregate import _language_rule_vs_human_gold
from uyam.annotate.candidates import select_candidates
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.lid import (
    RULE_VERSION,
    lexicon_language,
    refresh_language_rule,
    rule_language,
)
from uyam.annotate.prompts import PROMPT_VERSION


def _tags(spec: str) -> list[tuple[str, str]]:
    """'He:en sa:tl PH:other' -> [("He", "en"), ("sa", "tl"), ("PH", "other")]."""
    return [tuple(part.rsplit(":", 1)) for part in spec.split()]  # type: ignore[misc]


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        # L4: a lone particle keeps an English target English (guideline example).
        ("He's:en still:en jobless:en sa:tl PH:other", "english"),
        # L4b: the same particle twice is deliberate mixing.
        ("Thank:en you:en po:tl we're:en fine:en po:tl", "taglish"),
        # L3: on a short target one content word is already over 10% (guideline example).
        ("Rare:en yan:tl parang:tl misprint:en", "taglish"),
        # A lone English function word keeps a Tagalog target Tagalog.
        ("Grabe:tl the:en traffic:tl kanina:tl sa:tl EDSA:other", "tagalog"),
        # One content word in a long target stays under 10%.
        (" ".join(["word:en"] * 19) + " ulam:tl", "english"),
        # ...but two minority words at >= 10% make it Taglish.
        (" ".join(["word:en"] * 18) + " ulam:tl masarap:tl", "taglish"),
        # L2: laughter and internet tokens are not counted.
        ("hahaha:tl nice:en loot:en bag:en lol:tl", "english"),
        ("Happy:en birthday:en OP:tl", "english"),
        # Names and noise ('other') are not counted either.
        ("Jollibee:other Uniqlo:other", None),
    ],
)
def test_rule_language_follows_the_guideline(spec: str, expected: str | None) -> None:
    assert rule_language(_tags(spec)) == expected


class _FakeIdentifier:
    """Tags every token English except the Filipino function words (no fastText)."""

    def __init__(self, cfg: Any) -> None:
        pass

    def rule_tags(self, text: str) -> tuple[list[tuple[str, str]], str]:
        tags = [
            (tok, "tl" if tok.lower() in lid_module._TL_FUNCTION_WORDS else "en")
            for tok in lid_module._TOKEN_RE.findall(text)
        ]
        return tags, RULE_VERSION


def test_refresh_sets_only_language_rule_for_the_target_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    monkeypatch.setattr(lid_module, "LanguageIdentifier", _FakeIdentifier)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)
        eligible = db.eligible_fullnames()
        for fullname in eligible:
            db.upsert_lid(fullname, {"language": "taglish", "confidence": 0.5})
        db.commit()
        target = db.target_items(PROMPT_VERSION, 2)

        stats = refresh_language_rule(db, cfg.lid, target=2, prompt_version=PROMPT_VERSION)
        assert stats == {"processed": 2}
        rows = {
            str(r["reddit_fullname"]): dict(r)
            for r in db.conn.execute("SELECT * FROM lid_results")
        }
    for fullname, row in rows.items():
        assert row["language"] == "taglish"  # the original field never changes
        if fullname in target:
            assert row["language_rule"] in {"english", "tagalog", "taglish"}
            assert row["rule_version"] == RULE_VERSION
        else:
            assert row["language_rule"] is None  # outside the target set: untouched


def test_card_scores_language_rule_against_the_blind_gold(tmp_path: Path) -> None:
    from tests.test_annotate_review_ui import LABEL_KEYS, _setup
    from uyam.annotate.review_workflow import build_review_row

    _, db_path = _setup(tmp_path, {"t1_c3": "gold", "t1_c4": "gold"}, assists=())
    with AnnotationDatabase(db_path) as db:
        assert _language_rule_vs_human_gold(db) == {"n": 0, "accuracy": None, "cohen_kappa": None}
        cases = (("t1_c3", "english", "english"), ("t1_c4", "taglish", "english"))
        for fullname, human, rule in cases:
            db.upsert_lid(fullname, {"language": "taglish", "language_rule": rule})
            first = dict.fromkeys(LABEL_KEYS) | {"sarcastic": 0, "language": human}
            db.upsert_human_review(
                build_review_row(fullname, first, notes=None, is_gold=True, existing=None)
            )
        db.commit()
        score = _language_rule_vs_human_gold(db)
    assert score["n"] == 2 and score["accuracy"] == 0.5


# Made-up zipf frequencies (wordfreq's scale): word -> (english, filipino).
_ZIPF = {
    "sumali": (0.0, 4.8),     # Tagalog only
    "kahit": (1.8, 6.1),      # Tagalog, rare in English text
    "bag": (4.8, 4.9),        # English loanword used in Filipino at English rates
    "birthday": (4.8, 4.7),
    "guarantee": (4.4, 0.0),  # English only
    "nagstop": (0.0, 0.0),    # Taglish coinage neither list knows
}


def _zipf(word: str, lang: str) -> float:
    en, fil = _ZIPF.get(word, (0.0, 0.0))
    return en if lang == "en" else fil


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("sumali", "tl"),
        ("Kahit", "tl"),  # case-insensitive
        ("bag", "en"),  # a loanword stays English unless it is 10x more Filipino
        ("birthday", "en"),
        ("guarantee", "en"),
        ("nagstop", None),  # unknown to both lists: the caller falls back to fastText
        ("po", "tl"),  # function-word lists win first
        ("the", "en"),
    ],
)
def test_lexicon_language_tags_by_relative_frequency(word: str, expected: str | None) -> None:
    assert lexicon_language(word, _zipf) == expected


def test_rule_with_lexicon_tags_keeps_a_tagalog_target_tagalog() -> None:
    text = "Kahit sumali pa sila"
    tags = [(w, lexicon_language(w, _zipf) or "tl") for w in text.split()]
    assert rule_language(tags) == "tagalog"

