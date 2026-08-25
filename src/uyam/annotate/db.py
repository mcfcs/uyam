"""SQLite storage for the annotation pipeline.

Lives at data/db/annotation.sqlite3 — deliberately separate from
collection.sqlite3: `uyam clear-data` deletes the collection DB, annotations
must survive that, and collection may run concurrently (WAL single-writer
contention avoided with separate files).

Resume key for LLM passes: UNIQUE(reddit_fullname, model_key, prompt_version, role).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS corpus_index (
    reddit_fullname     TEXT PRIMARY KEY,
    record_type         TEXT NOT NULL,
    reddit_id           TEXT NOT NULL,
    subreddit           TEXT NOT NULL,
    submission_fullname TEXT,
    parent_fullname     TEXT,
    depth               INTEGER,
    created_utc         TEXT,
    author_hash         TEXT,
    author_status       TEXT,
    is_submitter        INTEGER,
    is_bot              INTEGER DEFAULT 0,
    distinguished       TEXT,
    stickied            INTEGER DEFAULT 0,
    is_self             INTEGER,
    score               INTEGER,
    sampling_strategy   TEXT,
    matched_query_or_keyword TEXT,
    title               TEXT,
    selftext            TEXT,
    body                TEXT,
    text                TEXT NOT NULL,
    permalink           TEXT,
    jsonl_path          TEXT,
    indexed_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_corpus_parent     ON corpus_index(parent_fullname);
CREATE INDEX IF NOT EXISTS idx_corpus_submission ON corpus_index(submission_fullname);
CREATE INDEX IF NOT EXISTS idx_corpus_author     ON corpus_index(author_hash);

CREATE TABLE IF NOT EXISTS candidates (
    reddit_fullname  TEXT PRIMARY KEY REFERENCES corpus_index(reddit_fullname),
    eligible         INTEGER NOT NULL,
    exclusion_reason TEXT,
    text_norm        TEXT,
    selected_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lid_results (
    reddit_fullname TEXT PRIMARY KEY,
    language        TEXT NOT NULL CHECK(language IN ('english','tagalog','taglish')),
    en_ratio        REAL,
    tl_ratio        REAL,
    other_ratio     REAL,
    confidence      REAL,
    lid_model       TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tx_sentiment (
    reddit_fullname TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    p_pos           REAL,
    p_neu           REAL,
    p_neg           REAL,
    model_name      TEXT,
    model_revision  TEXT,
    device          TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contexts (
    reddit_fullname TEXT PRIMARY KEY,
    prompt_version  TEXT NOT NULL,
    context_json    TEXT NOT NULL,
    context_text    TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_annotations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reddit_fullname TEXT NOT NULL,
    model_key       TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    role            TEXT NOT NULL CHECK(role IN ('annotator','adjudicator')),
    language        TEXT,
    literal_sentiment  TEXT,
    intended_sentiment TEXT,
    sarcastic       INTEGER,
    cue_polarity_inversion     INTEGER,
    cue_rhetorical_intent      INTEGER,
    cue_contextual_incongruity INTEGER,
    cue_hyperbole              INTEGER,
    confidence      REAL,
    rationale       TEXT,
    raw_json        TEXT,
    model_digest    TEXT,
    endpoint        TEXT,
    ollama_version  TEXT,
    options_json    TEXT,
    attempts        INTEGER,
    duration_ms     INTEGER,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    created_at      TEXT NOT NULL,
    UNIQUE(reddit_fullname, model_key, prompt_version, role)
);
CREATE INDEX IF NOT EXISTS idx_llm_item ON llm_annotations(reddit_fullname, prompt_version);

CREATE TABLE IF NOT EXISTS llm_failures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reddit_fullname TEXT NOT NULL,
    model_key       TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    role            TEXT NOT NULL,
    error           TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fail_key
    ON llm_failures(reddit_fullname, model_key, prompt_version, role);

CREATE TABLE IF NOT EXISTS aggregates (
    reddit_fullname   TEXT PRIMARY KEY,
    prompt_version    TEXT NOT NULL,
    n_annotators      INTEGER,
    sarcasm_votes     TEXT,
    votes_json        TEXT,
    sarcastic_final   INTEGER,
    language_final    TEXT,
    literal_final     TEXT,
    intended_final    TEXT,
    cue_polarity_inversion_final     INTEGER,
    cue_rhetorical_intent_final      INTEGER,
    cue_contextual_incongruity_final INTEGER,
    cue_hyperbole_final              INTEGER,
    needs_adjudication INTEGER DEFAULT 0,
    needs_human        INTEGER DEFAULT 0,
    resolved_by        TEXT CHECK(resolved_by IN ('unanimous','majority','adjudicator','human')),
    mean_confidence    REAL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS human_reviews (
    reddit_fullname TEXT PRIMARY KEY,
    sarcastic       INTEGER,
    language        TEXT,
    literal_sentiment  TEXT,
    intended_sentiment TEXT,
    cue_polarity_inversion     INTEGER,
    cue_rhetorical_intent      INTEGER,
    cue_contextual_incongruity INTEGER,
    cue_hyperbole              INTEGER,
    notes           TEXT,
    is_gold         INTEGER DEFAULT 0,
    reviewed_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_queue (
    reddit_fullname TEXT PRIMARY KEY,
    reason          TEXT NOT NULL CHECK(reason IN ('gold','low_confidence')),
    created_at      TEXT NOT NULL,
    completed       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    prompt_version TEXT PRIMARY KEY,
    content_hash   TEXT NOT NULL,
    first_used_at  TEXT NOT NULL
);
"""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AnnotationDatabase:
    """Manages the annotation SQLite database."""

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

    def __enter__(self) -> AnnotationDatabase:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    # ------------------------------------------------------------------
    # Corpus index
    # ------------------------------------------------------------------

    def upsert_corpus_record(self, row: dict[str, Any]) -> None:
        cols = (
            "reddit_fullname", "record_type", "reddit_id", "subreddit",
            "submission_fullname", "parent_fullname", "depth", "created_utc",
            "author_hash", "author_status", "is_submitter", "is_bot",
            "distinguished", "stickied", "is_self", "score",
            "sampling_strategy", "matched_query_or_keyword",
            "title", "selftext", "body", "text", "permalink", "jsonl_path",
        )
        placeholders = ", ".join("?" for _ in cols) + ", ?"
        self._conn.execute(
            f"INSERT OR REPLACE INTO corpus_index ({', '.join(cols)}, indexed_at) "
            f"VALUES ({placeholders})",
            tuple(row.get(c) for c in cols) + (_utc_now_iso(),),
        )

    def commit(self) -> None:
        self._conn.commit()

    def get_record(self, reddit_fullname: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM corpus_index WHERE reddit_fullname = ?", (reddit_fullname,)
        ).fetchone()
        return dict(row) if row else None

    def children(self, parent_fullname: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM corpus_index WHERE parent_fullname = ? ORDER BY created_utc",
            (parent_fullname,),
        ).fetchall()
        return [dict(r) for r in rows]

    def parent_chain(self, reddit_fullname: str, max_hops: int = 50) -> list[dict[str, Any]]:
        """Ancestor comments from the submission side down to the direct parent.

        Excludes the submission itself; stops early if a parent was not collected.
        """
        chain: list[dict[str, Any]] = []
        current = self.get_record(reddit_fullname)
        for _ in range(max_hops):
            if current is None:
                break
            parent_fullname = current.get("parent_fullname")
            if not parent_fullname or str(parent_fullname).startswith("t3_"):
                break
            parent = self.get_record(str(parent_fullname))
            if parent is None:
                break
            chain.append(parent)
            current = parent
        chain.reverse()
        return chain

    def corpus_counts(self) -> dict[str, int]:
        row = self._conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN record_type='submission' THEN 1 ELSE 0 END) AS submissions,
                   SUM(CASE WHEN record_type='comment' THEN 1 ELSE 0 END) AS comments
            FROM corpus_index
            """
        ).fetchone()
        return {
            "total": row["total"] or 0,
            "submissions": row["submissions"] or 0,
            "comments": row["comments"] or 0,
        }

    # ------------------------------------------------------------------
    # Candidates
    # ------------------------------------------------------------------

    def upsert_candidate(
        self,
        reddit_fullname: str,
        *,
        eligible: bool,
        exclusion_reason: str | None,
        text_norm: str | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO candidates
              (reddit_fullname, eligible, exclusion_reason, text_norm, selected_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (reddit_fullname, int(eligible), exclusion_reason, text_norm, _utc_now_iso()),
        )

    def candidate_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            """
            SELECT COALESCE(exclusion_reason, 'eligible') AS reason, COUNT(*) AS n
            FROM candidates GROUP BY reason ORDER BY n DESC
            """
        ).fetchall()
        return {str(r["reason"]): int(r["n"]) for r in rows}

    def eligible_fullnames(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT reddit_fullname FROM candidates WHERE eligible = 1 ORDER BY reddit_fullname"
        ).fetchall()
        return [str(r["reddit_fullname"]) for r in rows]

    # ------------------------------------------------------------------
    # LID / transformer sentiment
    # ------------------------------------------------------------------

    def upsert_lid(self, reddit_fullname: str, result: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO lid_results
              (reddit_fullname, language, en_ratio, tl_ratio, other_ratio,
               confidence, lid_model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reddit_fullname,
                result["language"],
                result.get("en_ratio"),
                result.get("tl_ratio"),
                result.get("other_ratio"),
                result.get("confidence"),
                result.get("lid_model"),
                _utc_now_iso(),
            ),
        )

    def missing_lid(self) -> list[str]:
        rows = self._conn.execute(
            """
            SELECT c.reddit_fullname FROM candidates c
            WHERE c.eligible = 1 AND NOT EXISTS
              (SELECT 1 FROM lid_results l WHERE l.reddit_fullname = c.reddit_fullname)
            ORDER BY c.reddit_fullname
            """
        ).fetchall()
        return [str(r["reddit_fullname"]) for r in rows]

    def upsert_tx_sentiment(self, reddit_fullname: str, result: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO tx_sentiment
              (reddit_fullname, label, p_pos, p_neu, p_neg,
               model_name, model_revision, device, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reddit_fullname,
                result["label"],
                result.get("p_pos"),
                result.get("p_neu"),
                result.get("p_neg"),
                result.get("model_name"),
                result.get("model_revision"),
                result.get("device"),
                _utc_now_iso(),
            ),
        )

    def missing_tx_sentiment(self) -> list[str]:
        rows = self._conn.execute(
            """
            SELECT c.reddit_fullname FROM candidates c
            WHERE c.eligible = 1 AND NOT EXISTS
              (SELECT 1 FROM tx_sentiment t WHERE t.reddit_fullname = c.reddit_fullname)
            ORDER BY c.reddit_fullname
            """
        ).fetchall()
        return [str(r["reddit_fullname"]) for r in rows]

    # ------------------------------------------------------------------
    # Contexts (snapshot of exactly what annotators saw)
    # ------------------------------------------------------------------

    def get_context(self, reddit_fullname: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM contexts WHERE reddit_fullname = ?", (reddit_fullname,)
        ).fetchone()
        return dict(row) if row else None

    def save_context(
        self,
        reddit_fullname: str,
        prompt_version: str,
        context_json: dict[str, Any],
        context_text: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO contexts
              (reddit_fullname, prompt_version, context_json, context_text, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                reddit_fullname,
                prompt_version,
                json.dumps(context_json, ensure_ascii=False),
                context_text,
                _utc_now_iso(),
            ),
        )

    # ------------------------------------------------------------------
    # LLM annotations
    # ------------------------------------------------------------------

    def insert_llm_annotation(self, row: dict[str, Any]) -> bool:
        """Insert one annotation. Returns False when the resume key already exists."""
        cols = (
            "reddit_fullname", "model_key", "prompt_version", "role",
            "language", "literal_sentiment", "intended_sentiment", "sarcastic",
            "cue_polarity_inversion", "cue_rhetorical_intent",
            "cue_contextual_incongruity", "cue_hyperbole",
            "confidence", "rationale", "raw_json", "model_digest", "endpoint",
            "ollama_version", "options_json", "attempts", "duration_ms",
            "prompt_tokens", "completion_tokens",
        )
        placeholders = ", ".join("?" for _ in cols) + ", ?"
        try:
            self._conn.execute(
                f"INSERT INTO llm_annotations ({', '.join(cols)}, created_at) "
                f"VALUES ({placeholders})",
                tuple(row.get(c) for c in cols) + (_utc_now_iso(),),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def record_failure(
        self, reddit_fullname: str, model_key: str, prompt_version: str, role: str, error: str
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO llm_failures
              (reddit_fullname, model_key, prompt_version, role, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (reddit_fullname, model_key, prompt_version, role, error[:2000], _utc_now_iso()),
        )
        self._conn.commit()

    def clear_failures(self, model_key: str, prompt_version: str, role: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM llm_failures WHERE model_key=? AND prompt_version=? AND role=?",
            (model_key, prompt_version, role),
        )
        self._conn.commit()
        return cur.rowcount

    def pending_annotator_items(
        self, model_key: str, prompt_version: str, *, include_failed: bool = False
    ) -> list[str]:
        failed_clause = (
            ""
            if include_failed
            else """
            AND NOT EXISTS (SELECT 1 FROM llm_failures f
                WHERE f.reddit_fullname = c.reddit_fullname
                  AND f.model_key = :model AND f.prompt_version = :pv AND f.role = 'annotator')
            """
        )
        rows = self._conn.execute(
            f"""
            SELECT c.reddit_fullname FROM candidates c
            WHERE c.eligible = 1
            AND NOT EXISTS (SELECT 1 FROM llm_annotations a
                WHERE a.reddit_fullname = c.reddit_fullname
                  AND a.model_key = :model AND a.prompt_version = :pv AND a.role = 'annotator')
            {failed_clause}
            ORDER BY c.reddit_fullname
            """,
            {"model": model_key, "pv": prompt_version},
        ).fetchall()
        return [str(r["reddit_fullname"]) for r in rows]

    def pending_adjudicator_items(
        self, model_key: str, prompt_version: str, *, include_failed: bool = False
    ) -> list[str]:
        failed_clause = (
            ""
            if include_failed
            else """
            AND NOT EXISTS (SELECT 1 FROM llm_failures f
                WHERE f.reddit_fullname = g.reddit_fullname
                  AND f.model_key = :model AND f.prompt_version = :pv AND f.role = 'adjudicator')
            """
        )
        rows = self._conn.execute(
            f"""
            SELECT g.reddit_fullname FROM aggregates g
            WHERE g.needs_adjudication = 1 AND g.prompt_version = :pv
            AND (g.resolved_by IS NULL OR g.resolved_by != 'human')
            AND NOT EXISTS (SELECT 1 FROM llm_annotations a
                WHERE a.reddit_fullname = g.reddit_fullname
                  AND a.prompt_version = :pv AND a.role = 'adjudicator')
            {failed_clause}
            ORDER BY g.reddit_fullname
            """,
            {"model": model_key, "pv": prompt_version},
        ).fetchall()
        return [str(r["reddit_fullname"]) for r in rows]

    def annotations_for_item(
        self, reddit_fullname: str, prompt_version: str
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM llm_annotations
            WHERE reddit_fullname = ? AND prompt_version = ?
            ORDER BY model_key
            """,
            (reddit_fullname, prompt_version),
        ).fetchall()
        return [dict(r) for r in rows]

    def annotated_items(self, prompt_version: str) -> list[str]:
        rows = self._conn.execute(
            """
            SELECT DISTINCT reddit_fullname FROM llm_annotations
            WHERE prompt_version = ? AND role = 'annotator'
            ORDER BY reddit_fullname
            """,
            (prompt_version,),
        ).fetchall()
        return [str(r["reddit_fullname"]) for r in rows]

    def model_progress(self, prompt_version: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT model_key, role, COUNT(*) AS done, AVG(duration_ms) AS avg_ms
            FROM llm_annotations WHERE prompt_version = ?
            GROUP BY model_key, role ORDER BY model_key
            """,
            (prompt_version,),
        ).fetchall()
        return [dict(r) for r in rows]

    def failure_counts(self, prompt_version: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT model_key, role, COUNT(DISTINCT reddit_fullname) AS failed
            FROM llm_failures WHERE prompt_version = ?
            GROUP BY model_key, role ORDER BY model_key
            """,
            (prompt_version,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Aggregates
    # ------------------------------------------------------------------

    def upsert_aggregate(self, row: dict[str, Any]) -> None:
        cols = (
            "reddit_fullname", "prompt_version", "n_annotators", "sarcasm_votes",
            "votes_json", "sarcastic_final", "language_final", "literal_final",
            "intended_final",
            "cue_polarity_inversion_final", "cue_rhetorical_intent_final",
            "cue_contextual_incongruity_final", "cue_hyperbole_final",
            "needs_adjudication", "needs_human", "resolved_by", "mean_confidence",
        )
        placeholders = ", ".join("?" for _ in cols) + ", ?"
        self._conn.execute(
            f"INSERT OR REPLACE INTO aggregates ({', '.join(cols)}, updated_at) "
            f"VALUES ({placeholders})",
            tuple(row.get(c) for c in cols) + (_utc_now_iso(),),
        )

    def get_aggregate(self, reddit_fullname: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM aggregates WHERE reddit_fullname = ?", (reddit_fullname,)
        ).fetchone()
        return dict(row) if row else None

    def all_aggregates(self, prompt_version: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM aggregates WHERE prompt_version = ? ORDER BY reddit_fullname",
            (prompt_version,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Human review
    # ------------------------------------------------------------------

    def upsert_human_review(self, row: dict[str, Any]) -> None:
        cols = (
            "reddit_fullname", "sarcastic", "language", "literal_sentiment",
            "intended_sentiment",
            "cue_polarity_inversion", "cue_rhetorical_intent",
            "cue_contextual_incongruity", "cue_hyperbole",
            "notes", "is_gold",
        )
        placeholders = ", ".join("?" for _ in cols) + ", ?"
        self._conn.execute(
            f"INSERT OR REPLACE INTO human_reviews ({', '.join(cols)}, reviewed_at) "
            f"VALUES ({placeholders})",
            tuple(row.get(c) for c in cols) + (_utc_now_iso(),),
        )
        self._conn.execute(
            "UPDATE review_queue SET completed = 1 WHERE reddit_fullname = ?",
            (row["reddit_fullname"],),
        )
        self._conn.commit()

    def get_human_review(self, reddit_fullname: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM human_reviews WHERE reddit_fullname = ?", (reddit_fullname,)
        ).fetchone()
        return dict(row) if row else None

    def all_human_reviews(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM human_reviews").fetchall()
        return [dict(r) for r in rows]

    def enqueue_review(self, reddit_fullname: str, reason: str) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO review_queue (reddit_fullname, reason, created_at)
            VALUES (?, ?, ?)
            """,
            (reddit_fullname, reason, _utc_now_iso()),
        )

    def review_queue_items(self, *, only_pending: bool = True) -> list[dict[str, Any]]:
        clause = "WHERE completed = 0" if only_pending else ""
        rows = self._conn.execute(
            f"SELECT * FROM review_queue {clause} ORDER BY reason, reddit_fullname"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Prompt version guard
    # ------------------------------------------------------------------

    def check_prompt_version(self, prompt_version: str, content_hash: str) -> None:
        """Register or verify the content hash for a prompt version.

        Raises RuntimeError when prompt content changed without a version bump —
        mixed prompts inside one version would silently corrupt agreement stats.
        """
        row = self._conn.execute(
            "SELECT content_hash FROM prompt_versions WHERE prompt_version = ?",
            (prompt_version,),
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO prompt_versions (prompt_version, content_hash, first_used_at) "
                "VALUES (?, ?, ?)",
                (prompt_version, content_hash, _utc_now_iso()),
            )
            self._conn.commit()
            return
        if str(row["content_hash"]) != content_hash:
            raise RuntimeError(
                f"Prompt content changed but prompt_version is still {prompt_version!r}. "
                "Bump PROMPT_VERSION in src/uyam/annotate/prompts.py (and annotation.yaml) "
                "before annotating."
            )
