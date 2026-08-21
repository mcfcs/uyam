"""SQLite-backed deduplication for the Uyám collection pipeline.

Uses a UNIQUE constraint on reddit_fullname as the authoritative guard.
Tracks per-run statistics in a separate collection_runs table.

Failure model:
  SQLite UNIQUE(reddit_fullname) is reserved first; JSONL is written only after
  a successful insert. Clicking Start Collection again skips already-stored
  posts and comments — no second JSONL line.
  If the process crashes after SQLite insert but before the JSONL append, that
  one record is marked seen and will not be re-fetched (a missing JSONL line,
  not a duplicate). The website also drops duplicate fullnames when displaying.
"""

from __future__ import annotations

import logging
import sqlite3
import time
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
        last_exc: sqlite3.OperationalError | None = None
        for attempt in range(25):
            conn: sqlite3.Connection | None = None
            try:
                conn = sqlite3.connect(str(db_path), timeout=30.0)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.executescript(_SCHEMA)
                conn.commit()
                self._conn = conn
                return
            except sqlite3.OperationalError as exc:
                last_exc = exc
                if conn is not None:
                    conn.close()
                if "locked" not in str(exc).lower():
                    raise
                time.sleep(0.05 * (attempt + 1))
        raise last_exc or sqlite3.OperationalError("database is locked")

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
