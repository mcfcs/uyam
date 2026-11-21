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
