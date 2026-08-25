"""Export: row shape, dataset card, and the privacy whitelist (no raw authors)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.annotate_fixtures import make_config, write_raw_corpus
from tests.test_annotate_runner import FakeClient, make_output
from uyam.annotate.aggregate import run_aggregation
from uyam.annotate.candidates import select_candidates
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.export import run_export
from uyam.annotate.prompts import PROMPT_VERSION
from uyam.annotate.review import sample_gold_subset
from uyam.annotate.runner import run_annotator


def assert_no_author_key(obj: Any) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert key != "author", "raw author key leaked into an export artifact"
            assert_no_author_key(value)
    elif isinstance(obj, list):
        for value in obj:
            assert_no_author_key(value)


def _run_pipeline(tmp_path: Path) -> Any:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)
    for key in ("a1", "a2", "a3"):
        run_annotator(cfg, key, client_factory=lambda c, a: FakeClient())
    run_aggregation(cfg, PROMPT_VERSION)
    return cfg


def test_export_rows_and_card(tmp_path: Path) -> None:
    cfg = _run_pipeline(tmp_path)
    result = run_export(cfg, PROMPT_VERSION, dataset_version="vtest", formats=("jsonl",))

    rows = [
        json.loads(line)
        for line in Path(result["jsonl"]).read_text(encoding="utf-8").splitlines()
    ]
    assert result["rows"] == len(rows) > 0

    row = next(r for r in rows if r["record_type"] == "comment")
    assert row["labels"]["sarcastic"] is False
    assert row["labels"]["language"] == "taglish"
    assert row["reliability"]["resolved_by"] == "unanimous"
    assert row["reliability"]["sarcasm_votes"] == "3-0"
    assert len(row["reliability"]["annotators"]) == 3
    assert row["context"] is not None
    assert row["author_hash"].startswith("hash_")
    assert row["provenance"]["prompt_version"] == PROMPT_VERSION

    card = json.loads(Path(result["card"]).read_text(encoding="utf-8"))
    assert card["dataset_version"] == "vtest"
    assert card["counts"]["exported_rows"] == len(rows)
    assert "sarcastic" in card["label_distributions"]
    assert card["agreement"]["labels"]["sarcastic"]["raters"] == ["a1", "a2", "a3"]


def test_export_never_leaks_raw_authors(tmp_path: Path) -> None:
    cfg = _run_pipeline(tmp_path)
    result = run_export(cfg, PROMPT_VERSION, dataset_version="vtest", formats=("jsonl",))

    for artifact in (result["jsonl"], result["corpus"]):
        text = Path(artifact).read_text(encoding="utf-8")
        assert "real_name" not in text  # fixture usernames all contain this marker
        for line in text.splitlines():
            assert_no_author_key(json.loads(line))
    assert_no_author_key(json.loads(Path(result["card"]).read_text(encoding="utf-8")))


def test_export_skips_unresolved_by_default(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    with AnnotationDatabase(cfg.db_path) as db:
        index_corpus(db, cfg.data_dir)
        select_candidates(db, cfg.candidate_filters)

    # Two annotators disagree on sarcasm -> escalated, unresolved.
    run_annotator(
        cfg,
        "a1",
        client_factory=lambda c, a: FakeClient(lambda ann, p: make_output(sarcastic=True)),
    )
    run_annotator(
        cfg,
        "a2",
        client_factory=lambda c, a: FakeClient(lambda ann, p: make_output(sarcastic=False)),
    )
    run_aggregation(cfg, PROMPT_VERSION)

    result = run_export(cfg, PROMPT_VERSION, dataset_version="vtest", formats=("jsonl",))
    assert result["rows"] == 0
    assert result["skipped_unresolved"] > 0

    included = run_export(
        cfg,
        PROMPT_VERSION,
        dataset_version="vtest2",
        formats=("jsonl",),
        include_unresolved=True,
    )
    assert included["rows"] > 0


def test_gold_sample_fills_review_queue(tmp_path: Path) -> None:
    cfg = _run_pipeline(tmp_path)
    stats = sample_gold_subset(cfg, PROMPT_VERSION, size=3, seed=7)
    assert stats["sampled"] == 3
    with AnnotationDatabase(cfg.db_path) as db:
        queue = db.review_queue_items()
        assert len([q for q in queue if q["reason"] == "gold"]) == 3
    # Deterministic under the same seed.
    stats2 = sample_gold_subset(cfg, PROMPT_VERSION, size=3, seed=7)
    assert stats2["sampled"] == 3
