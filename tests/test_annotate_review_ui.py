"""Annotation Review tab: blind first pass -> optional assist reveal -> revise.

The gold labels are the only independent check on the ensemble, so the tab
must never show (or hint at) an assist suggestion before the reviewer saved a
first-pass label, and a revision must never replace that first pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.annotate_fixtures import make_config, write_raw_corpus
from uyam.annotate.candidates import select_candidates
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.prompts import PROMPT_VERSION
from uyam.annotate.review_workflow import (
    LABEL_KEYS,
    build_review_row,
    highlight_spans,
    reveal_assist,
    split_sentences,
)

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

LONG_TEXT = (
    "Grabe ang traffic kanina. Ang galing talaga ng MMDA, sobrang bilis ng biyahe!!! "
    "Sana all may helicopter"
)
LONG_SENTENCES = [
    "Grabe ang traffic kanina.",
    "Ang galing talaga ng MMDA, sobrang bilis ng biyahe!!!",
    "Sana all may helicopter",
]
RATIONALE = "ASSIST-RATIONALE-MARKER praise for a service that just failed"
ASSIST_SPAN = "Wow ang galing naman"  # exact substring of t1_c3's text


# ---------------------------------------------------------------------------
# Sentence splitter and pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("   \n  ", []),
        ("walang punctuation dito grabe talaga", ["walang punctuation dito grabe talaga"]),
        ("Ang galing mo naman. Edi wow!", ["Ang galing mo naman.", "Edi wow!"]),
        ("Talaga ba?? Hala...\nok lang\n\nsige", ["Talaga ba??", "Hala...", "ok lang", "sige"]),
        ('Sabi niya "ang ganda." Tapos umalis', ['Sabi niya "ang ganda."', "Tapos umalis"]),
        ("Php 12.50 lang daw, mura na", ["Php 12.50 lang daw, mura na"]),
        ("ayos… sana all", ["ayos…", "sana all"]),
        (LONG_TEXT, LONG_SENTENCES),
    ],
)
def test_split_sentences(text: str, expected: list[str]) -> None:
    sentences = split_sentences(text)
    assert sentences == expected
    assert all(s in text for s in sentences)  # exact substrings, so spans are exact text


def test_highlight_spans_escapes_and_reports_missing() -> None:
    html, missing = highlight_spans("a <b> sarcastic & bit\nend", "sarcastic & bit\nnot there")
    assert html == "a &lt;b&gt; <mark>sarcastic &amp; bit</mark><br>end"
    assert missing == ["not there"]


def test_build_review_row_keeps_first_pass_and_snapshots_legacy_rows() -> None:
    first = dict.fromkeys(LABEL_KEYS) | {"sarcastic": 1, "language": "taglish"}
    row = build_review_row("t1_x", first, notes="", is_gold=True, existing=None)
    assert json.loads(row["first_pass_json"])["sarcastic"] == 1
    assert row["assist_seen"] == 0 and row["notes"] is None and row["is_gold"] == 1

    revised = first | {"sarcastic": 0}
    seen = row | {"assist_seen": 1}
    again = build_review_row("t1_x", revised, notes=None, is_gold=True, existing=seen)
    assert again["first_pass_json"] == row["first_pass_json"]
    assert again["sarcastic"] == 0 and again["assist_seen"] == 1

    # A row saved before first_pass_json existed: its stored labels were the blind pass.
    legacy = dict.fromkeys(LABEL_KEYS) | {"sarcastic": 1, "first_pass_json": None}
    from_legacy = build_review_row("t1_x", revised, notes=None, is_gold=True, existing=legacy)
    assert json.loads(from_legacy["first_pass_json"])["sarcastic"] == 1


# ---------------------------------------------------------------------------
# AppTest on a tmp_path DB
# ---------------------------------------------------------------------------


def _setup(tmp_path: Path, queue: dict[str, str], assists: tuple[str, ...]) -> tuple[Path, Path]:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)
        db.conn.execute(
            "UPDATE corpus_index SET text = ? WHERE reddit_fullname = 't1_c4'", (LONG_TEXT,)
        )
        for fullname, reason in queue.items():
            db.enqueue_review(fullname, reason)
        for fullname in assists:
            db.conn.execute(
                "INSERT INTO assist_suggestions (reddit_fullname, source, guideline_version, "
                "sarcastic, sarcastic_span, rationale, confidence, created_at) "
                "VALUES (?, 'claude-code-test', 'guide-v9', 1, ?, ?, 0.85, '2026-11-19')",
                (fullname, ASSIST_SPAN, RATIONALE),
            )
        db.commit()
    config_yaml = tmp_path / "annotation.yaml"
    config_yaml.write_text(
        yaml.safe_dump(
            {
                "db_path": cfg.db_path.as_posix(),
                "data_dir": cfg.data_dir.as_posix(),
                "prompt_version": PROMPT_VERSION,
                "endpoints": {"local": "http://127.0.0.1:11434"},
                "annotators": [{"key": "a1", "model": "model-one", "endpoint": "local"}],
                "adjudicator": {"key": "adj", "model": "model-adj", "endpoint": "local"},
            }
        ),
        encoding="utf-8",
    )
    return config_yaml, cfg.db_path


def _page(config_path: str) -> None:
    from pathlib import Path

    from uyam.annotate.review_ui import render_review_tab

    render_review_tab(Path(config_path))


def _app(config_yaml: Path) -> Any:
    at = AppTest.from_function(_page, args=(str(config_yaml),), default_timeout=30)
    at.run()
    assert not at.exception
    return at


def _rendered_text(at: Any) -> str:
    """Every value and label the page rendered, for leak checks."""
    chunks: list[str] = []
    for kind in ("markdown", "caption", "info", "success", "warning", "text", "json", "subheader"):
        chunks += [str(el.value) for el in getattr(at, kind)]
    for kind in ("button", "checkbox", "radio", "selectbox", "text_input"):
        chunks += [f"{el.label} {getattr(el, 'help', '') or ''}" for el in getattr(at, kind)]
    chunks += [str(el.label) for el in at.expander]
    return "\n".join(chunks)


def _save(at: Any) -> None:
    [submit] = [b for b in at.button if b.label == "Save label"]
    submit.click().run()
    assert not at.exception


def _review(db_path: Path, fullname: str) -> dict[str, Any] | None:
    with AnnotationDatabase(db_path) as db:
        return db.get_human_review(fullname)


def _assert_no_assist_leak(at: Any) -> None:
    text = _rendered_text(at)
    for leak in (RATIONALE, "claude-code-test", "guide-v9", "<mark>", "0.85", "Show Claude's take"):
        assert leak not in text
    assert "can be revealed after you save" in text  # the neutral note is fine


def test_blind_first_pass_then_reveal_then_revise(tmp_path: Path) -> None:
    config_yaml, db_path = _setup(tmp_path, {"t1_c3": "gold"}, assists=("t1_c3",))
    at = _app(config_yaml)

    # (a) An assist row exists, but nothing from it is rendered before the first save.
    _assert_no_assist_leak(at)
    assert at.checkbox(key="votes_t1_c3").value is False  # gold keeps model votes hidden
    assert not at.json

    # (b) First save writes the labels AND first_pass_json; the item stays on screen.
    at.radio(key="review_t1_c3_sarcastic").set_value("yes")
    at.radio(key="review_t1_c3_language").set_value("taglish")
    at.radio(key="review_t1_c3_literal").set_value("positive")
    at.radio(key="review_t1_c3_intended").set_value("negative")
    at.checkbox(key="review_t1_c3_cue_polarity_inversion").check()
    _save(at)
    first = _review(db_path, "t1_c3")
    assert first is not None
    expected_first = {
        "sarcastic": 1,
        "language": "taglish",
        "literal_sentiment": "positive",
        "intended_sentiment": "negative",
        "cue_polarity_inversion": 1,
        "cue_rhetorical_intent": 0,
        "cue_contextual_incongruity": 0,
        "cue_hyperbole": 0,
        # one-sentence target: the sentence is pre-ticked, so it is the span
        "sarcastic_span": "Wow ang galing naman ng serbisyo nila dito.",
        "unsure": 0,
    }
    assert json.loads(str(first["first_pass_json"])) == expected_first
    assert {k: first[k] for k in LABEL_KEYS} == expected_first
    assert first["is_gold"] == 1 and first["assist_seen"] == 0
    assert "Label saved." in _rendered_text(at)
    assert RATIONALE not in _rendered_text(at)  # saving alone does not reveal

    # (c) Reveal persists assist_seen = 1 and renders the suggestion.
    at.button(key="reveal_t1_c3").click().run()
    assert not at.exception
    revealed = _review(db_path, "t1_c3")
    assert revealed is not None and revealed["assist_seen"] == 1
    text = _rendered_text(at)
    assert RATIONALE in text
    assert f"<mark>{ASSIST_SPAN}</mark>" in text
    assert "claude-code-test" in text and "guide-v9" in text and "0.85" in text
    assert "Verdict:** sarcastic" in text

    # (d) Revise after reveal: labels change, first_pass_json is byte-identical.
    at.button(key="revise_btn_t1_c3").click().run()
    at.radio(key="revise_t1_c3_sarcastic").set_value("no")
    at.radio(key="revise_t1_c3_intended").set_value("positive")
    _save(at)
    revised = _review(db_path, "t1_c3")
    assert revised is not None
    assert revised["sarcastic"] == 0 and revised["intended_sentiment"] == "positive"
    assert revised["sarcastic_span"] is None  # sarcastic = no stores NULL
    assert revised["first_pass_json"] == first["first_pass_json"]
    assert revised["assist_seen"] == 1

    # Next item leaves the (now empty) gold queue.
    at.button(key="next_t1_c3").click().run()
    assert "No pending items in the gold queue." in _rendered_text(at)


def test_revisit_after_restart_never_replaces_first_pass(tmp_path: Path) -> None:
    config_yaml, db_path = _setup(tmp_path, {"t1_c3": "gold"}, assists=("t1_c3",))
    at = _app(config_yaml)
    _save(at)  # defaults: sarcastic = no
    first = _review(db_path, "t1_c3")
    assert first is not None and first["sarcastic"] == 0

    restarted = _app(config_yaml)  # fresh session, as after an app restart
    restarted.selectbox(key="review_revisit_gold").set_value("t1_c3")
    restarted.button(key="review_open_gold").click().run()
    assert restarted.button(key="reveal_t1_c3")  # still unrevealed after the restart
    restarted.button(key="revise_btn_t1_c3").click().run()
    restarted.radio(key="revise_t1_c3_sarcastic").set_value("yes")
    _save(restarted)
    again = _review(db_path, "t1_c3")
    assert again is not None and again["sarcastic"] == 1
    assert again["first_pass_json"] == first["first_pass_json"]
    assert json.loads(str(again["first_pass_json"]))["sarcastic"] == 0


def test_span_ticks_exact_sentence_and_unsure(tmp_path: Path) -> None:
    config_yaml, db_path = _setup(tmp_path, {"t1_c4": "gold", "t1_c5": "gold"}, assists=())
    at = _app(config_yaml)

    # (e) the long target is split into ticks; ticking one stores its exact text.
    assert [at.checkbox(key=f"review_t1_c4_span_{i}").label for i in range(3)] == LONG_SENTENCES
    assert not any(at.checkbox(key=f"review_t1_c4_span_{i}").value for i in range(3))
    at.radio(key="review_t1_c4_sarcastic").set_value("yes")
    at.checkbox(key="review_t1_c4_span_1").check()
    # (f) unsure is stored.
    at.checkbox(key="review_t1_c4_unsure").check()
    _save(at)
    row = _review(db_path, "t1_c4")
    assert row is not None
    assert row["sarcastic_span"] == LONG_SENTENCES[1]
    assert row["unsure"] == 1
    assert json.loads(str(row["first_pass_json"]))["unsure"] == 1

    # (g) no assist row -> no reveal button, after the save either.
    assert not [b for b in at.button if b.label == "Show Claude's take"]
    assert "No Claude suggestion for this item." in _rendered_text(at)

    # Next item; a ticked sentence with sarcastic = no stores NULL, unsure 0.
    at.button(key="next_t1_c4").click().run()
    at.checkbox(key="review_t1_c5_span_0").check()
    _save(at)
    other = _review(db_path, "t1_c5")
    assert other is not None
    assert other["sarcastic"] == 0 and other["sarcastic_span"] is None and other["unsure"] == 0

    with AnnotationDatabase(db_path) as db:
        assert db.review_queue_counts() == {"gold": 0, "low_confidence": 0, "completed": 2}


def test_low_confidence_shows_votes_but_assist_stays_blind_first(tmp_path: Path) -> None:
    config_yaml, db_path = _setup(tmp_path, {"t1_c3": "low_confidence"}, assists=("t1_c3",))
    at = _app(config_yaml)
    at.radio(key="review_queue_reason").set_value("low_confidence").run()
    assert not at.exception
    assert not [c for c in at.checkbox if c.key == "votes_t1_c3"]  # votes always shown here
    _assert_no_assist_leak(at)

    _save(at)
    row = _review(db_path, "t1_c3")
    assert row is not None and row["is_gold"] == 0 and row["first_pass_json"]
    at.button(key="reveal_t1_c3").click().run()
    assert RATIONALE in _rendered_text(at)


def test_reveal_assist_refuses_before_first_pass(tmp_path: Path) -> None:
    _, db_path = _setup(tmp_path, {"t1_c3": "gold"}, assists=("t1_c3",))
    with AnnotationDatabase(db_path) as db:
        assert reveal_assist(db, "t1_c3") is None
        assert db.get_human_review("t1_c3") is None


def test_gold_kappa_uses_the_blind_first_pass(tmp_path: Path) -> None:
    from uyam.annotate.aggregate import _gold_cohen_kappa

    _, db_path = _setup(tmp_path, {"t1_c3": "gold", "t1_c4": "gold"}, assists=())
    with AnnotationDatabase(db_path) as db:
        for fullname, ensemble in (("t1_c3", 1), ("t1_c4", 0)):
            db.upsert_aggregate(
                {
                    "reddit_fullname": fullname,
                    "prompt_version": PROMPT_VERSION,
                    "sarcastic_final": ensemble,
                }
            )
            # The blind first pass agreed with the ensemble; the revision flipped it.
            first = dict.fromkeys(LABEL_KEYS) | {"sarcastic": ensemble}
            db.upsert_human_review(
                build_review_row(fullname, first, notes=None, is_gold=True, existing=None)
            )
            flipped = first | {"sarcastic": 1 - ensemble}
            existing = db.get_human_review(fullname)
            db.upsert_human_review(
                build_review_row(fullname, flipped, notes=None, is_gold=True, existing=existing)
            )
        db.commit()
        result = _gold_cohen_kappa(db)
    assert result["n_gold_items"] == 2
    assert result["sarcastic"] == 1.0
