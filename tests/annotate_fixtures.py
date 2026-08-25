"""Shared builders for the annotate test suite: a tiny synthetic corpus and
an in-tmp AnnotationConfig. Imported explicitly by test_annotate_* modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from uyam.annotate.config import (
    AnnotationConfig,
    AnnotatorConfig,
    CandidateFiltersConfig,
    ContextConfig,
    ExportConfig,
    LidConfig,
    OllamaOptionsConfig,
    ReviewConfig,
    TxSentimentConfig,
)
from uyam.annotate.prompts import PROMPT_VERSION


def make_config(tmp_path: Path) -> AnnotationConfig:
    return AnnotationConfig(
        db_path=tmp_path / "db" / "annotation.sqlite3",
        data_dir=tmp_path / "data",
        prompt_version=PROMPT_VERSION,
        endpoints={"local": "http://127.0.0.1:11434", "remote": "http://remote:11434"},
        annotators=[
            AnnotatorConfig(key="a1", model="model-one", endpoint="local"),
            AnnotatorConfig(key="a2", model="model-two", endpoint="local"),
            AnnotatorConfig(key="a3", model="model-three", endpoint="remote"),
        ],
        adjudicator=AnnotatorConfig(key="adj", model="model-adj", endpoint="remote"),
        options=OllamaOptionsConfig(max_retries=1),
        context=ContextConfig(),
        candidate_filters=CandidateFiltersConfig(),
        lid=LidConfig(model_path=tmp_path / "lid.bin"),
        tx_sentiment=TxSentimentConfig(),
        review=ReviewConfig(),
        export=ExportConfig(out_dir=tmp_path / "annotated"),
    )


def _submission(rid: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "record_type": "submission",
        "reddit_id": rid,
        "reddit_fullname": f"t3_{rid}",
        "subreddit": "CasualPH",
        "title": f"Post title {rid} about something mildly interesting",
        "selftext": "Gumawa ako ng post kasi gusto ko lang magshare ng kwento ngayon.",
        "created_utc": "2026-08-20T10:00:00Z",
        "score": 10,
        "is_self": True,
        "author": "op_user_real_name",
        "author_hash": f"hash_op_{rid}",
        "author_status": "pseudonymized",
        "sampling_strategy": "natural",
        "permalink": f"/r/CasualPH/comments/{rid}/post/",
        "distinguished": None,
        "stickied": False,
    }
    base.update(overrides)
    return base


def _comment(rid: str, sub_rid: str, parent: str, depth: int, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "record_type": "comment",
        "reddit_id": rid,
        "reddit_fullname": f"t1_{rid}",
        "submission_id": sub_rid,
        "link_id": f"t3_{sub_rid}",
        "parent_id": parent,
        "parent_record_type": "submission" if parent.startswith("t3_") else "comment",
        "subreddit": "CasualPH",
        "body": f"Comment body {rid} na medyo mahaba para pumasa sa filters dito.",
        "created_utc": f"2026-08-20T11:0{depth}:00Z",
        "score": 5,
        "depth": depth,
        "is_submitter": False,
        "author": f"user_{rid}_real_name",
        "author_hash": f"hash_{rid}",
        "author_status": "pseudonymized",
        "sampling_strategy": "natural",
        "permalink": f"/r/CasualPH/comments/{sub_rid}/post/{rid}/",
        "distinguished": None,
        "stickied": False,
    }
    base.update(overrides)
    return base


def write_raw_corpus(data_dir: Path) -> list[dict[str, Any]]:
    """A thread (sub1 <- c1 <- c2 <- c3, plus c4/c5 replies to c1) and records
    that every candidate filter should exclude."""
    records = [
        _submission("sub1"),
        _comment("c1", "sub1", "t3_sub1", 0),
        _comment("c2", "sub1", "t1_c1", 1),
        _comment("c3", "sub1", "t1_c2", 2, body="Wow ang galing naman ng serbisyo nila dito."),
        _comment("c4", "sub1", "t1_c1", 1, created_utc="2026-08-20T11:05:00Z"),
        _comment("c5", "sub1", "t1_c1", 1, created_utc="2026-08-20T11:06:00Z"),
        _comment("bot1", "sub1", "t3_sub1", 0, author="AutoModerator", author_hash="hash_bot"),
        _comment("short1", "sub1", "t3_sub1", 0, body="ok"),
        _comment(
            "del1", "sub1", "t3_sub1", 0, body="[deleted]", author=None, author_status="deleted"
        ),
        _comment("url1", "sub1", "t3_sub1", 0, body="https://example.com/a?b=c longurl"),
        _submission("link1", is_self=False, selftext=""),
    ]
    out = data_dir / "raw" / "CasualPH" / "2026-08-20.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    return records
