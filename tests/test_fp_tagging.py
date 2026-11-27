"""False-positive tagging: which posts are listed, tag storage, summary, and the page."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests.annotate_fixtures import make_config, write_raw_corpus
from uyam.annotate.candidates import select_candidates
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.fp_tagging import (
    TAGS,
    false_positives,
    load_tags,
    model_votes,
    save_tag,
    summarize,
)

GUIDE = "guide-v1.1"


def _db(tmp_path: Path) -> tuple[Path, list[str]]:
    """Indexed fixture corpus; gold on four posts; the label flags three of them."""
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)
        names = db.eligible_fullnames()[:4]
        a, b, c, d = names
        for name, gio, label in ((a, 1, 1), (b, 0, 1), (c, 0, 1), (d, 0, 0)):
            db.enqueue_review(name, "gold")
            db.upsert_human_review(
                {"reddit_fullname": name, "sarcastic": 1 - gio, "is_gold": 1,
                 "first_pass_json": json.dumps({"sarcastic": gio})}
            )
            db.upsert_aggregate(
                {"reddit_fullname": name, "prompt_version": GUIDE, "sarcastic_final": label}
            )
            for key, vote in (("qwen3", label), ("sealion", 1), ("llamasealion", 0)):
                db.insert_llm_annotation(
                    {"reddit_fullname": name, "model_key": key, "prompt_version": GUIDE,
                     "role": "annotator", "sarcastic": vote, "confidence": 0.9,
                     "rationale": f"{key} says so", "language": "english",
                     "literal_sentiment": "neutral", "intended_sentiment": "neutral"}
                )
        db.commit()
    return cfg.db_path, names


def test_false_positives_use_first_pass_and_label(tmp_path: Path) -> None:
    db, (a, b, c, d) = _db(tmp_path)
    before = db.read_bytes()
    # The saved labels are flipped; the first pass decides: b and c are false positives.
    assert false_positives(db, GUIDE) == sorted([b, c])
    votes = model_votes(db, GUIDE, b)
    assert {v["model"] for v in votes} == {"qwen3", "sealion", "llamasealion"}
    assert db.read_bytes() == before  # read-only


def test_save_tag_is_atomic_validated_and_keeps_others(tmp_path: Path) -> None:
    path = tmp_path / "fp_tags.json"
    save_tag(path, "t1_x", ["H10", "D1"], " hate speech ")
    save_tag(path, "t1_y", ["H14"], "")
    save_tag(path, "t1_x", ["D1", "H10", "H10"], "revised")  # re-save replaces, dedups, orders
    tags = load_tags(path)
    assert tags["t1_x"]["tags"] == ["H10", "D1"] and tags["t1_x"]["note"] == "revised"
    assert tags["t1_y"]["tags"] == ["H14"]
    assert not path.with_suffix(".json.tmp").exists()
    with pytest.raises(ValueError, match="unknown tags"):
        save_tag(path, "t1_z", ["H99"], "")
    path.write_text("not json", encoding="utf-8")
    assert load_tags(path) == {}


def test_summarize_counts_tags_and_media() -> None:
    items = ["t1_a", "t1_b", "t1_c"]
    tags = {"t1_a": {"tags": ["H10", "D1"]}, "t1_b": {"tags": ["H10"]}, "t1_z": {"tags": ["H14"]}}
    media = {"t1_a": False, "t1_b": True, "t1_c": False}
    s = summarize(items, tags, media)
    assert s["false positives"] == 3 and s["tagged"] == 2
    assert s["per tag"]["H10"] == 2 and s["per tag"]["D1"] == 1 and s["per tag"]["H14"] == 0
    assert s["replies to unseen media (data flag)"] == 2
    assert set(s["per tag"]) == set(TAGS)


def test_page_saves_and_advances(tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    db, (_, b, c, _) = _db(tmp_path)
    config = tmp_path / "v5.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "db_path": db.as_posix(),
                "data_dir": (tmp_path / "data").as_posix(),
                "prompt_version": GUIDE,
                "endpoints": {"local": "http://127.0.0.1:11434"},
                "annotators": [
                    {"key": "qwen3", "model": "m", "endpoint": "local", "prompt": "guideline"}
                ],
                "aggregation": {"sarcasm_rule": "majority", "tie_break": "confidence"},
            }
        ),
        encoding="utf-8",
    )
    tags_path = tmp_path / "fp_tags.json"

    def page(cfg_path: str, tags: str) -> None:
        from pathlib import Path

        from uyam.annotate.fp_tagging_ui import render_fp_tagging

        render_fp_tagging(Path(cfg_path), Path(tags))

    at = AppTest.from_function(page, args=(str(config), str(tags_path)), default_timeout=30)
    at.run()
    assert not at.exception
    assert at.metric[0].value == "2"
    at.checkbox(key=f"fp_{b}_H10").check().run()
    at.button(key=f"fp_{b}_save").click().run()
    assert not at.exception
    assert load_tags(tags_path)[b]["tags"] == ["H10"]
    assert at.selectbox(key="fp_select").value == 1  # moved on to the next post
    assert at.metric[1].value == "1 / 2"
    assert c not in load_tags(tags_path)
