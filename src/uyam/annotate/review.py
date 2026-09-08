"""Human review support: stratified gold-subset sampling.

The gold subset validates the LLM ensemble against a native-speaker annotator
(Cohen's kappa, reported in the dataset card). It is sampled AFTER aggregation
so strata reflect the ensemble's own labels: sarcastic_final x language_final
x record_type x subreddit x split, proportional allocation with at least one
item per non-empty stratum, seeded for reproducibility. `split` marks items
whose base annotators disagreed on sarcasm (2-1 / 1-1, later decided by the
adjudicator); those strata are weighted by review.gold_split_oversample so
the human sample concentrates where the ensemble is least certain.

Gold labels are evaluation-only: aggregation never applies them, and the
export ships them alongside the ensemble labels.
"""

from __future__ import annotations

import random
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


def sample_gold_subset(
    cfg: AnnotationConfig,
    prompt_version: str,
    *,
    size: int | None = None,
    seed: int = 7,
    split_oversample: float | None = None,
) -> dict[str, Any]:
    """Draw the stratified gold sample into review_queue(reason='gold')."""
    target = size or cfg.review.gold_size
    weight_split = (
        cfg.review.gold_split_oversample if split_oversample is None else split_oversample
    )
    with AnnotationDatabase(cfg.db_path) as db:
        rows = db.conn.execute(
            """
            SELECT g.reddit_fullname, g.sarcastic_final, g.language_final,
                   g.sarcasm_votes, c.record_type, c.subreddit
            FROM aggregates g JOIN corpus_index c ON c.reddit_fullname = g.reddit_fullname
            WHERE g.prompt_version = ? AND g.sarcastic_final IS NOT NULL
            """,
            (prompt_version,),
        ).fetchall()
        if not rows:
            return {"sampled": 0, "strata": 0, "population": 0, "split_sampled": 0}

        strata: dict[tuple[Any, ...], list[str]] = defaultdict(list)
        for r in rows:
            key = (
                r["sarcastic_final"],
                r["language_final"],
                r["record_type"],
                r["subreddit"],
                is_split_vote(r["sarcasm_votes"]),
            )
            strata[key].append(str(r["reddit_fullname"]))

        population = len(rows)
        weights = {
            key: len(members) * (weight_split if key[-1] else 1.0)
            for key, members in strata.items()
        }
        total_weight = sum(weights.values())
        rng = random.Random(seed)
        sampled: list[str] = []
        # At least one per stratum, remainder proportional to (weighted) stratum size.
        for key in sorted(strata, key=str):
            members = sorted(strata[key])
            quota = max(1, round(target * weights[key] / total_weight))
            quota = min(quota, len(members))
            sampled.extend(rng.sample(members, quota))
        rng.shuffle(sampled)
        sampled = sampled[:target]
        # A weighted stratum capped by its own size leaves the target
        # under-filled: top up from the members not yet picked so the sample
        # always holds min(target, population) items.
        if len(sampled) < target:
            picked = set(sampled)
            leftovers = sorted(f for members in strata.values() for f in members if f not in picked)
            rng.shuffle(leftovers)
            sampled.extend(leftovers[: target - len(sampled)])
        split_set = {f for key, members in strata.items() if key[-1] for f in members}
        split_sampled = sum(1 for f in sampled if f in split_set)

        for fullname in sampled:
            db.enqueue_review(fullname, "gold")
        db.commit()
        return {
            "sampled": len(sampled),
            "strata": len(strata),
            "population": population,
            "split_sampled": split_sampled,
        }
