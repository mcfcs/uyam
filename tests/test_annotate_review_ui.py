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
                # Never the real data/annotated (media side files live there).
                "export": {"out_dir": (tmp_path / "annotated").as_posix()},
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
    # Not sarcastic: no intended choice (it follows literal) and the cues are off.
    assert all(at.checkbox(key=f"review_t1_c3_{c}").disabled for c in (
        "cue_polarity_inversion", "cue_hyperbole"))
    assert not any(r.key == "review_t1_c3_intended" for r in at.radio)
    at.radio(key="review_t1_c3_sarcastic").set_value("yes").run()
    assert not at.checkbox(key="review_t1_c3_cue_polarity_inversion").disabled
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


# ---------------------------------------------------------------------------
# votes_seen, first_reviewed_at, and every gold score on the blind first pass
# ---------------------------------------------------------------------------


def test_build_review_row_records_votes_seen_only_on_the_first_save() -> None:
    first = dict.fromkeys(LABEL_KEYS) | {"sarcastic": 0}
    row = build_review_row("t1_x", first, notes=None, is_gold=True, existing=None, votes_seen=True)
    assert row["votes_seen"] == 1 and row["first_reviewed_at"] is None

    stored = row | {"reviewed_at": "2026-11-19T01:00:00+00:00"}
    again = build_review_row("t1_x", first, notes=None, is_gold=True, existing=stored)
    # Revising can neither clear nor set it, and the first-save time carries forward.
    assert again["votes_seen"] == 1
    assert again["first_reviewed_at"] == "2026-11-19T01:00:00+00:00"
    blind = build_review_row(
        "t1_x", first, notes=None, is_gold=True, existing=stored | {"votes_seen": 0},
        votes_seen=True,
    )
    assert blind["votes_seen"] == 0


def test_first_reviewed_at_survives_revisions(tmp_path: Path) -> None:
    _, db_path = _setup(tmp_path, {"t1_c3": "gold"}, assists=())
    with AnnotationDatabase(db_path) as db:
        labels = dict.fromkeys(LABEL_KEYS) | {"sarcastic": 0}
        db.upsert_human_review(
            build_review_row("t1_c3", labels, notes=None, is_gold=True, existing=None)
        )
        first = db.get_human_review("t1_c3")
        assert first is not None and first["first_reviewed_at"] == first["reviewed_at"]
        db.conn.execute(
            "UPDATE human_reviews SET first_reviewed_at = '2026-01-01T00:00:00+00:00', "
            "reviewed_at = '2026-01-01T00:00:00+00:00'"
        )
        existing = db.get_human_review("t1_c3")
        db.upsert_human_review(
            build_review_row(
                "t1_c3", labels | {"sarcastic": 1}, notes=None, is_gold=True, existing=existing
            )
        )
        revised = db.get_human_review("t1_c3")
    assert revised is not None
    assert revised["first_reviewed_at"] == "2026-01-01T00:00:00+00:00"
    assert revised["reviewed_at"] != "2026-01-01T00:00:00+00:00"


def test_revealing_votes_before_the_first_save_is_recorded(tmp_path: Path) -> None:
    config_yaml, db_path = _setup(tmp_path, {"t1_c3": "gold", "t1_c2": "gold"}, assists=())
    head, other = "t1_c2", "t1_c3"  # the queue is served in fullname order
    with AnnotationDatabase(db_path) as db:
        db.upsert_aggregate(
            {"reddit_fullname": head, "prompt_version": PROMPT_VERSION,
             "sarcasm_votes": "0-3", "votes_json": "{}"}
        )
        db.commit()
    at = _app(config_yaml)
    # Tick, then untick: the votes were seen, so the first pass is not blind.
    at.checkbox(key=f"votes_{head}").check().run()
    assert at.json  # the votes rendered
    at.checkbox(key=f"votes_{head}").uncheck().run()
    _save(at)
    row = _review(db_path, head)
    assert row is not None and row["votes_seen"] == 1

    at.button(key=f"next_{head}").click().run()
    _save(at)
    blind_row = _review(db_path, other)
    assert blind_row is not None and blind_row["votes_seen"] == 0
    with AnnotationDatabase(db_path) as db:
        labels, n_votes_seen = db.blind_gold_labels()
    assert set(labels) == {other} and n_votes_seen == 1


def test_revising_one_sentence_no_to_yes_ticks_the_sentence(tmp_path: Path) -> None:
    config_yaml, db_path = _setup(tmp_path, {"t1_c3": "gold"}, assists=())
    at = _app(config_yaml)
    _save(at)  # defaults: sarcastic = no -> span NULL
    at.button(key="revise_btn_t1_c3").click().run()
    assert at.checkbox(key="revise_t1_c3_span_0").value is True  # GUIDELINE T2
    at.radio(key="revise_t1_c3_sarcastic").set_value("yes")
    _save(at)
    row = _review(db_path, "t1_c3")
    assert row is not None
    assert row["sarcastic_span"] == "Wow ang galing naman ng serbisyo nila dito."


def test_eval_report_and_lid_accuracy_use_the_blind_first_pass(tmp_path: Path) -> None:
    from uyam.annotate.aggregate import _lid_vs_human_gold
    from uyam.annotate.config import load_annotation_config
    from uyam.annotate.evaluation import build_eval_report

    config_yaml, db_path = _setup(tmp_path, {"t1_c3": "gold", "t1_c4": "gold"}, assists=())
    with AnnotationDatabase(db_path) as db:
        for fullname, value in (("t1_c3", 1), ("t1_c4", 0)):
            db.upsert_aggregate(
                {"reddit_fullname": fullname, "prompt_version": PROMPT_VERSION,
                 "sarcastic_final": value}
            )
            db.upsert_lid(fullname, {"language": "taglish"})
            # Blind first pass: matches the shipped label and the LID language.
            first = dict.fromkeys(LABEL_KEYS) | {"sarcastic": value, "language": "taglish"}
            db.upsert_human_review(
                build_review_row(fullname, first, notes=None, is_gold=True, existing=None)
            )
            existing = db.get_human_review(fullname)
            # The post-assist revision disagrees on everything.
            revised = first | {"sarcastic": 1 - value, "language": "english"}
            db.upsert_human_review(
                build_review_row(fullname, revised, notes=None, is_gold=True, existing=existing)
            )
        db.commit()
        lid = _lid_vs_human_gold(db)
    assert lid == {"n": 2, "accuracy": 1.0}

    report = build_eval_report(load_annotation_config(config_yaml), PROMPT_VERSION)
    [shipped] = [r for r in report["vs_human"] if r["kind"] == "final"]
    assert shipped["n"] == 2 and shipped["accuracy"] == 1.0


# ---------------------------------------------------------------------------
# Opt-in "second judgement": Claude's suggestion before the first save
# ---------------------------------------------------------------------------


def test_upfront_toggle_shows_the_suggestion_and_records_it(tmp_path: Path) -> None:
    config_yaml, db_path = _setup(tmp_path, {"t1_c3": "gold", "t1_c4": "gold"}, assists=("t1_c3",))
    at = _app(config_yaml)
    _assert_no_assist_leak(at)  # off by default: still a blind first pass
    at.toggle(key="review_assist_upfront").set_value(True).run()
    assert not at.exception
    text = _rendered_text(at)
    assert RATIONALE in text and "Verdict:** sarcastic" in text  # before any save
    assert "Claude-informed" in text
    _save(at)
    row = _review(db_path, "t1_c3")
    assert row is not None
    assert row["assist_before_first_pass"] == 1 and row["assist_seen"] == 1

    # With the toggle on, saving goes straight to the next item (no saved page
    # repeating Claude's take). It has no suggestion: NOT recorded as assisted.
    text = _rendered_text(at)
    assert "Saved t1_c3" in text and "Label saved." not in text
    assert "No Claude suggestion for this item." in text
    _save(at)
    other = _review(db_path, "t1_c4")
    assert other is not None and other["assist_before_first_pass"] == 0


def test_upfront_flag_is_kept_on_revision_and_counted_in_kappa(tmp_path: Path) -> None:
    from uyam.annotate.aggregate import _gold_cohen_kappa

    _, db_path = _setup(tmp_path, {"t1_c3": "gold"}, assists=("t1_c3",))
    with AnnotationDatabase(db_path) as db:
        first = dict.fromkeys(LABEL_KEYS) | {"sarcastic": 1}
        db.upsert_human_review(
            build_review_row("t1_c3", first, notes=None, is_gold=True, existing=None,
                             assist_upfront=True)
        )
        existing = db.get_human_review("t1_c3")
        db.upsert_human_review(
            build_review_row("t1_c3", first | {"sarcastic": 0}, notes=None, is_gold=True,
                             existing=existing)
        )
        row = db.get_human_review("t1_c3")
        assert row is not None and row["assist_before_first_pass"] == 1
        db.upsert_aggregate({"reddit_fullname": "t1_c3", "prompt_version": PROMPT_VERSION,
                             "sarcastic_final": 1})
        db.commit()
        assert _gold_cohen_kappa(db)["n_first_pass_with_assist"] == 1


def test_label_site_renders_only_the_review_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_yaml, _ = _setup(tmp_path, {"t1_c3": "gold"}, assists=())
    import uyam.annotate.config as annotate_config

    monkeypatch.setattr(annotate_config, "_DEFAULT_CONFIG_PATH", config_yaml)
    app = Path(__file__).resolve().parent.parent / "src" / "uyam" / "label_app.py"
    at = AppTest.from_file(str(app), default_timeout=30)
    at.run()
    assert not at.exception
    assert not at.tabs  # no other tabs: nothing else runs on a click
    assert "Wow ang galing naman" in _rendered_text(at)  # the queue's item is on screen


# ---------------------------------------------------------------------------
# Form rules: "not sarcastic" disables cues + span; intended follows literal
# ---------------------------------------------------------------------------


def test_not_sarcastic_clears_cues_and_span_and_mirrors_intended(tmp_path: Path) -> None:
    config_yaml, db_path = _setup(tmp_path, {"t1_c3": "gold"}, assists=())
    at = _app(config_yaml)
    # Tick a cue while sarcastic, then change mind to "no": the cue must not be saved.
    at.radio(key="review_t1_c3_sarcastic").set_value("yes").run()
    at.checkbox(key="review_t1_c3_cue_hyperbole").check().run()
    at.radio(key="review_t1_c3_intended").set_value("negative").run()
    at.radio(key="review_t1_c3_sarcastic").set_value("no").run()
    assert at.checkbox(key="review_t1_c3_cue_hyperbole").disabled
    assert at.checkbox(key="review_t1_c3_span_0").disabled
    at.radio(key="review_t1_c3_literal").set_value("positive")
    _save(at)
    row = _review(db_path, "t1_c3")
    assert row is not None
    assert row["sarcastic"] == 0 and row["sarcastic_span"] is None
    assert row["cue_hyperbole"] == 0 and row["cue_polarity_inversion"] == 0
    assert row["intended_sentiment"] == "positive"  # follows literal (S3)


def test_intended_can_differ_when_ticked(tmp_path: Path) -> None:
    config_yaml, db_path = _setup(tmp_path, {"t1_c3": "gold"}, assists=())
    at = _app(config_yaml)
    at.checkbox(key="review_t1_c3_intended_differs").check().run()
    at.radio(key="review_t1_c3_literal").set_value("neutral")
    at.radio(key="review_t1_c3_intended").set_value("negative")
    _save(at)
    row = _review(db_path, "t1_c3")
    assert row is not None
    assert (row["literal_sentiment"], row["intended_sentiment"]) == ("neutral", "negative")


def test_upfront_toggle_prefills_the_form_with_claudes_full_take(tmp_path: Path) -> None:
    config_yaml, db_path = _setup(tmp_path, {"t1_c3": "gold"}, assists=("t1_c3",))
    with AnnotationDatabase(db_path) as db:
        db.conn.execute(
            "UPDATE assist_suggestions SET language = 'tagalog', literal_sentiment = 'positive', "
            "intended_sentiment = 'negative', cue_polarity_inversion = 1, "
            "cue_rhetorical_intent = 0, cue_contextual_incongruity = 1, cue_hyperbole = 0, "
            "sarcastic_span = ? WHERE reddit_fullname = 't1_c3'",
            ("Wow ang galing naman ng serbisyo nila dito.",),
        )
        db.commit()
    at = _app(config_yaml)
    at.toggle(key="review_assist_upfront").set_value(True).run()
    text = _rendered_text(at)
    assert "**Language:** tagalog" in text and "literal positive → intended negative" in text
    # The form starts from Claude's take...
    assert at.radio(key="reviewc_t1_c3_sarcastic").value == "yes"
    assert at.radio(key="reviewc_t1_c3_language").value == "tagalog"
    assert at.radio(key="reviewc_t1_c3_intended").value == "negative"
    assert at.checkbox(key="reviewc_t1_c3_cue_contextual_incongruity").value is True
    assert at.checkbox(key="reviewc_t1_c3_span_0").value is True
    # ...and saving it unchanged stores Claude's labels as the (Claude-informed) first pass.
    [submit] = [b for b in at.button if b.label == "Save label"]
    submit.click().run()
    row = _review(db_path, "t1_c3")
    assert row is not None
    assert (row["sarcastic"], row["language"]) == (1, "tagalog")
    assert row["intended_sentiment"] == "negative"
    assert (row["cue_polarity_inversion"], row["cue_contextual_incongruity"]) == (1, 1)
    assert row["sarcastic_span"] == "Wow ang galing naman ng serbisyo nila dito."
    assert row["assist_before_first_pass"] == 1


def test_assist_defaults_handles_old_and_full_suggestions() -> None:
    from uyam.annotate.review_workflow import assist_defaults

    old = assist_defaults({"sarcastic": 0, "sarcastic_span": None})
    assert old is not None and old["sarcastic"] == 0 and old["language"] is None
    assert old["cue_hyperbole"] is None
    full = assist_defaults({"sarcastic": 0, "sarcastic_span": "x", "language": "english",
                            "cue_hyperbole": 1})
    assert full is not None and full["sarcastic_span"] is None and full["cue_hyperbole"] == 0
    assert assist_defaults(None) is None



# ---------------------------------------------------------------------------
# Media indicator: unseen images flagged; the Reddit link waits for the save
# ---------------------------------------------------------------------------


def test_describe_media_flags() -> None:
    from uyam.annotate.media import describe

    assert describe(None) is None
    level, text = describe({"post_type": "text", "text_only": True}) or ("", "")
    assert level == "info" and "Text-only" in text
    level, text = describe({"post_type": "image", "text_only": False}) or ("", "")
    assert level == "warning" and "image" in text and "Unsure" in text
    _, text = describe({"post_type": "text", "text_only": False, "parent_has_media": True}) or (
        "", "")
    assert "parent comment has an image" in text


def test_media_item_shows_a_warning_and_hides_the_link_until_saved(tmp_path: Path) -> None:
    config_yaml, db_path = _setup(tmp_path, {"t1_c3": "gold"}, assists=())
    side = tmp_path / "annotated" / "media_context-v2.jsonl"
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps({"reddit_fullname": "t1_c3", "media_context": {
        "post_type": "image", "is_post": False, "post_body_has_media": False,
        "parent_has_media": False, "target_has_media": False, "text_only": False}}),
        encoding="utf-8")
    at = _app(config_yaml)
    warnings = " ".join(str(w.value) for w in at.warning)
    assert "image" in warnings and "Unsure" in warnings
    text = _rendered_text(at)
    assert "view on reddit" not in text and "appears after you save" in text
    _save(at)
    assert "view on reddit" in _rendered_text(at)  # after the first pass


def test_text_only_item_keeps_the_link_and_says_so(tmp_path: Path) -> None:
    config_yaml, _ = _setup(tmp_path, {"t1_c3": "gold"}, assists=())  # no side file: computed
    at = _app(config_yaml)
    text = _rendered_text(at)
    assert "Text-only thread" in text and "view on reddit" in text
    assert not at.warning



def test_blind_save_still_stays_on_the_item(tmp_path: Path) -> None:
    config_yaml, _ = _setup(tmp_path, {"t1_c3": "gold", "t1_c4": "gold"}, assists=("t1_c3",))
    at = _app(config_yaml)  # toggle off
    _save(at)
    text = _rendered_text(at)
    assert "Label saved." in text and "Show Claude's take" in text
