"""Tests for storage failure and recovery behaviour.

The documented failure model is:
  JSONL is written first, then SQLite is updated.
  If SQLite registration fails after JSONL write, the record appears in JSONL
  but SQLite allows re-collection, producing a JSONL duplicate on the next run.
  The reddit_fullname field in every JSONL line allows post-hoc deduplication.

These tests verify:
  1. The SQLite UNIQUE constraint prevents the common "run twice" duplicate.
  2. A record that reached JSONL but missed SQLite (simulated) is re-collected
     on the next run (intentional design), and can be deduplicated by fullname.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uyam.dedup import DedupDatabase
from uyam.pipeline import make_collection_run_id, run_collection
from uyam.privacy import reset_hmac_key_cache
from uyam.sources.base import CollectionRequest
from uyam.sources.fixture import FixtureRedditSource
from uyam.storage import clear_collected_data, load_jsonl_records

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "sample_posts.json"
TEST_KEY = "test-hmac-key-storage-recovery"


@pytest.fixture(autouse=True)
def set_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHOR_HMAC_KEY", TEST_KEY)
    reset_hmac_key_cache()
    yield
    reset_hmac_key_cache()


class TestSQLitePreventsDuplicates:
    """Normal case: running the same collection twice stores zero duplicates."""

    def test_second_run_stores_zero(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        db = DedupDatabase(tmp_path / "db" / "coll.sqlite3")
        source = FixtureRedditSource(FIXTURE_PATH)

        def _run() -> tuple[int, int]:
            req = CollectionRequest(
                subreddit="Philippines",
                listing_type="new",
                collection_run_id=make_collection_run_id(),
            )
            ctx = run_collection(source, req, data_dir=data_dir, db=db, source_type="fixture")
            return ctx.actual_submissions_stored, ctx.comments_stored

        subs1, com1 = _run()
        jsonl_files = list((data_dir / "raw" / "Philippines").glob("*.jsonl"))
        assert len(jsonl_files) == 1
        lines_after_first = jsonl_files[0].read_text(encoding="utf-8")

        subs2, com2 = _run()

        assert subs1 > 0
        assert com1 > 0
        assert subs2 == 0  # all duplicates
        assert com2 == 0
        assert jsonl_files[0].read_text(encoding="utf-8") == lines_after_first

        db.close()


class TestJSONLFullnameProvidesDeduplication:
    """Website loader still drops duplicate fullnames if a file already has them."""

    def test_second_pipeline_run_does_not_grow_jsonl(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        db = DedupDatabase(tmp_path / "db" / "coll.sqlite3")
        source = FixtureRedditSource(FIXTURE_PATH)

        def _run() -> int:
            req = CollectionRequest(
                subreddit="Philippines",
                listing_type="new",
                collection_run_id=make_collection_run_id(),
            )
            ctx = run_collection(source, req, data_dir=data_dir, db=db, source_type="fixture")
            return ctx.actual_submissions_stored

        assert _run() > 0
        jsonl_file = list((data_dir / "raw" / "Philippines").glob("*.jsonl"))[0]
        before = jsonl_file.read_text(encoding="utf-8")
        assert _run() == 0
        assert jsonl_file.read_text(encoding="utf-8") == before
        db.close()

    def test_load_jsonl_records_drops_duplicate_fullnames(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        raw = data_dir / "raw" / "Philippines"
        raw.mkdir(parents=True)
        path = raw / "2026-08-21.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {"reddit_fullname": "t3_aaa", "record_type": "submission", "id": "aaa"}
                    ),
                    json.dumps(
                        {"reddit_fullname": "t3_aaa", "record_type": "submission", "id": "aaa"}
                    ),
                    json.dumps(
                        {"reddit_fullname": "t1_bbb", "record_type": "comment", "id": "bbb"}
                    ),
                    "{not json",
                    json.dumps(
                        {"reddit_fullname": "t1_bbb", "record_type": "comment", "id": "bbb"}
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        unique = load_jsonl_records(data_dir, unique=True)
        assert [r["reddit_fullname"] for r in unique] == ["t3_aaa", "t1_bbb"]
        all_rows = load_jsonl_records(data_dir, unique=False)
        assert len(all_rows) == 4


def test_clear_collected_data(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    raw = data_dir / "raw" / "Philippines"
    raw.mkdir(parents=True)
    (raw / "2026-08-20.jsonl").write_text("{}\n", encoding="utf-8")
    manifests = data_dir / "manifests"
    manifests.mkdir()
    (manifests / "abc.json").write_text("{}", encoding="utf-8")
    db_dir = data_dir / "db"
    db_dir.mkdir()
    (db_dir / "collection.sqlite3").write_bytes(b"x")
    (db_dir / "collection.sqlite3-wal").write_bytes(b"x")

    stats = clear_collected_data(data_dir)
    assert stats["jsonl_files"] == 1
    assert stats["manifests"] == 1
    assert stats["db_files"] == 2
    assert not (raw / "2026-08-20.jsonl").exists()
    assert not (manifests / "abc.json").exists()
    assert not (db_dir / "collection.sqlite3").exists()
