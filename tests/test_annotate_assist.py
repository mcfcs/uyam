"""Loading reviewer-assist suggestions: validation first, then one write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.annotate_fixtures import make_config, write_raw_corpus
from uyam.annotate.assist import AssistValidationError, load_assist_suggestions
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase


def _db(tmp_path: Path) -> AnnotationDatabase:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    db = AnnotationDatabase(cfg.db_path)
    index_corpus(db, cfg.data_dir)
    return db


def _write(path: Path, items: list[dict[str, object]]) -> Path:
    path.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n", "utf-8")
    return path


def _ok(fullname: str, text: str, sarcastic: bool) -> dict[str, object]:
    return {
        "reddit_fullname": fullname,
        "sarcastic": sarcastic,
        "sarcastic_span": text if sarcastic else None,
        "rationale": "Says: x; Means: y (§3).",
        "confidence": 0.7,
    }


def test_loads_valid_suggestions(tmp_path: Path) -> None:
    with _db(tmp_path) as db:
        c3 = db.get_record("t1_c3")
        assert c3 is not None
        span = str(c3["text"]).split(".")[0]  # an exact substring of the target
        f = _write(tmp_path / "a.jsonl", [_ok("t1_c3", span, True), _ok("t1_c1", "", False)])
        stats = load_assist_suggestions(db, [f], source="test", guideline_version="g1")
        assert stats == {"loaded": 2, "sarcastic": 1, "files": 1}
        row = db.conn.execute(
            "SELECT * FROM assist_suggestions WHERE reddit_fullname = 't1_c3'"
        ).fetchone()
        assert (row["sarcastic"], row["sarcastic_span"], row["source"]) == (1, span, "test")
        # Re-loading replaces, never duplicates.
        load_assist_suggestions(db, [f], source="test2", guideline_version="g1")
        assert db.conn.execute("SELECT COUNT(*) FROM assist_suggestions").fetchone()[0] == 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"sarcastic_span": "not in the text at all"}, "exact substring"),
        ({"sarcastic": False}, "sarcastic and sarcastic_span disagree"),
        ({"confidence": 1.5}, "confidence"),
        ({"reddit_fullname": "t1_missing"}, "not in the corpus"),
        ({"rationale": ""}, "rationale"),
    ],
)
def test_invalid_files_write_nothing(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    with _db(tmp_path) as db:
        c3 = db.get_record("t1_c3")
        assert c3 is not None
        good = _ok("t1_c1", "", False)
        bad = {**_ok("t1_c3", str(c3["text"]).split(".")[0], True), **change}
        f = _write(tmp_path / "a.jsonl", [good, bad])
        with pytest.raises(AssistValidationError, match=message) as excinfo:
            load_assist_suggestions(db, [f], source="t", guideline_version="g")
        assert db.conn.execute("SELECT COUNT(*) FROM assist_suggestions").fetchone()[0] == 0
        # Errors name the line, never the span text (it would hint at the verdict).
        assert str(bad["sarcastic_span"]) not in str(excinfo.value)


def test_duplicate_items_across_files_are_rejected(tmp_path: Path) -> None:
    with _db(tmp_path) as db:
        a = _write(tmp_path / "a.jsonl", [_ok("t1_c1", "", False)])
        b = _write(tmp_path / "b.jsonl", [_ok("t1_c1", "", False)])
        with pytest.raises(AssistValidationError, match="appears twice"):
            load_assist_suggestions(db, [a, b], source="t", guideline_version="g")
