"""Live run status for the app: progress, pace, ETA, stall detection, read-only."""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.test_annotate_aggregate import _ann
from tests.test_annotate_v3 import _source
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.fork import fork_for_reannotation
from uyam.annotate.prompts import PROMPT_VERSION
from uyam.annotate.run_status import discover_runs, run_status
from uyam.annotate.run_status_ui import status_label

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def _v3_run(tmp_path: Path) -> Any:
    """A forked v3-style DB: 6 source items (a1, a2 voted) + 2 June items; a3 is new."""
    src = _source(tmp_path)
    dst = tmp_path / "db" / "annotation-v3.sqlite3"
    fork_for_reannotation(
        src.db_path, dst, prompt_version=PROMPT_VERSION, keep_annotators=["a1", "a2"],
        extend={"2026-06": 2},
    )
    cfg = dataclasses.replace(src, db_path=dst)
    cfg.pipeline.target_items = 8
    return cfg


def _stamp(db_path: Path, model: str, when: datetime) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE llm_annotations SET created_at = ?, duration_ms = 2000 WHERE model_key = ?",
        (when.isoformat(), model),
    )
    conn.commit()
    conn.close()


def test_progress_rate_and_eta_for_a_running_lane(tmp_path: Path) -> None:
    cfg = _v3_run(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        for f in ("t1_c1", "t1_c2", "t1_c3"):
            db.insert_llm_annotation(_ann(f, "a3"))
        db.commit()
    _stamp(cfg.db_path, "a3", NOW - timedelta(minutes=1))
    _stamp(cfg.db_path, "a1", NOW - timedelta(days=60))
    _stamp(cfg.db_path, "a2", NOW - timedelta(days=60))

    status = run_status(cfg, now=NOW, window_minutes=15, job_running=lambda name: True)
    models = {m.key: m for m in status["models"]}
    assert status["target"] == 8
    assert (models["a1"].done, models["a1"].pending) == (6, 2)
    assert (models["a3"].done, models["a3"].pending) == (3, 5)
    assert models["a3"].rate_per_min == pytest.approx(0.2)  # 3 items in 15 min
    assert models["a3"].sec_per_item == 300.0
    assert models["a1"].sec_per_item == 2.0  # idle model: its past mean pace
    assert status["current"] == "a3"
    assert status["complete"] == 3  # items carrying all three votes
    assert status["running"] and not status["stalled"]
    # 5 x 300 s (a3) + 2 x 2 s (a1) + 2 x 2 s (a2)
    assert status["eta"] == NOW + timedelta(seconds=1508)
    assert status_label(status) == "🟢 running"


def test_stall_and_stopped_and_complete_states(tmp_path: Path) -> None:
    cfg = _v3_run(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        db.insert_llm_annotation(_ann("t1_c1", "a3"))
        db.commit()
    _stamp(cfg.db_path, "a3", NOW - timedelta(minutes=30))
    stalled = run_status(cfg, now=NOW, job_running=lambda name: True)
    assert stalled["stalled"] and stalled["current"] is None
    assert status_label(stalled).startswith("⚠️ stalled")
    stopped = run_status(cfg, now=NOW, job_running=lambda name: False)
    assert not stopped["stalled"] and stopped["eta"] is None
    assert status_label(stopped).startswith("⏸️ not running")

    with AnnotationDatabase(cfg.db_path) as db:
        targets = db.target_items(PROMPT_VERSION, 8)
        for f in targets:
            for key in ("a1", "a2", "a3"):
                if db.conn.execute(
                    "SELECT 1 FROM llm_annotations WHERE reddit_fullname = ? AND model_key = ?",
                    (f, key),
                ).fetchone() is None:
                    db.insert_llm_annotation(_ann(f, key))
        db.commit()
    done = run_status(cfg, now=NOW, job_running=lambda name: False)
    assert done["complete"] == 8
    assert status_label(done) == "✅ complete"


def test_status_never_writes_to_the_database(tmp_path: Path) -> None:
    cfg = _v3_run(tmp_path)
    before = cfg.db_path.read_bytes()
    run_status(cfg, now=NOW, job_running=lambda name: False)
    assert cfg.db_path.read_bytes() == before


def test_unpinned_databases_use_the_general_target_ranking(tmp_path: Path) -> None:
    src = _source(tmp_path)  # no target_priority rows
    src.pipeline.target_items = 6
    status = run_status(src, now=NOW, job_running=lambda name: False)
    assert status["target"] == 6
    assert {m.key: m.done for m in status["models"]} == {"a1": 6, "a2": 6, "a3": 6}


def test_discover_runs_lists_configs_whose_database_exists(tmp_path: Path) -> None:
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "annotation.sqlite3").write_bytes(b"")
    base = {
        "endpoints": {"local": "http://127.0.0.1:11434"},
        "annotators": [{"key": "a1", "model": "m", "endpoint": "local"}],
        "adjudicator": {"key": "adj", "model": "m", "endpoint": "local"},
    }
    for name, db in (("annotation", "annotation"), ("annotation-v3", "annotation-v3")):
        cfg = {**base, "db_path": (tmp_path / "db" / f"{db}.sqlite3").as_posix()}
        (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (tmp_path / "annotation-broken.yaml").write_text("annotators: []\n", encoding="utf-8")
    assert [p.name for p in discover_runs(tmp_path)] == ["annotation.yaml"]
    (tmp_path / "db" / "annotation-v3.sqlite3").write_bytes(b"")
    assert [p.name for p in discover_runs(tmp_path)] == ["annotation.yaml", "annotation-v3.yaml"]


def test_panel_renders_in_the_app(tmp_path: Path) -> None:
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    cfg = _v3_run(tmp_path)
    raw = {
        "db_path": cfg.db_path.as_posix(),
        "prompt_version": PROMPT_VERSION,
        "endpoints": {"local": "http://127.0.0.1:11434"},
        "annotators": [
            {"key": k, "model": f"m-{k}", "endpoint": "local"} for k in ("a1", "a2", "a3")
        ],
        "aggregation": {"sarcasm_rule": "majority", "tie_break": "confidence"},
        "pipeline": {"target_items": 8},
    }
    config_dir = tmp_path / "cfgs"
    config_dir.mkdir()
    (config_dir / "annotation-v3.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")

    def page(config_dir: str) -> None:
        from pathlib import Path

        from uyam.annotate.run_status_ui import render_run_status_panel

        render_run_status_panel(Path(config_dir))

    at = AppTest.from_function(page, args=(str(config_dir),), default_timeout=30)
    at.run()
    assert not at.exception
    text = "\n".join(str(m.value) for m in at.markdown)
    assert "annotation-v3.sqlite3" in text and "not running" in text
    assert at.dataframe  # the per-annotator table rendered
