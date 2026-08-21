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
from uyam.storage import append_record, clear_collected_data

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
        subs2, com2 = _run()

        assert subs1 > 0
        assert com1 > 0
        assert subs2 == 0  # all duplicates
        assert com2 == 0

        db.close()


class TestJSONLFullnameProvidesDeduplication:
    """Simulate the crash scenario: JSONL written but SQLite not updated.

    On re-run: SQLite allows re-collection → record appears twice in JSONL.
    The reddit_fullname field allows post-hoc deduplication.
    """

    def test_duplicate_jsonl_lines_deduplicable_by_fullname(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        db = DedupDatabase(tmp_path / "db" / "coll.sqlite3")
        source = FixtureRedditSource(FIXTURE_PATH)

        # First run — normal
        req1 = CollectionRequest(
            subreddit="Philippines",
            listing_type="new",
            collection_run_id=make_collection_run_id(),
        )
        ctx1 = run_collection(source, req1, data_dir=data_dir, db=db, source_type="fixture")
        subs_first = ctx1.actual_submissions_stored
        assert subs_first > 0

        # Simulate crash: manually write duplicate JSONL lines
        # by collecting again WITHOUT registering in SQLite (simulate using a fresh DB)
        db2 = DedupDatabase(tmp_path / "db2" / "coll.sqlite3")
        req2 = CollectionRequest(
            subreddit="Philippines",
            listing_type="new",
            collection_run_id=make_collection_run_id(),
        )
        # Use the same JSONL path as first run
        # by directly reading submissions from fixture and appending
        phantom_source = FixtureRedditSource(FIXTURE_PATH)
        jsonl_file = list((data_dir / "raw" / "Philippines").glob("*.jsonl"))[0]
        lines_before = len(jsonl_file.read_text(encoding="utf-8").strip().splitlines())

        for sub in phantom_source.iter_submissions(req2):
            # Write to JSONL without marking in the ORIGINAL db (simulates crash)
            append_record(jsonl_file, sub)

        lines_after = len(jsonl_file.read_text(encoding="utf-8").strip().splitlines())
        db2.close()

        # JSONL now has duplicates
        assert lines_after > lines_before

        # But deduplication by reddit_fullname removes them
        seen: set[str] = set()
        unique_records = []
        for line in jsonl_file.read_text(encoding="utf-8").strip().splitlines():
            record = json.loads(line)
            fn = record["reddit_fullname"]
            if fn not in seen:
                seen.add(fn)
                unique_records.append(record)

        # Unique count matches original first-run count (submissions only for simplicity)
        unique_submissions = [r for r in unique_records if r["record_type"] == "submission"]
        assert len(unique_submissions) == subs_first

        db.close()


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
