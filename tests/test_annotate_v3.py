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
from uyam.annotate.evaluation import import_eval_votes, insert_eval_annotation
from uyam.annotate.fork import (
    fork_extend,
    fork_for_reannotation,
    fork_subset,
    parse_extend,
    sync_gold,
)
from uyam.annotate.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from uyam.annotate.prompts_guideline import (
    GUIDE_FEW_SHOTS,
    GUIDE_PROMPT_VERSION,
    GUIDE_SYSTEM_PROMPT,
    dataset_prompt_set,
)
from uyam.annotate.runner import run_annotator

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


def test_the_repo_v4_config_is_the_v3_lineup_on_the_guideline_prompt() -> None:
    repo = Path(__file__).resolve().parent.parent
    v3 = load_annotation_config(repo / "config" / "annotation-v3.yaml")
    v4 = load_annotation_config(repo / "config" / "annotation-v4.yaml")
    assert v4.prompt_version == GUIDE_PROMPT_VERSION
    assert {a.prompt for a in v4.annotators} == {"guideline"}
    same = [(a.key, a.model, a.think, a.merge_system_prompt) for a in v4.annotators]
    assert same == [(a.key, a.model, a.think, a.merge_system_prompt) for a in v3.annotators]
    assert v4.options == v3.options and v4.context == v3.context
    assert v4.aggregation == V3 and v4.adjudicator is None
    assert {a.endpoint for a in v4.annotators} == {"local"} and "remote" not in v4.endpoints
    assert v4.db_path.name == "annotation-v4.sqlite3"
    assert v4.pipeline.target_items == 5000
    # The definition-test evaluators it reuses votes from match it exactly.
    main = load_annotation_config(repo / "config" / "annotation.yaml")
    pairs = {"qwen3": "qwen3-guide", "sealion": "sealion-guide", "llamasealion": "llama-guide"}
    for ann_key, ev_key in pairs.items():
        ann, ev = v4.annotator(ann_key), main.evaluator(ev_key)
        assert (ann.model, ann.think, ann.merge_system_prompt, ann.prompt) == (
            ev.model, ev.think, ev.merge_system_prompt, ev.prompt
        )
    assert main.options == v4.options


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


def test_sync_gold_copies_new_reviews_and_never_touches_votes(tmp_path: Path) -> None:
    cfg = _source(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        db.enqueue_review("t1_c4", "gold")
        db.commit()
    dst = tmp_path / "v3.sqlite3"
    fork_for_reannotation(
        cfg.db_path, dst, prompt_version=PROMPT_VERSION, keep_annotators=["a1"], extend={}
    )
    with AnnotationDatabase(dst) as db:
        votes_before = db.conn.execute("SELECT COUNT(*) FROM llm_annotations").fetchone()[0]
        target_before = db.conn.execute("SELECT COUNT(*) FROM target_priority").fetchone()[0]
    src_before = _snapshot(cfg.db_path)
    # Gio keeps labeling in the main DB after the fork: one new, one revised review.
    with AnnotationDatabase(cfg.db_path) as db:
        db.upsert_human_review(
            {"reddit_fullname": "t1_c4", "sarcastic": 1, "is_gold": 1, "notes": "new"}
        )
        db.upsert_human_review(
            {"reddit_fullname": "t1_c2", "sarcastic": 1, "is_gold": 1, "notes": "revised"}
        )
        db.conn.execute("UPDATE review_queue SET completed = 1")
        db.conn.execute(
            "INSERT INTO assist_suggestions (reddit_fullname, source, sarcastic, created_at) "
            "VALUES ('t1_c4', 'claude-code:test', 1, '2026-11-23T00:00:00Z')"
        )
        db.commit()
    src_after = _snapshot(cfg.db_path)

    stats = sync_gold(cfg.db_path, dst)
    assert _snapshot(cfg.db_path) == src_after  # the source is read-only
    assert src_after["human_reviews"] == src_before["human_reviews"] + 1
    assert stats["reviews_added"] == 1 and stats["reviews_updated"] == 1
    assert stats["gold_completed"] == 2 and stats["assist_copied"] == 1
    with AnnotationDatabase(dst) as db:
        new = db.get_human_review("t1_c4")
        old = db.get_human_review("t1_c2")
        assert new is not None and new["sarcastic"] == 1 and new["notes"] == "new"
        assert old is not None and old["notes"] == "revised"
        assert db.conn.execute("SELECT COUNT(*) FROM llm_annotations").fetchone()[0] == votes_before
        n_target = db.conn.execute("SELECT COUNT(*) FROM target_priority").fetchone()[0]
        assert n_target == target_before
        assert db.conn.execute(
            "SELECT COUNT(*) FROM review_queue WHERE completed = 1"
        ).fetchone()[0] == 2

    again = sync_gold(cfg.db_path, dst)  # re-running is a no-op
    assert again["reviews_added"] == again["reviews_updated"] == 0
    assert again["reviews_unchanged"] == 2 and again["assist_copied"] == 0


def test_sync_gold_refuses_different_gold_queues(tmp_path: Path) -> None:
    cfg = _source(tmp_path)
    dst = tmp_path / "v3.sqlite3"
    fork_for_reannotation(
        cfg.db_path, dst, prompt_version=PROMPT_VERSION, keep_annotators=["a1"], extend={}
    )
    with AnnotationDatabase(cfg.db_path) as db:
        db.enqueue_review("t1_c5", "gold")
        db.commit()
    with pytest.raises(ValueError, match="gold queues differ"):
        sync_gold(cfg.db_path, dst)


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


# ---------------------------------------------------------------------------
# Guideline-prompt datasets (the v4 pilot)
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Answers every call with a fixed label and keeps the prompts it saw."""

    def __init__(self) -> None:
        from tests.test_annotate_runner import FakeClient

        self._fake = FakeClient()
        self.calls: list[tuple[str, str]] = []

    def annotate(self, annotator: Any, system_prompt: str, user_prompt: str, opts: Any) -> Any:
        self.calls.append((system_prompt, user_prompt))
        return self._fake.annotate(annotator, system_prompt, user_prompt, opts)

    def server_version(self) -> str | None:
        return None

    def model_digest(self, model: str) -> str | None:
        return None


def test_guideline_dataset_labels_with_the_guideline_and_the_sarc_v2_context(
    tmp_path: Path,
) -> None:
    cfg = _source(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        contexts_before = db.conn.execute(
            "SELECT reddit_fullname, prompt_version, context_text FROM contexts ORDER BY 1"
        ).fetchall()
        sarc_votes = db.conn.execute(
            "SELECT COUNT(*) FROM llm_annotations WHERE prompt_version = ?", (PROMPT_VERSION,)
        ).fetchone()[0]
    guide = dataclasses.replace(cfg, prompt_version=GUIDE_PROMPT_VERSION)
    client = _RecordingClient()

    stats = run_annotator(guide, "a1", target=3, client_factory=lambda c, a: client)
    assert stats["done"] == 3
    assert {system for system, _ in client.calls} == {GUIDE_SYSTEM_PROMPT}
    assert all(GUIDE_FEW_SHOTS in user for _, user in client.calls)
    assert all(SYSTEM_PROMPT not in system for system, _ in client.calls)
    with AnnotationDatabase(cfg.db_path) as db:
        rows = db.conn.execute(
            "SELECT prompt_version, COUNT(*) FROM llm_annotations GROUP BY 1"
        ).fetchall()
        counts = {str(r[0]): int(r[1]) for r in rows}
        assert counts[GUIDE_PROMPT_VERSION] == 3
        assert counts[PROMPT_VERSION] == sarc_votes  # sarc-v2 votes untouched
        # The context snapshots are reused as they are, never rebuilt or re-keyed.
        contexts_after = db.conn.execute(
            "SELECT reddit_fullname, prompt_version, context_text FROM contexts ORDER BY 1"
        ).fetchall()
        assert [tuple(r) for r in contexts_after][: len(contexts_before)] == [
            tuple(r) for r in contexts_before
        ]
        assert {str(r[1]) for r in contexts_after} <= {PROMPT_VERSION}
        hashes = {
            str(r[0]): str(r[1])
            for r in db.conn.execute("SELECT prompt_version, content_hash FROM prompt_versions")
        }
    assert hashes[GUIDE_PROMPT_VERSION] == dataset_prompt_set(GUIDE_PROMPT_VERSION).content_hash
    # Its target set counts only guide-v1.1 votes: a2 still has all 3 to do.
    with AnnotationDatabase(cfg.db_path) as db:
        assert len(db.pending_target_items("a2", GUIDE_PROMPT_VERSION, 3)) == 3


def test_guideline_prompt_hash_is_guarded(tmp_path: Path) -> None:
    cfg = _source(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        db.check_prompt_version(GUIDE_PROMPT_VERSION, "0" * 64)  # an older guideline text
    guide = dataclasses.replace(cfg, prompt_version=GUIDE_PROMPT_VERSION)
    with pytest.raises(RuntimeError, match="Prompt content changed"):
        run_annotator(guide, "a1", target=1, client_factory=lambda c, a: _RecordingClient())


def test_unknown_prompt_version_is_refused(tmp_path: Path) -> None:
    cfg = _source(tmp_path)
    with pytest.raises(RuntimeError, match="matches neither"):
        run_annotator(
            dataclasses.replace(cfg, prompt_version="sarc-v9"),
            "a1",
            target=1,
            client_factory=lambda c, a: _RecordingClient(),
        )


def _target(path: Path) -> list[tuple[str, int, str]]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT reddit_fullname, priority, reason FROM target_priority ORDER BY 1"
        ).fetchall()
        return [(str(r[0]), int(r[1]), str(r[2])) for r in rows]
    finally:
        conn.close()


def test_fork_subset_keeps_all_gold_samples_the_rest_and_drops_every_vote(
    tmp_path: Path,
) -> None:
    cfg = _source(tmp_path)
    before = _snapshot(cfg.db_path)
    dst = tmp_path / "v4.sqlite3"
    stats = fork_subset(cfg.db_path, dst, base_prompt_version=PROMPT_VERSION, size=4, seed=13)
    assert _snapshot(cfg.db_path) == before  # the source is untouched
    assert stats["gold"] == 1 and stats["sampled"] == 3 and stats["target_total"] == 4
    target = _target(dst)
    assert ("t1_c2", 2, "gold") in target
    assert len(target) == 4
    labeled = {"t1_c1", "t1_c3", "t1_c4", "t1_c5", "t3_sub1"}
    assert {f for f, prio, _ in target if prio == 1} <= labeled  # only source-labeled items
    after = _snapshot(dst)
    assert after["llm_annotations"] == 0 and after["aggregates"] == 0  # labels from scratch
    assert after["human_reviews"] == before["human_reviews"]  # gold labels carry over

    # Same seed, same subset; the whole source when size covers it.
    fork_subset(cfg.db_path, tmp_path / "v4b.sqlite3", base_prompt_version=PROMPT_VERSION,
                size=4, seed=13)
    assert _target(tmp_path / "v4b.sqlite3") == target
    full = fork_subset(cfg.db_path, tmp_path / "v4c.sqlite3",
                       base_prompt_version=PROMPT_VERSION, size=6)
    assert full["sampled"] == 5 and full["sampled_sarcastic"] == 1


def test_fork_subset_refuses_impossible_sizes(tmp_path: Path) -> None:
    cfg = _source(tmp_path)
    with pytest.raises(ValueError, match="needs 6 non-gold items"):
        fork_subset(cfg.db_path, tmp_path / "a.sqlite3", base_prompt_version=PROMPT_VERSION,
                    size=7)
    with pytest.raises(ValueError, match="smaller than the 1 gold"):
        fork_subset(cfg.db_path, tmp_path / "b.sqlite3", base_prompt_version=PROMPT_VERSION,
                    size=0)


def test_import_eval_votes_reuses_gold_votes_in_a_separate_pilot_db(tmp_path: Path) -> None:
    cfg = _source(tmp_path)
    with AnnotationDatabase(cfg.db_path) as db:
        for fullname in ("t1_c2", "t1_jun0"):  # a gold item, and one outside the pilot
            row = _ann(fullname, "e1-guide", sarcastic=1) | {
                "prompt_version": GUIDE_PROMPT_VERSION
            }
            assert insert_eval_annotation(db, row)
        # A sarc-v2 eval vote must not be picked up for a guide-v1.1 pilot.
        assert insert_eval_annotation(db, _ann("t1_c2", "e1-guide"))
        eval_before = db.conn.execute("SELECT COUNT(*) FROM eval_annotations").fetchone()[0]
    dst = tmp_path / "v4.sqlite3"
    fork_subset(cfg.db_path, dst, base_prompt_version=PROMPT_VERSION, size=3)

    with pytest.raises(ValueError, match="their own database"):
        import_eval_votes(cfg.db_path, cfg.db_path, mapping={"a1": "e1-guide"},
                          prompt_version=GUIDE_PROMPT_VERSION)
    stats = import_eval_votes(cfg.db_path, dst, mapping={"a1": "e1-guide"},
                              prompt_version=GUIDE_PROMPT_VERSION)
    assert stats == {"copied": 1, "already_present": 0}
    with AnnotationDatabase(dst) as db:
        rows = db.conn.execute(
            "SELECT reddit_fullname, model_key, role, prompt_version, sarcastic "
            "FROM llm_annotations"
        ).fetchall()
        assert [tuple(r) for r in rows] == [
            ("t1_c2", "a1", "annotator", GUIDE_PROMPT_VERSION, 1)
        ]
        # The pilot's a1 queue skips the imported item.
        assert "t1_c2" not in db.pending_target_items("a1", GUIDE_PROMPT_VERSION, 3)
    with AnnotationDatabase(cfg.db_path) as db:
        assert db.conn.execute("SELECT COUNT(*) FROM eval_annotations").fetchone()[0] == (
            eval_before
        )
        # Nothing reached the source's own votes.
        assert db.conn.execute(
            "SELECT COUNT(*) FROM llm_annotations WHERE prompt_version = ?",
            (GUIDE_PROMPT_VERSION,),
        ).fetchone()[0] == 0
    again = import_eval_votes(cfg.db_path, dst, mapping={"a1": "e1-guide"},
                              prompt_version=GUIDE_PROMPT_VERSION)
    assert again == {"copied": 0, "already_present": 1}


def test_pipeline_next_steps_name_the_config_and_no_fixed_version(tmp_path: Path) -> None:
    from uyam.annotate.pipeline import _next_steps_hint

    cfg = load_annotation_config(
        _write_config(tmp_path, aggregation={"sarcasm_rule": "majority", "tie_break": "confidence"})
    )
    hint = _next_steps_hint(cfg)
    assert "--version v1" not in hint
    assert f"aggregate --config-path {cfg.source_path.as_posix()}" in hint


def test_fork_extend_keeps_pilot_votes_and_stages_the_new_targets(tmp_path: Path) -> None:
    cfg = _source(tmp_path)
    v3 = tmp_path / "v3.sqlite3"
    fork_for_reannotation(
        cfg.db_path, v3, prompt_version=PROMPT_VERSION, keep_annotators=["a1"],
        extend={"2026-06": 3},
    )
    pilot = tmp_path / "v4.sqlite3"
    fork_subset(cfg.db_path, pilot, base_prompt_version=PROMPT_VERSION, size=3)
    with AnnotationDatabase(pilot) as db:
        first = db.conn.execute("SELECT reddit_fullname FROM target_priority").fetchone()[0]
        db.insert_llm_annotation(_ann(str(first), "a1") | {"prompt_version": GUIDE_PROMPT_VERSION})
    before = {p: _snapshot(p) for p in (pilot, v3)}

    dst = tmp_path / "v5.sqlite3"
    stats = fork_extend(pilot, dst, targets_from=v3, first_reason="source-labeled")
    assert {p: _snapshot(p) for p in (pilot, v3)} == before  # both sources untouched
    assert stats["kept"] == 3 and stats["kept_gold"] == 1
    assert stats["added_first"] == 6 - 3  # v3's 6 source-labeled, minus the 3 pilot items
    assert stats["added_rest"] == 3  # the June extension
    assert stats["stage1_target"] == 6 and stats["target_total"] == 9

    with AnnotationDatabase(dst) as db:
        prio = {str(r[0]): int(r[1]) for r in db.conn.execute(
            "SELECT reddit_fullname, priority FROM target_priority")}
        assert prio["t1_c2"] == 4  # gold
        assert sorted(prio.values()).count(1) == 3 and sorted(prio.values()).count(2) == 3
        # The pilot's vote carries over and still counts.
        assert db.conn.execute("SELECT COUNT(*) FROM llm_annotations").fetchone()[0] == 1
        # Stage 1 (--target 6) holds every gold, pilot and first-reason item and no June item.
        stage1 = db.target_items(GUIDE_PROMPT_VERSION, stats["stage1_target"])
        assert {f for f, p in prio.items() if p >= 2} == set(stage1)
        assert set(db.target_items(GUIDE_PROMPT_VERSION, 9)) == set(prio)
    with pytest.raises(FileExistsError):
        fork_extend(pilot, dst, targets_from=v3, first_reason="source-labeled")


def test_the_repo_v5_config_is_v4_at_v3_size() -> None:
    repo = Path(__file__).resolve().parent.parent
    v4 = load_annotation_config(repo / "config" / "annotation-v4.yaml")
    v5 = load_annotation_config(repo / "config" / "annotation-v5.yaml")
    assert v5.prompt_version == v4.prompt_version == GUIDE_PROMPT_VERSION
    assert [(a.key, a.model, a.prompt) for a in v5.annotators] == [
        (a.key, a.model, a.prompt) for a in v4.annotators
    ]
    assert v5.options == v4.options and v5.aggregation == v4.aggregation
    assert {a.endpoint for a in v5.annotators} == {"local"} and "remote" not in v5.endpoints
    assert v5.db_path.name == "annotation-v5.sqlite3" and v5.pipeline.target_items == 21000

