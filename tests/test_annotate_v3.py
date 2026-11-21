"""dataset-v3: equal-size re-annotation (majority vote, confidence tie-break, no
adjudicator) in a forked database that never touches the source."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.annotate_fixtures import _comment, make_config, write_raw_corpus
from tests.test_annotate_aggregate import _ann, _seed
from uyam.annotate.aggregate import per_label_resolution, run_aggregation
from uyam.annotate.candidates import select_candidates
from uyam.annotate.config import AggregationConfig, load_annotation_config
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.fork import fork_for_reannotation, parse_extend
from uyam.annotate.prompts import PROMPT_VERSION

V3 = AggregationConfig(sarcasm_rule="majority", tie_break="confidence")


def _v3(cfg: Any, keys: tuple[str, ...] = ("a1", "a2", "a3")) -> Any:
    annotators = [a for a in cfg.annotators if a.key in keys]
    return dataclasses.replace(cfg, aggregation=V3, adjudicator=None, annotators=annotators)


# ---------------------------------------------------------------------------
# Aggregation rules
# ---------------------------------------------------------------------------


def test_majority_rule_ships_two_one_splits_and_never_escalates(tmp_path: Path) -> None:
    cfg = _v3(make_config(tmp_path))
    with AnnotationDatabase(cfg.db_path) as db:
        _seed(db)
    stats = run_aggregation(cfg, PROMPT_VERSION)
    assert stats["escalated_pending"] == 0 and stats["adjudicated"] == 0
    with AnnotationDatabase(cfg.db_path) as db:
        e = db.get_aggregate("t1_itemE")  # 2-1 yes: unanimity would escalate
        assert e is not None
        assert (e["sarcastic_final"], e["sarcasm_votes"]) == (1, "2-1")
        assert e["resolved_by"] == "majority"
        assert e["needs_adjudication"] == 0
        a = db.get_aggregate("t1_itemA")  # 1-2: the adjudicator's vote is ignored
        assert a is not None
        assert (a["sarcastic_final"], a["resolved_by"], a["needs_human"]) == (0, "majority", 0)
        assert a["language_final"] == "taglish"  # not the adjudicator's "tagalog"


def test_confidence_breaks_a_three_way_tie(tmp_path: Path) -> None:
    cfg = _v3(make_config(tmp_path))
    with AnnotationDatabase(cfg.db_path) as db:
        db.insert_llm_annotation(_ann("t1_tie", "a1", language="english", confidence=0.6))
        db.insert_llm_annotation(_ann("t1_tie", "a2", language="tagalog", confidence=0.9))
        db.insert_llm_annotation(_ann("t1_tie", "a3", language="taglish", confidence=0.7))
        # Equal confidence: config order decides (a1 before a3).
        db.insert_llm_annotation(_ann("t1_eq", "a1", literal="positive", confidence=0.8))
        db.insert_llm_annotation(_ann("t1_eq", "a2", literal="neutral", confidence=0.5))
        db.insert_llm_annotation(_ann("t1_eq", "a3", literal="negative", confidence=0.8))
    stats = run_aggregation(cfg, PROMPT_VERSION)
    assert stats["tiebreak_labels"] == 2
    with AnnotationDatabase(cfg.db_path) as db:
        tie = db.get_aggregate("t1_tie")
        eq = db.get_aggregate("t1_eq")
    assert tie is not None and eq is not None
    assert tie["language_final"] == "tagalog" and tie["resolved_by"] == "majority"
    assert eq["literal_final"] == "positive"


def test_only_the_configured_lineup_is_counted(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        # a1 and a2 say no; a3 (not in the v3 lineup) says yes three times over.
        db.insert_llm_annotation(_ann("t1_x", "a1", sarcastic=0))
        db.insert_llm_annotation(_ann("t1_x", "a2", sarcastic=0))
        db.insert_llm_annotation(_ann("t1_x", "a3", sarcastic=1))
    run_aggregation(_v3(cfg, keys=("a1", "a2")), PROMPT_VERSION)
    with AnnotationDatabase(cfg.db_path) as db:
        x = db.get_aggregate("t1_x")
    assert x is not None
    assert (x["sarcastic_final"], x["sarcasm_votes"], x["n_annotators"]) == (0, "2-0", 2)
    assert set(json.loads(str(x["votes_json"]))) == {"a1", "a2"}


def test_majority_rule_leaves_a_one_one_split_unresolved(tmp_path: Path) -> None:
    cfg = _v3(make_config(tmp_path))
    with AnnotationDatabase(cfg.db_path) as db:
        db.insert_llm_annotation(_ann("t1_half", "a1", sarcastic=1))
        db.insert_llm_annotation(_ann("t1_half", "a2", sarcastic=0))  # a3 failed
    run_aggregation(cfg, PROMPT_VERSION)
    with AnnotationDatabase(cfg.db_path) as db:
        half = db.get_aggregate("t1_half")
    assert half is not None
    assert half["sarcastic_final"] is None and half["resolved_by"] is None


def test_per_label_resolution_follows_the_rules() -> None:
    rows = [
        {"sarcastic": 1, "language": "english", "literal_sentiment": "positive",
         "intended_sentiment": "negative"},
        {"sarcastic": 1, "language": "tagalog", "literal_sentiment": "positive",
         "intended_sentiment": "negative"},
        {"sarcastic": 0, "language": "taglish", "literal_sentiment": "neutral",
         "intended_sentiment": "negative"},
    ]
    v2 = per_label_resolution(rows, [], None)
    assert v2["sarcastic"] == "unresolved" and v2["language"] == "unresolved"
    v3 = per_label_resolution(rows, [], None, sarcasm_rule="majority", tie_break="confidence")
    assert v3 == {
        "sarcastic": "majority",
        "language": "tiebreak",
        "literal_sentiment": "majority",
        "intended_sentiment": "unanimous",
    }


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, **extra: Any) -> Path:
    raw: dict[str, Any] = {
        "db_path": (tmp_path / "a.sqlite3").as_posix(),
        "prompt_version": PROMPT_VERSION,
        "endpoints": {"local": "http://127.0.0.1:11434"},
        "annotators": [{"key": "a1", "model": "m1", "endpoint": "local"}],
        **extra,
    }
    path = tmp_path / "annotation.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_adjudicator_is_optional_only_when_nothing_escalates(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must define an adjudicator"):
        load_annotation_config(_write_config(tmp_path))
    with pytest.raises(ValueError, match="must define an adjudicator"):
        load_annotation_config(_write_config(tmp_path, aggregation={"sarcasm_rule": "majority"}))
    cfg = load_annotation_config(
        _write_config(tmp_path, aggregation={"sarcasm_rule": "majority", "tie_break": "confidence"})
    )
    assert cfg.adjudicator is None and cfg.aggregation == V3
    with pytest.raises(ValueError, match="no adjudicator"):
        cfg.require_adjudicator()
    with pytest.raises(ValueError, match="sarcasm_rule"):
        load_annotation_config(_write_config(tmp_path, aggregation={"sarcasm_rule": "vote"}))


def test_the_repo_v3_config_loads_with_an_equal_size_local_lineup() -> None:
    repo = Path(__file__).resolve().parent.parent
    cfg = load_annotation_config(repo / "config" / "annotation-v3.yaml")
    assert cfg.adjudicator is None
    assert cfg.aggregation == V3
    assert cfg.annotator_keys == ["qwen3", "sealion", "llamasealion"]
    # Equal size and never the remote box (Gio's gaming PC; RULES §6).
    assert {a.endpoint for a in cfg.annotators} == {"local"}
    assert cfg.db_path.name == "annotation-v3.sqlite3"
    assert "remote" not in cfg.endpoints


# ---------------------------------------------------------------------------
# Fork
# ---------------------------------------------------------------------------


def _source(tmp_path: Path) -> Any:
    """Six labeled Aug items (3 annotators + adjudicator) and unlabeled June/Oct items."""
    cfg = make_config(tmp_path / "src")
    write_raw_corpus(cfg.data_dir)
    extra = [
        _comment(f"jun{i}", "sub1", "t3_sub1", 0, created_utc=f"2026-06-1{i}T10:00:00Z")
        for i in range(5)
    ] + [
        _comment(f"oct{i}", "sub1", "t3_sub1", 0, created_utc=f"2026-10-0{i + 1}T10:00:00Z")
        for i in range(2)
    ]
    out = cfg.data_dir / "raw" / "CasualPH" / "2026-06-10.jsonl"
    out.write_text("".join(json.dumps(r) + "\n" for r in extra), encoding="utf-8")
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)
        for f in ("t1_c1", "t1_c2", "t1_c3", "t1_c4", "t1_c5", "t3_sub1"):
            for key in ("a1", "a2", "a3"):
                db.insert_llm_annotation(_ann(f, key, sarcastic=int(f == "t1_c3")))
        db.insert_llm_annotation(_ann("t1_c3", "adj", role="adjudicator", sarcastic=1))
        # A kept model's vote under an older prompt version (a pilot) must go too.
        db.insert_llm_annotation(_ann("t1_c1", "a1") | {"prompt_version": "sarc-v0"})
        db.enqueue_review("t1_c2", "gold")
        db.upsert_human_review({"reddit_fullname": "t1_c2", "sarcastic": 0, "is_gold": 1})
        db.commit()
    run_aggregation(cfg, PROMPT_VERSION)
    return cfg


def _snapshot(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(path)
    try:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("llm_annotations", "aggregates", "human_reviews", "review_queue")
        }
    finally:
        conn.close()


def test_fork_keeps_the_lineup_gold_and_pins_the_target_set(tmp_path: Path) -> None:
    cfg = _source(tmp_path)
    before = _snapshot(cfg.db_path)
    dst = tmp_path / "v3.sqlite3"
    stats = fork_for_reannotation(
        cfg.db_path, dst, prompt_version=PROMPT_VERSION, keep_annotators=["a1", "a2"],
        extend={"2026-06": 3, "2026-10": 5}, seed=13,
    )
    assert _snapshot(cfg.db_path) == before  # the source is untouched
    assert stats["base"] == 6
    assert stats["extension"] == {"2026-06": 3, "2026-10": 2}
    assert stats["extension_short"] == {"2026-10": 3}
    assert stats["target_total"] == 11

    with AnnotationDatabase(dst) as db:
        keys = {(str(r[0]), str(r[1]), str(r[2])) for r in db.conn.execute(
            "SELECT DISTINCT model_key, role, prompt_version FROM llm_annotations")}
        assert keys == {("a1", "annotator", PROMPT_VERSION), ("a2", "annotator", PROMPT_VERSION)}
        assert db.conn.execute("SELECT COUNT(*) FROM aggregates").fetchone()[0] == 0
        gold = db.get_human_review("t1_c2")
        assert gold is not None and gold["is_gold"] == 1  # gold labels carry over
        target = db.target_items(PROMPT_VERSION, stats["target_total"])
        assert target[:6] == sorted(
            ["t1_c1", "t1_c2", "t1_c3", "t1_c4", "t1_c5", "t3_sub1"]
        )
        assert all(f.startswith(("t1_jun", "t1_oct")) for f in target[6:])
        # The new annotator's queue is the whole pinned set.
        assert len(db.pending_target_items("a3new", PROMPT_VERSION, stats["target_total"])) == 11
        info = {str(k): json.loads(str(v)) for k, v in db.conn.execute(
            "SELECT key, value FROM fork_info")}
    assert info["kept_annotators"] == ["a1", "a2"] and info["seed"] == 13

    # Same seed, same sample.
    again = fork_for_reannotation(
        cfg.db_path, tmp_path / "v3b.sqlite3", prompt_version=PROMPT_VERSION,
        keep_annotators=["a1", "a2"], extend={"2026-06": 3, "2026-10": 5}, seed=13,
    )
    assert again["extension"] == stats["extension"]
    a, b = (sqlite3.connect(p) for p in (dst, tmp_path / "v3b.sqlite3"))
    q = "SELECT reddit_fullname, priority FROM target_priority ORDER BY 1"
    assert a.execute(q).fetchall() == b.execute(q).fetchall()
    a.close()
    b.close()


def test_fork_refuses_to_overwrite(tmp_path: Path) -> None:
    cfg = _source(tmp_path)
    dst = tmp_path / "v3.sqlite3"
    dst.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        fork_for_reannotation(
            cfg.db_path, dst, prompt_version=PROMPT_VERSION, keep_annotators=["a1"], extend={}
        )
    assert dst.read_bytes() == b"existing"


def test_target_priority_survives_reselect(tmp_path: Path) -> None:
    cfg = _source(tmp_path)
    dst = tmp_path / "v3.sqlite3"
    fork_for_reannotation(
        cfg.db_path, dst, prompt_version=PROMPT_VERSION, keep_annotators=["a1", "a2"],
        extend={"2026-06": 2},
    )
    with AnnotationDatabase(dst) as db:
        before = db.target_items(PROMPT_VERSION, 8)
        select_candidates(db, cfg.candidate_filters)  # INSERT OR REPLACE on candidates
        assert db.target_items(PROMPT_VERSION, 8) == before


def test_parse_extend() -> None:
    assert parse_extend(["2026-06:1600", "2026-11:1200"]) == {"2026-06": 1600, "2026-11": 1200}
    for bad in (["2026-6:10"], ["2026-06:0"], ["2026-06"], ["2026-06:5", "2026-06:6"]):
        with pytest.raises(ValueError):
            parse_extend(bad)


# ---------------------------------------------------------------------------
# Pipeline: child jobs must run THIS config (never the default annotation.yaml)
# ---------------------------------------------------------------------------


def test_v3_pipeline_jobs_carry_the_config_path_and_their_own_names(tmp_path: Path) -> None:
    from tests.test_annotate_target import _FakeBackend
    from uyam.annotate.pipeline import run_pipeline

    base = make_config(tmp_path)
    source = tmp_path / "annotation-v3.yaml"
    source.write_text("# stand-in\n", encoding="utf-8")
    cfg = dataclasses.replace(
        _v3(base),
        db_path=tmp_path / "db" / "annotation-v3.sqlite3",
        source_path=source,
    )
    write_raw_corpus(cfg.data_dir)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)

    fake = _FakeBackend()
    run_pipeline(cfg, target=4, skip_prep=True, poll_seconds=0.0, backend=fake.backend())
    starts = [(name, args) for kind, name, args in fake.events if kind == "start"]
    waits = [name for kind, name, _ in fake.events if kind == "wait"]
    assert starts, "the pipeline started no jobs"
    for name, args in starts:
        # Without this a v3 lane would run the default config: main DB + remote box.
        assert args[-2:] == ["--config-path", str(source)], name
        assert name.startswith("annotation-v3-"), name
    assert not any("adjudicate" in name for name, _ in starts)  # nothing escalates
    assert set(waits) <= {name for name, _ in starts}  # waits use the same names


def test_default_config_jobs_keep_their_names(tmp_path: Path) -> None:
    from uyam.annotate.pipeline import _job

    cfg = dataclasses.replace(make_config(tmp_path), db_path=tmp_path / "annotation.sqlite3")
    assert _job(cfg, "run-all-local", ["run"]) == ("run-all-local", ["run"])
    loaded = load_annotation_config(_write_config(tmp_path, aggregation={
        "sarcasm_rule": "majority", "tie_break": "confidence"}))
    assert loaded.source_path == (tmp_path / "annotation.yaml").resolve()
