"""Fork the annotation database for a re-annotation with a different lineup.

dataset-v3 (adviser, 2026-11): remove model size as a hidden variable by
labeling with equal-size annotators. The fork never touches the source
database. It writes a new SQLite file that:

- keeps everything shared (corpus index, candidates, context snapshots, LID,
  transformer sentiment, the human gold reviews and queue, assist
  suggestions), so the new lineup sees exactly the context the old one saw;
- keeps only the annotator votes of the models that stay in the lineup
  under the current prompt version, and drops every other vote and failure
  (other annotators, the adjudicator, older prompt versions), plus the
  aggregates (recomputed under the new rules);
- pins the target set with `target_priority`: the items the source shipped
  (priority 2) plus a seeded random sample of not-yet-labeled eligible,
  non-phatic items per calendar month (priority 1). Sampling is uniform
  within a month, so it keeps the natural subreddit and sarcasm mix.

Run it once; `dst` must not exist yet.
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uyam.annotate.db import AnnotationDatabase

BASE_PRIORITY = 2
EXTENSION_PRIORITY = 1


def parse_extend(specs: list[str]) -> dict[str, int]:
    """["2026-06:1600", ...] -> {"2026-06": 1600, ...}."""
    out: dict[str, int] = {}
    for spec in specs:
        month, _, count = spec.partition(":")
        if len(month) != 7 or month[4] != "-" or not count.isdigit() or int(count) <= 0:
            raise ValueError(f"--extend expects YYYY-MM:N with N > 0, got {spec!r}")
        if month in out:
            raise ValueError(f"--extend lists {month} twice")
        out[month] = int(count)
    return out


def fork_for_reannotation(
    src: Path,
    dst: Path,
    *,
    prompt_version: str,
    keep_annotators: list[str],
    extend: dict[str, int],
    seed: int = 13,
) -> dict[str, Any]:
    """Copy ``src`` to ``dst`` and prepare it for the new lineup (see module doc)."""
    if dst.exists():
        raise FileExistsError(f"{dst} already exists; the fork is written once")
    if not keep_annotators:
        raise ValueError("keep at least one annotator, or start from an empty database")
    dst.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)  # consistent copy even if src is in WAL mode
    finally:
        target.close()
        source.close()

    stats: dict[str, Any] = {"extension": {}, "extension_short": {}}
    keep = sorted(set(keep_annotators))
    marks = ",".join("?" for _ in keep)
    with AnnotationDatabase(dst) as db:
        conn = db.conn
        base = [
            str(r[0])
            for r in conn.execute(
                "SELECT reddit_fullname FROM aggregates "
                "WHERE prompt_version = ? AND sarcastic_final IS NOT NULL "
                "ORDER BY reddit_fullname",
                (prompt_version,),
            )
        ]
        stats["base"] = len(base)
        # Everything but the kept lineup's votes under this prompt version goes,
        # including votes left over from older prompt versions (pilots).
        stats["dropped_votes"] = conn.execute(
            f"DELETE FROM llm_annotations WHERE NOT (prompt_version = ? "
            f"AND role = 'annotator' AND model_key IN ({marks}))",
            (prompt_version, *keep),
        ).rowcount
        stats["dropped_failures"] = conn.execute(
            f"DELETE FROM llm_failures WHERE NOT (prompt_version = ? "
            f"AND role = 'annotator' AND model_key IN ({marks}))",
            (prompt_version, *keep),
        ).rowcount
        stats["dropped_aggregates"] = conn.execute("DELETE FROM aggregates").rowcount
        # A low-confidence queue item was the old adjudicator's call; it doesn't
        # carry over. Gold items and every human review stay.
        conn.execute("DELETE FROM review_queue WHERE reason = 'low_confidence' AND completed = 0")
        conn.execute("DELETE FROM target_priority")
        conn.executemany(
            "INSERT INTO target_priority (reddit_fullname, priority, reason) VALUES (?, ?, ?)",
            [(f, BASE_PRIORITY, "source-labeled") for f in base],
        )

        taken = set(base)
        rng = random.Random(seed)
        for month in sorted(extend):
            pool = [
                str(r[0])
                for r in conn.execute(
                    """
                    SELECT c.reddit_fullname FROM candidates c
                    JOIN corpus_index ci ON ci.reddit_fullname = c.reddit_fullname
                    WHERE c.eligible = 1 AND COALESCE(c.phatic, 0) = 0
                      AND substr(ci.created_utc, 1, 7) = ?
                    ORDER BY c.reddit_fullname
                    """,
                    (month,),
                )
                if str(r[0]) not in taken
            ]
            want = extend[month]
            drawn = rng.sample(pool, min(want, len(pool)))
            if len(drawn) < want:
                stats["extension_short"][month] = want - len(drawn)
            conn.executemany(
                "INSERT INTO target_priority (reddit_fullname, priority, reason) "
                "VALUES (?, ?, ?)",
                [(f, EXTENSION_PRIORITY, f"extension-{month}") for f in drawn],
            )
            taken.update(drawn)
            stats["extension"][month] = len(drawn)

        stats["target_total"] = len(taken)
        stats["kept_annotators"] = keep
        conn.execute(
            "CREATE TABLE IF NOT EXISTS fork_info (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        info = {
            "source": str(src),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "prompt_version": prompt_version,
            "seed": seed,
            "extend": extend,
            **{k: v for k, v in stats.items() if k != "extension_short"},
        }
        conn.executemany(
            "INSERT OR REPLACE INTO fork_info (key, value) VALUES (?, ?)",
            [(k, json.dumps(v)) for k, v in info.items()],
        )
        db.commit()
    return stats


def sync_gold(src: Path, dst: Path) -> dict[str, int]:
    """Mirror the human side of the gold set from ``src`` into a fork ``dst``.

    Gio labels gold items in the main database (the labeling site writes
    there), so a fork falls behind as he works. This copies, for the gold
    items both databases share, the human reviews, the queue's completed flag
    and the assist suggestions. It never touches votes, aggregates or the
    target set, and ``src`` is opened read-only. Safe to re-run.
    """
    source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    try:
        src_gold = {
            str(r[0])
            for r in source.execute(
                "SELECT reddit_fullname FROM review_queue WHERE reason = 'gold'"
            )
        }
        with AnnotationDatabase(dst) as db:
            conn = db.conn
            dst_gold = {
                str(r[0])
                for r in conn.execute(
                    "SELECT reddit_fullname FROM review_queue WHERE reason = 'gold'"
                )
            }
            if src_gold != dst_gold:
                raise ValueError(
                    f"gold queues differ ({len(src_gold - dst_gold)} only in {src.name}, "
                    f"{len(dst_gold - src_gold)} only in {dst.name}); redraw one to match first"
                )
            stats = {"reviews_added": 0, "reviews_updated": 0, "reviews_unchanged": 0}
            _mirror_rows(source, conn, "human_reviews", dst_gold, stats, "is_gold = 1")
            assist = {"reviews_added": 0, "reviews_updated": 0, "reviews_unchanged": 0}
            _mirror_rows(source, conn, "assist_suggestions", dst_gold, assist, "1 = 1")
            stats["assist_copied"] = assist["reviews_added"] + assist["reviews_updated"]
            done = [
                (int(r[1]), str(r[0]))
                for r in source.execute(
                    "SELECT reddit_fullname, completed FROM review_queue WHERE reason = 'gold'"
                )
            ]
            conn.executemany(
                "UPDATE review_queue SET completed = ? WHERE reddit_fullname = ? "
                "AND reason = 'gold'",
                done,
            )
            stats["gold_completed"] = sum(c for c, _ in done)
            stats["dst_only_reviews"] = int(
                conn.execute(
                    "SELECT COUNT(*) FROM human_reviews WHERE is_gold = 1 AND reddit_fullname "
                    f"NOT IN ({','.join('?' for _ in dst_gold) or 'NULL'})",
                    sorted(dst_gold),
                ).fetchone()[0]
            )
            db.commit()
    finally:
        source.close()
    return stats


def _mirror_rows(
    source: sqlite3.Connection,
    conn: sqlite3.Connection,
    table: str,
    keep: set[str],
    stats: dict[str, int],
    where: str,
) -> None:
    """INSERT OR REPLACE ``table`` rows for ``keep`` ids, on the shared columns."""
    src_cols = [str(r[1]) for r in source.execute(f"PRAGMA table_info({table})")]
    dst_cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
    cols = [c for c in src_cols if c in dst_cols]
    col_sql = ", ".join(cols)
    key = cols.index("reddit_fullname")
    current = {str(r[key]): tuple(r) for r in conn.execute(f"SELECT {col_sql} FROM {table}")}
    rows = []
    for r in source.execute(f"SELECT {col_sql} FROM {table} WHERE {where}"):
        row = tuple(r)
        name = str(row[key])
        if name not in keep:
            continue
        old = current.get(name)
        if old == row:
            stats["reviews_unchanged"] += 1
            continue
        stats["reviews_updated" if old is not None else "reviews_added"] += 1
        rows.append(row)
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({col_sql}) VALUES ({', '.join('?' for _ in cols)})",
        rows,
    )


def fork_subset(
    src: Path,
    dst: Path,
    *,
    base_prompt_version: str,
    size: int,
    seed: int = 13,
) -> dict[str, Any]:
    """Fork a stratified subset of a labeled dataset for a re-annotation pilot (v4).

    The target set is every gold item plus a seeded sample of the source's
    labeled items (aggregates under ``base_prompt_version``), allocated in
    proportion to calendar month x source sarcasm label x source language
    (largest remainder), so the subset keeps the source's mix. Every vote,
    failure and aggregate is dropped: the pilot labels from scratch under its
    own prompt version. Context snapshots, gold reviews, LID and transformer
    sentiment carry over. ``src`` is never changed; ``dst`` must not exist.
    """
    if dst.exists():
        raise FileExistsError(f"{dst} already exists; the fork is written once")
    dst.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    with AnnotationDatabase(dst) as db:
        conn = db.conn
        pool = {
            str(r[0]): (str(r[1])[:7], int(r[2]), str(r[3]))
            for r in conn.execute(
                """
                SELECT a.reddit_fullname, ci.created_utc, a.sarcastic_final, a.language_final
                FROM aggregates a JOIN corpus_index ci ON ci.reddit_fullname = a.reddit_fullname
                WHERE a.prompt_version = ? AND a.sarcastic_final IS NOT NULL
                """,
                (base_prompt_version,),
            )
        }
        gold = sorted(
            str(r[0])
            for r in conn.execute("SELECT reddit_fullname FROM review_queue WHERE reason = 'gold'")
        )
        rest_n = size - len(gold)
        if rest_n < 0:
            raise ValueError(f"size {size} is smaller than the {len(gold)} gold items")
        strata: dict[tuple[str, int, str], list[str]] = {}
        for name in sorted(set(pool) - set(gold)):
            strata.setdefault(pool[name], []).append(name)
        total = sum(len(v) for v in strata.values())
        if rest_n > total:
            raise ValueError(f"size {size} needs {rest_n} non-gold items; the source has {total}")
        quotas = {k: rest_n * len(v) / total for k, v in strata.items()}
        alloc = {k: int(q) for k, q in quotas.items()}
        leftover = rest_n - sum(alloc.values())
        for k in sorted(quotas, key=lambda k: (alloc[k] - quotas[k], k))[:leftover]:
            alloc[k] += 1
        rng = random.Random(seed)
        drawn: list[str] = []
        for k in sorted(strata):
            drawn.extend(rng.sample(strata[k], alloc[k]))

        stats: dict[str, Any] = {
            "gold": len(gold),
            "gold_outside_source": sum(1 for g in gold if g not in pool),
            "sampled": len(drawn),
            "target_total": len(gold) + len(drawn),
            "source_items": len(pool),
            "sampled_sarcastic": sum(pool[f][1] for f in drawn),
        }
        stats["dropped_votes"] = conn.execute("DELETE FROM llm_annotations").rowcount
        conn.execute("DELETE FROM llm_failures")
        stats["dropped_aggregates"] = conn.execute("DELETE FROM aggregates").rowcount
        conn.execute("DELETE FROM review_queue WHERE reason = 'low_confidence' AND completed = 0")
        conn.execute("DELETE FROM target_priority")
        conn.executemany(
            "INSERT INTO target_priority (reddit_fullname, priority, reason) VALUES (?, ?, ?)",
            [(f, BASE_PRIORITY, "gold") for f in gold]
            + [(f, EXTENSION_PRIORITY, f"subset-{base_prompt_version}") for f in drawn],
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS fork_info (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute("DELETE FROM fork_info")
        info = {
            "source": str(src),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "kind": "subset",
            "base_prompt_version": base_prompt_version,
            "size": size,
            "seed": seed,
            **stats,
        }
        conn.executemany(
            "INSERT OR REPLACE INTO fork_info (key, value) VALUES (?, ?)",
            [(k, json.dumps(v)) for k, v in info.items()],
        )
        db.commit()
    return stats
