"""Human review support: stratified gold-subset sampling.

The gold subset validates the LLM ensemble against a native-speaker annotator
(Cohen's kappa, reported in the dataset card). It is sampled AFTER aggregation
so strata reflect the ensemble's own labels: sarcastic_final x language_final
x split x length band, with exact proportional allocation (largest remainder)
and a fixed seed. `split` marks items whose base annotators disagreed on
sarcasm (2-1 / 1-1, later decided by the adjudicator); those strata are
weighted by review.gold_split_oversample so the human sample concentrates
where the ensemble is least certain.

Only eligible, non-phatic items are drawn. Until 2026-11-19 the strata also
split on record_type and subreddit with a forced minimum of one item per
stratum; with ~100 small strata that over-drew long submissions and pulled in
greeting/birthday items, which made the gold set hard to label.

Gold labels are evaluation-only: aggregation never applies them, and the
export ships them alongside the ensemble labels.
"""

from __future__ import annotations

import math
import random
import re
from collections import defaultdict
from typing import Any

from uyam.annotate.config import AnnotationConfig
from uyam.annotate.db import AnnotationDatabase


def is_split_vote(sarcasm_votes: Any) -> bool:
    """True for '2-1', '1-1', '1-0'-style patterns; False for unanimous 'N-0' (N >= 2)."""
    text = str(sarcasm_votes or "")
    if "-" not in text:
        return False
    yes, no = text.split("-", 1)
    try:
        return int(no) > 0 or int(yes) < 2
    except ValueError:
        return False


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")
_LETTER = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿñÑ]")


def sentence_count(text: str | None) -> int:
    """Sentences by punctuation and line breaks (approximate for informal Taglish).

    This counts sentences for the length bands and the published length
    profile, and only pieces with a letter count. It is deliberately not
    review_workflow.split_sentences, the span picker's splitter, which also
    keeps emoji-only pieces and closing quotes. Changing this would change
    the reported band counts.
    """
    return len([p for p in _SENTENCE_SPLIT.split(text or "") if _LETTER.search(p)])


def length_band(text: str | None) -> str:
    """short (<=3 sentences), medium (4-5), long (6+) — the bands in UYAM_LATEST §2.2."""
    n = sentence_count(text)
    return "short" if n <= 3 else "medium" if n <= 5 else "long"


def _allocate(
    weights: dict[tuple[Any, ...], float], sizes: dict[tuple[Any, ...], int], total: int
) -> dict[tuple[Any, ...], int]:
    """Proportional allocation of `total` items, capped by each stratum's size.

    Floors first, then every remaining item goes to the stratum with room
    that is furthest below its exact share (largest remainder). Shares a
    capped stratum can't take flow to the others, so the result sums to
    min(total, sum(sizes)).
    """
    weight_sum = sum(weights.values()) or 1.0
    raw = {k: total * w / weight_sum for k, w in weights.items()}
    quota = {k: min(math.floor(v), sizes[k]) for k, v in raw.items()}
    left = min(total, sum(sizes.values())) - sum(quota.values())
    while left > 0:
        room = [k for k in quota if quota[k] < sizes[k]]
        if not room:
            break
        k = min(room, key=lambda k: (quota[k] - raw[k], str(k)))
        quota[k] += 1
        left -= 1
    return quota


def sample_gold_subset(
    cfg: AnnotationConfig,
    prompt_version: str,
    *,
    size: int | None = None,
    seed: int = 7,
    split_oversample: float | None = None,
    redraw: bool = False,
) -> dict[str, Any]:
    """Fill review_queue(reason='gold') up to the target size.

    Items already in the gold queue count toward the target, so re-running is
    a no-op once the queue is full. `redraw=True` first removes the gold items
    nobody has labeled yet (completed = 0) and draws their replacements;
    labeled gold items are always kept.

    Allocation is proportional over the items being drawn. Kept items are not
    subtracted from their strata's shares, so the final queue is proportional
    only up to the kept items (31 of 300 at the 2026-11-19 redraw, drawn under
    the older record_type x subreddit scheme).
    """
    target = size or cfg.review.gold_size
    weight_split = (
        cfg.review.gold_split_oversample if split_oversample is None else split_oversample
    )
    with AnnotationDatabase(cfg.db_path) as db:
        removed = 0
        if redraw:
            cur = db.conn.execute(
                "DELETE FROM review_queue WHERE reason = 'gold' AND completed = 0"
            )
            removed = int(cur.rowcount or 0)
        kept = int(
            db.conn.execute(
                "SELECT COUNT(*) FROM review_queue WHERE reason = 'gold'"
            ).fetchone()[0]
        )
        queued = {str(r[0]) for r in db.conn.execute("SELECT reddit_fullname FROM review_queue")}
        rows = db.conn.execute(
            """
            SELECT g.reddit_fullname, g.sarcastic_final, g.language_final,
                   g.sarcasm_votes, c.text
            FROM aggregates g
            JOIN corpus_index c ON c.reddit_fullname = g.reddit_fullname
            JOIN candidates k ON k.reddit_fullname = g.reddit_fullname
            WHERE g.prompt_version = ? AND g.sarcastic_final IS NOT NULL
              AND k.eligible = 1 AND COALESCE(k.phatic, 0) = 0
            """,
            (prompt_version,),
        ).fetchall()
        need = max(0, target - kept)
        stats: dict[str, Any] = {
            "sampled": 0,
            "strata": 0,
            "population": len(rows),
            "split_sampled": 0,
            "kept": kept,
            "removed_unlabeled": removed,
            "bands": {},
        }
        strata: dict[tuple[Any, ...], list[str]] = defaultdict(list)
        for r in rows:
            fullname = str(r["reddit_fullname"])
            if fullname in queued:
                continue
            key = (
                r["sarcastic_final"],
                r["language_final"],
                is_split_vote(r["sarcasm_votes"]),
                length_band(r["text"]),
            )
            strata[key].append(fullname)
        stats["strata"] = len(strata)
        if need == 0 or not strata:
            db.commit()
            return stats

        sizes = {k: len(v) for k, v in strata.items()}
        weights = {k: n * (weight_split if k[2] else 1.0) for k, n in sizes.items()}
        quota = _allocate(weights, sizes, min(need, sum(sizes.values())))
        rng = random.Random(seed)
        sampled: list[str] = []
        for key in sorted(strata, key=str):
            members = sorted(strata[key])
            sampled.extend(rng.sample(members, quota[key]))
        rng.shuffle(sampled)

        split_set = {f for key, members in strata.items() if key[2] for f in members}
        band_of = {f: key[3] for key, members in strata.items() for f in members}
        for fullname in sampled:
            db.enqueue_review(fullname, "gold")
        db.commit()
        bands: dict[str, int] = defaultdict(int)
        for f in sampled:
            bands[band_of[f]] += 1
        stats.update(
            sampled=len(sampled),
            split_sampled=sum(1 for f in sampled if f in split_set),
            bands=dict(bands),
        )
        return stats
