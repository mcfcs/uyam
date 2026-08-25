"""Adjudication pass: the large model re-annotates escalated items blind.

The adjudicator never sees the three annotator outputs (same prompt, same
schema) — it is a genuine fourth opinion, not a tie-break anchored on the
others. Its labels replace all labels for escalated items at the next
`annotate aggregate` run.

Run `annotate aggregate` BEFORE this pass (it populates the escalation queue)
and again AFTER it (to apply the adjudicator's labels).
"""

from __future__ import annotations

from uyam.annotate.config import AnnotationConfig
from uyam.annotate.runner import run_annotator


def run_adjudication(
    cfg: AnnotationConfig,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    retry_failed: bool = False,
) -> dict[str, int]:
    return run_annotator(
        cfg,
        cfg.adjudicator.key,
        role="adjudicator",
        limit=limit,
        dry_run=dry_run,
        retry_failed=retry_failed,
    )
