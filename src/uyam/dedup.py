"""SQLite-backed deduplication for the Uyám collection pipeline.

Uses a UNIQUE constraint on reddit_fullname as the authoritative guard.
Tracks per-run statistics in a separate collection_runs table.

Failure model:
  Records are written to JSONL first, then registered in SQLite.
  If the process crashes between JSONL write and SQLite registration,
  the record exists in JSONL but SQLite allows re-collection on the next run,
  producing a duplicate JSONL line. This window is narrow and documented.
  The reddit_fullname field in every JSONL line allows post-hoc deduplication
  during dataset construction. The SQLite UNIQUE constraint is the guard
  against the common "run twice" case.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collected_records (
    reddit_fullname   TEXT PRIMARY KEY,
    record_type       TEXT NOT NULL,
    reddit_id         TEXT NOT NULL,
    subreddit         TEXT NOT NULL,
    collection_run_id TEXT NOT NULL,
    first_seen_at_utc TEXT NOT NULL,
    jsonl_path        TEXT
);

CREATE TABLE IF NOT EXISTS collection_runs (
    collection_run_id   TEXT PRIMARY KEY,
    source_type         TEXT NOT NULL,
    subreddit           TEXT NOT NULL,
    started_at_utc      TEXT NOT NULL,
    finished_at_utc     TEXT,
    submissions_seen    INTEGER DEFAULT 0,
    submissions_stored  INTEGER DEFAULT 0,
    comments_seen       INTEGER DEFAULT 0,
    comments_stored     INTEGER DEFAULT 0,
    duplicates_skipped  INTEGER DEFAULT 0,
    validation_failures INTEGER DEFAULT 0,
    manifest_path       TEXT
);
"""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class DedupDatabase:
    """Manages the SQLite deduplication database."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DedupDatabase:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Run management
    # ------------------------------------------------------------------

    def register_run(
        self,
        collection_run_id: str,
        source_type: str,
        subreddit: str,
        started_at_utc: str | None = None,
    ) -> None:
        ts = started_at_utc or _utc_now_iso()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO collection_runs
              (collection_run_id, source_type, subreddit, started_at_utc)
            VALUES (?, ?, ?, ?)
            """,
            (collection_run_id, source_type, subreddit, ts),
        )
        self._conn.commit()

    def finish_run(
        self,
        collection_run_id: str,
        *,
        submissions_seen: int = 0,
        submissions_stored: int = 0,
        comments_seen: int = 0,
        comments_stored: int = 0,
        duplicates_skipped: int = 0,
        validation_failures: int = 0,
        manifest_path: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE collection_runs SET
              finished_at_utc     = ?,
              submissions_seen    = ?,
              submissions_stored  = ?,
              comments_seen       = ?,
              comments_stored     = ?,
              duplicates_skipped  = ?,
              validation_failures = ?,
              manifest_path       = ?
            WHERE collection_run_id = ?
            """,
            (
                _utc_now_iso(),
                submissions_seen,
                submissions_stored,
                comments_seen,
                comments_stored,
                duplicates_skipped,
                validation_failures,
                manifest_path,
                collection_run_id,
            ),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Record deduplication
    # ------------------------------------------------------------------

    def is_seen(self, reddit_fullname: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM collected_records WHERE reddit_fullname = ?",
            (reddit_fullname,),
        ).fetchone()
        return row is not None

    def mark_seen(
        self,
        *,
        reddit_fullname: str,
        record_type: str,
        reddit_id: str,
        subreddit: str,
        collection_run_id: str,
        jsonl_path: str | None = None,
    ) -> bool:
        """Register a record as collected. Returns True if newly inserted, False if duplicate."""
        try:
            self._conn.execute(
                """
                INSERT INTO collected_records
                  (reddit_fullname, record_type, reddit_id, subreddit,
                   collection_run_id, first_seen_at_utc, jsonl_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reddit_fullname,
                    record_type,
                    reddit_id,
                    subreddit,
                    collection_run_id,
                    _utc_now_iso(),
                    jsonl_path,
                ),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            logger.debug(
                "duplicate_skipped",
                extra={"reddit_fullname": reddit_fullname},
            )
            return False

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def subreddit_stats(self) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT
              subreddit,
              SUM(CASE WHEN record_type = 'submission' THEN 1 ELSE 0 END) AS submissions,
              SUM(CASE WHEN record_type = 'comment'    THEN 1 ELSE 0 END) AS comments,
              MAX(first_seen_at_utc) AS last_collection
            FROM collected_records
            GROUP BY subreddit
            ORDER BY subreddit
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def total_counts(self) -> dict[str, int]:
        row = self._conn.execute(
            "SELECT COUNT(*) AS total FROM collected_records"
        ).fetchone()
        run_row = self._conn.execute(
            "SELECT COUNT(*) AS runs FROM collection_runs"
        ).fetchone()
        return {
            "total_records": row["total"],
            "total_runs": run_row["runs"],
        }

    def list_runs(self) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            SELECT * FROM collection_runs ORDER BY started_at_utc DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, collection_run_id: str) -> dict[str, object] | None:
        row = self._conn.execute(
            "SELECT * FROM collection_runs WHERE collection_run_id = ?",
            (collection_run_id,),
        ).fetchone()
        return dict(row) if row else None
