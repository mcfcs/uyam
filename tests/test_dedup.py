"""Tests for SQLite deduplication."""

from __future__ import annotations

from pathlib import Path

import pytest

from uyam.dedup import DedupDatabase


@pytest.fixture
def db(tmp_path: Path) -> DedupDatabase:
    return DedupDatabase(tmp_path / "db" / "test.sqlite3")


def _mark(db: DedupDatabase, fullname: str, run_id: str = "run-001") -> bool:
    record_type = "submission" if fullname.startswith("t3_") else "comment"
    return db.mark_seen(
        reddit_fullname=fullname,
        record_type=record_type,
        reddit_id=fullname.split("_", 1)[1],
        subreddit="Philippines",
        collection_run_id=run_id,
        jsonl_path="data/raw/Philippines/2026-08-15.jsonl",
    )


class TestMarkSeen:
    def test_first_insert_returns_true(self, db: DedupDatabase) -> None:
        assert _mark(db, "t3_abc001") is True

    def test_duplicate_returns_false(self, db: DedupDatabase) -> None:
        _mark(db, "t3_abc001")
        assert _mark(db, "t3_abc001") is False

    def test_is_seen_after_mark(self, db: DedupDatabase) -> None:
        _mark(db, "t3_abc001")
        assert db.is_seen("t3_abc001") is True

    def test_not_seen_before_mark(self, db: DedupDatabase) -> None:
        assert db.is_seen("t3_new001") is False

    def test_different_fullnames_independent(self, db: DedupDatabase) -> None:
        assert _mark(db, "t3_abc001") is True
        assert _mark(db, "t3_abc002") is True


class TestRunFixtureTwice:
    """Simulates running fixture collection twice; second run stores zero new records."""

    def test_second_run_zero_new(self, db: DedupDatabase) -> None:
        fullnames = [f"t3_fake{i:03d}" for i in range(1, 11)]

        # First run
        db.register_run("run-001", "fixture", "Philippines")
        stored_first = sum(_mark(db, fn, "run-001") for fn in fullnames)
        db.finish_run("run-001", submissions_stored=stored_first)

        # Second run — same records
        db.register_run("run-002", "fixture", "Philippines")
        stored_second = sum(_mark(db, fn, "run-002") for fn in fullnames)
        db.finish_run("run-002", submissions_stored=stored_second)

        assert stored_first == 10
        assert stored_second == 0


class TestRunManagement:
    def test_register_and_finish(self, db: DedupDatabase) -> None:
        db.register_run("run-abc", "fixture", "CasualPH")
        db.finish_run("run-abc", submissions_stored=5, comments_stored=20)

        run = db.get_run("run-abc")
        assert run is not None
        assert run["submissions_stored"] == 5
        assert run["comments_stored"] == 20
        assert run["finished_at_utc"] is not None

    def test_list_runs(self, db: DedupDatabase) -> None:
        db.register_run("run-1", "fixture", "Philippines")
        db.register_run("run-2", "reddit", "CasualPH")
        runs = db.list_runs()
        assert len(runs) == 2

    def test_get_nonexistent_run(self, db: DedupDatabase) -> None:
        assert db.get_run("does-not-exist") is None


class TestSubredditStats:
    def test_stats_grouped_by_subreddit(self, db: DedupDatabase) -> None:
        db.register_run("run-001", "fixture", "Philippines")
        _mark(db, "t3_ph001")
        _mark(db, "t3_ph002")
        _mark(db, "t1_com001")

        stats = db.subreddit_stats()
        ph = next(s for s in stats if s["subreddit"] == "Philippines")
        assert ph["submissions"] == 2
        assert ph["comments"] == 1
