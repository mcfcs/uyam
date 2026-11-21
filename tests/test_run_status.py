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


# ---------------------------------------------------------------------------
# Runs & Datasets tab
# ---------------------------------------------------------------------------


def test_run_summaries_keep_the_original_and_the_new_run_apart(tmp_path: Path) -> None:
    from uyam.annotate.aggregate import run_aggregation
    from uyam.annotate.run_status import run_summary

    v3 = _v3_run(tmp_path)
    src = dataclasses.replace(v3, db_path=tmp_path / "src" / "db" / "annotation.sqlite3")
    run_aggregation(src, PROMPT_VERSION)  # the source already had labels; recompute is idempotent
    original = run_summary(src, job_running=lambda name: False)
    new = run_summary(v3, job_running=lambda name: name == "annotation-v3-run-all-local")
    assert original["run"] == "original run (dataset-v1 / v2)"
    assert (original["state"], original["labeled"], original["sarcastic"]) == ("labels ready", 6, 1)
    assert original["fork"] == {}
    assert new["run"] == "v3 run (annotation-v3.sqlite3)" and new["state"] == "running"
    assert new["labeled"] == 0 and new["items with votes"] == 6
    assert new["fork"]["kept_annotators"] == ["a1", "a2"]
    assert run_summary(v3, job_running=lambda name: False)["state"] == "in progress (stopped)"


def test_exported_datasets_lists_each_version_once(tmp_path: Path) -> None:
    import json

    from uyam.annotate.run_status import exported_datasets

    old = {
        "dataset_version": "v1", "created_at": "2026-09-15T11:56:40+00:00",
        "prompt_version": "sarc-v2", "uyam_commit": "039c3aa0000",
        "readiness": {"exported_rows": 15000, "sarcastic_positives": 1601,
                      "gold_items_labeled": 31, "gold_sarcastic_cohen_kappa": -0.15},
        "annotator_provenance": [
            {"model_key": "qwen3", "role": "annotator"},
            {"model_key": "qwen3", "role": "annotator"},
            {"model_key": "qwen3-32b", "role": "adjudicator"},
        ],
    }
    new = old | {"dataset_version": "v3", "aggregation": {
        "sarcasm_rule": "majority", "tie_break": "confidence",
        "annotators": ["qwen3", "sealion", "llamasealion"], "adjudicator": None}}
    (tmp_path / "dataset_card-v1.json").write_text(json.dumps(old), encoding="utf-8")
    (tmp_path / "dataset_card-v3.json").write_text(json.dumps(new), encoding="utf-8")
    (tmp_path / "dataset_card.json").write_text(json.dumps(new), encoding="utf-8")  # latest copy
    (tmp_path / "dataset-v1.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "uyam-dataset-v3.zip").write_bytes(b"x" * 2_000_000)
    rows = exported_datasets(tmp_path)
    assert [r["version"] for r in rows] == ["v1", "v3"]
    v1, v3 = rows
    assert v1["rules"] == "unanimous sarcasm, adjudicator qwen3-32b" and v1["annotators"] == "qwen3"
    assert v1["dataset file"] is True and v1["bundle MB"] is None and v1["uyam commit"] == "039c3aa"
    assert v3["rules"] == "majority vote, ties by confidence"
    assert v3["annotators"] == "qwen3, sealion, llamasealion" and v3["bundle MB"] == 2.0


def test_label_browser_filters_and_pages(tmp_path: Path) -> None:
    from uyam.annotate.runs_ui import label_page, latest_votes

    src = _source(tmp_path)  # 6 labeled Aug items, t1_c3 sarcastic
    rows, total = label_page(src)
    assert total == 6 and len(rows) == 6 and set(rows[0]) >= {"reddit_fullname", "text"}
    sarc, n = label_page(src, sarcastic_only=True)
    assert n == 1 and sarc[0]["reddit_fullname"] == "t1_c3" and sarc[0]["sarcastic"] is True
    assert label_page(src, month="2026-06")[1] == 0
    assert label_page(src, page=2)[0] == []
    votes = latest_votes(src, limit=4)
    assert len(votes) == 4 and all(v["model_key"] in {"a1", "a2", "a3"} for v in votes)


def test_runs_tab_renders_both_runs_and_the_datasets(tmp_path: Path) -> None:
    import json

    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    v3 = _v3_run(tmp_path)
    config_dir = tmp_path / "cfgs"
    config_dir.mkdir()
    lineup = [{"key": k, "model": f"m-{k}", "endpoint": "local"} for k in ("a1", "a2", "a3")]
    common = {"prompt_version": PROMPT_VERSION,
              "endpoints": {"local": "http://127.0.0.1:11434"}, "annotators": lineup}
    (config_dir / "annotation.yaml").write_text(yaml.safe_dump(common | {
        "db_path": (tmp_path / "src" / "db" / "annotation.sqlite3").as_posix(),
        "adjudicator": {"key": "adj", "model": "m-adj", "endpoint": "local"},
        "pipeline": {"target_items": 6}}), encoding="utf-8")
    (config_dir / "annotation-v3.yaml").write_text(yaml.safe_dump(common | {
        "db_path": v3.db_path.as_posix(),
        "aggregation": {"sarcasm_rule": "majority", "tie_break": "confidence"},
        "pipeline": {"target_items": 8}}), encoding="utf-8")
    annotated = tmp_path / "annotated"
    annotated.mkdir()
    (annotated / "dataset_card-v2.json").write_text(json.dumps(
        {"dataset_version": "v2", "created_at": "2026-11-21T16:34:08+00:00",
         "readiness": {"exported_rows": 6}}), encoding="utf-8")

    def page(config_dir: str, annotated: str) -> None:
        from pathlib import Path

        from uyam.annotate.runs_ui import render_runs_tab

        render_runs_tab(Path(config_dir), Path(annotated))

    at = AppTest.from_function(page, args=(str(config_dir), str(annotated)), default_timeout=30)
    at.run()
    assert not at.exception
    runs_table = at.dataframe[0].value
    assert list(runs_table["run"]) == [
        "original run (dataset-v1 / v2)",
        "v3 run (annotation-v3.sqlite3)",
    ]
    assert at.selectbox(key="runs_tab_choice").options == list(runs_table["run"])
    # The original run opens on its labels; the datasets table lists v2.
    assert any("t1_c3" in str(df.value) for df in at.dataframe)
    assert "v2" in str(at.dataframe[-1].value["version"].tolist())
    at.selectbox(key="runs_tab_choice").set_value("v3 run (annotation-v3.sqlite3)").run()
    assert not at.exception
    assert any("No final labels yet" in str(i.value) for i in at.info)
