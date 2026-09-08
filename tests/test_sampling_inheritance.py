"""Comments inherit the submission's sampling strategy (thesis §3.1 oversampling tag)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.annotate_fixtures import make_config, write_raw_corpus
from tests.conftest import FIXTURE_PATH
from uyam.annotate.corpus import index_corpus
from uyam.annotate.db import AnnotationDatabase
from uyam.sources.base import CollectionRequest
from uyam.sources.fixture import FixtureRedditSource
from uyam.sources.mapping import map_comment_dict


def test_map_comment_dict_carries_parent_tag() -> None:
    raw = {
        "id": "c1",
        "name": "t1_c1",
        "parent_id": "t3_s1",
        "link_id": "t3_s1",
        "subreddit": "CasualPH",
        "body": "edi wow naman",
        "created_utc": 1755000000,
        "score": 1,
        "permalink": "/r/CasualPH/comments/s1/x/c1/",
        "author": None,
    }
    rec = map_comment_dict(
        raw,
        collection_run_id="run",
        sampling_strategy="keyword_oversampled",
        matched_query_or_keyword="edi wow",
    )
    assert rec.sampling_strategy == "keyword_oversampled"
    assert rec.matched_query_or_keyword == "edi wow"
    dumped = json.loads(rec.model_dump_json())
    assert dumped["sampling_strategy"] == "keyword_oversampled"
    assert "author" not in dumped

    natural = map_comment_dict(raw, collection_run_id="run")
    assert natural.sampling_strategy == "natural"
    assert natural.matched_query_or_keyword is None


def test_fixture_source_comments_inherit_request_tag() -> None:
    source = FixtureRedditSource(FIXTURE_PATH)
    request = CollectionRequest(
        subreddit="Philippines",
        listing_type="search",
        collection_run_id="run-oversample",
        limit=5,
        search_query="sana all",
        sampling_strategy="keyword_oversampled",
        matched_query_or_keyword="sana all",
        max_comments_per_submission=10,
    )
    subs = list(source.iter_submissions(request))
    assert subs and all(s.sampling_strategy == "keyword_oversampled" for s in subs)
    comments = [c for s in subs for c in source.iter_comments(s, request)]
    assert comments
    assert all(c.sampling_strategy == "keyword_oversampled" for c in comments)
    assert all(c.matched_query_or_keyword == "sana all" for c in comments)


def test_index_backfills_legacy_comments_from_their_submission(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_raw_corpus(cfg.data_dir)
    raw_file = next((cfg.data_dir / "raw").glob("**/*.jsonl"))
    rows = [json.loads(line) for line in raw_file.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["reddit_fullname"] == "t3_sub1":
            row["sampling_strategy"] = "keyword_oversampled"
            row["matched_query_or_keyword"] = "edi wow"
        elif row["record_type"] == "comment":
            row.pop("sampling_strategy", None)  # legacy comment: no tag at all
    raw_file.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )

    with AnnotationDatabase(cfg.db_path) as db:
        stats = index_corpus(db, cfg.data_dir)
        c3 = db.get_record("t1_c3")
        link_comment_count = db.conn.execute(
            "SELECT COUNT(*) FROM corpus_index WHERE record_type='comment' "
            "AND sampling_strategy IS NULL"
        ).fetchone()[0]
    assert stats["comments_inherited_sampling"] >= 5
    assert c3 is not None
    assert c3["sampling_strategy"] == "keyword_oversampled"
    assert c3["matched_query_or_keyword"] == "edi wow"
    assert link_comment_count == 0
