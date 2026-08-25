"""Human review support: stratified gold-subset sampling.

The gold subset validates the LLM ensemble against a native-speaker annotator
(Cohen's kappa, reported in the dataset card). It is sampled AFTER aggregation
so strata reflect the ensemble's own labels: sarcastic_final x language_final
x record_type x subreddit, proportional allocation with at least one item per
non-empty stratum, seeded for reproducibility.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any

from uyam.annotate.config import AnnotationConfig
from uyam.annotate.db import AnnotationDatabase


def sample_gold_subset(
    cfg: AnnotationConfig, prompt_version: str, *, size: int | None = None, seed: int = 7
) -> dict[str, Any]:
    """Draw the stratified gold sample into review_queue(reason='gold')."""
    target = size or cfg.review.gold_size
    with AnnotationDatabase(cfg.db_path) as db:
        rows = db.conn.execute(
            """
            SELECT g.reddit_fullname, g.sarcastic_final, g.language_final,
                   c.record_type, c.subreddit
            FROM aggregates g JOIN corpus_index c ON c.reddit_fullname = g.reddit_fullname
            WHERE g.prompt_version = ? AND g.sarcastic_final IS NOT NULL
            """,
            (prompt_version,),
        ).fetchall()
        if not rows:
            return {"sampled": 0, "strata": 0, "population": 0}

        strata: dict[tuple[Any, ...], list[str]] = defaultdict(list)
        for r in rows:
            key = (
                r["sarcastic_final"],
                r["language_final"],
                r["record_type"],
                r["subreddit"],
            )
            strata[key].append(str(r["reddit_fullname"]))

        population = len(rows)
        rng = random.Random(seed)
        sampled: list[str] = []
        # At least one per stratum, remainder proportional to stratum size.
        for key in sorted(strata, key=str):
            members = sorted(strata[key])
            quota = max(1, round(target * len(members) / population))
            quota = min(quota, len(members))
            sampled.extend(rng.sample(members, quota))
        rng.shuffle(sampled)
        sampled = sampled[:target]

        for fullname in sampled:
            db.enqueue_review(fullname, "gold")
        db.commit()
        return {"sampled": len(sampled), "strata": len(strata), "population": population}
