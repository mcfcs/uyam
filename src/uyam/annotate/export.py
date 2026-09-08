"""Final dataset export for the leische (model) repository.

Produces, under data/annotated/:
  dataset-<version>.jsonl    one row per resolved annotation target
  dataset-<version>.parquet  same rows (needs pandas+pyarrow; skipped otherwise)
  corpus-<version>.jsonl     pseudonymized corpus dump (temporal-context source)
  dataset_card.json          counts, distributions, agreement stats, provenance

Every row is built from an explicit whitelist — the raw JSONL contains real
usernames in `author`, which must never reach an export artifact. Only
`author_hash` ships.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uyam.annotate.aggregate import compute_agreement, per_label_resolution
from uyam.annotate.config import AnnotationConfig
from uyam.annotate.db import AnnotationDatabase

logger = logging.getLogger(__name__)

_CUE_FINAL_COLS = {
    "polarity_inversion": "cue_polarity_inversion_final",
    "rhetorical_intent": "cue_rhetorical_intent_final",
    "contextual_incongruity": "cue_contextual_incongruity_final",
    "hyperbole": "cue_hyperbole_final",
}
_CUE_COLS = {
    "polarity_inversion": "cue_polarity_inversion",
    "rhetorical_intent": "cue_rhetorical_intent",
    "contextual_incongruity": "cue_contextual_incongruity",
    "hyperbole": "cue_hyperbole",
}


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=Path(__file__).parent,
            check=False,
        )
        commit = out.stdout.strip()
        return commit or None
    except OSError:
        return None


def is_text_only(record: dict[str, Any]) -> bool:
    """Thesis §3.2: multimodal-only content (image/link posts with no body) is out.

    Comments are always text. A submission counts as text-only when it is a
    self post or carries a non-empty selftext.
    """
    if record.get("record_type") != "submission":
        return True
    if record.get("is_self"):
        return True
    return bool(str(record.get("selftext") or "").strip())


def _annotation_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_key": row["model_key"],
        "model_digest": row.get("model_digest"),
        "sarcastic": None if row.get("sarcastic") is None else bool(row["sarcastic"]),
        "language": row.get("language"),
        "literal_sentiment": row.get("literal_sentiment"),
        "intended_sentiment": row.get("intended_sentiment"),
        "cues": {name: bool(row.get(col) or 0) for name, col in _CUE_COLS.items()},
        "confidence": row.get("confidence"),
        "rationale": row.get("rationale"),
    }


def build_export_row(
    db: AnnotationDatabase, agg: dict[str, Any], dataset_version: str
) -> dict[str, Any] | None:
    fullname = str(agg["reddit_fullname"])
    record = db.get_record(fullname)
    if record is None:
        return None

    annotations = db.annotations_for_item(fullname, str(agg["prompt_version"]))
    raw_annotators = [r for r in annotations if r["role"] == "annotator"]
    raw_adjudicators = [r for r in annotations if r["role"] == "adjudicator"]
    annotator_rows = [_annotation_view(r) for r in raw_annotators]
    adjudicator_rows = [_annotation_view(r) for r in raw_adjudicators]

    # The context block the annotator prompt was given, verbatim — never a
    # rebuild from the corpus dump (the labels were conditioned on THIS).
    ctx_row = db.get_context(fullname)
    if ctx_row:
        context = {"source": "annotator_snapshot", **json.loads(str(ctx_row["context_json"]))}
    else:
        context = {"source": "missing", "submission": None, "parent_chain": [], "replies": []}

    lid = db.conn.execute(
        "SELECT * FROM lid_results WHERE reddit_fullname = ?", (fullname,)
    ).fetchone()
    tx = db.conn.execute(
        "SELECT * FROM tx_sentiment WHERE reddit_fullname = ?", (fullname,)
    ).fetchone()
    human = db.get_human_review(fullname)

    return {
        "reddit_fullname": fullname,
        "record_type": record["record_type"],
        "subreddit": record["subreddit"],
        "permalink": record.get("permalink"),
        "created_utc": record.get("created_utc"),
        "submission_fullname": record.get("submission_fullname"),
        "parent_fullname": record.get("parent_fullname"),
        "depth": record.get("depth"),
        "author_hash": record.get("author_hash"),
        "is_submitter": record.get("is_submitter"),
        "score": record.get("score"),
        "sampling_strategy": record.get("sampling_strategy"),
        "matched_query_or_keyword": record.get("matched_query_or_keyword"),
        "is_text_only": is_text_only(record),
        "title": record.get("title"),
        "selftext": record.get("selftext"),
        "text": record["text"],
        "labels": {
            "sarcastic": (
                None if agg.get("sarcastic_final") is None else bool(agg["sarcastic_final"])
            ),
            "language": agg.get("language_final"),
            "literal_sentiment": agg.get("literal_final"),
            "intended_sentiment": agg.get("intended_final"),
            "cues": {
                name: bool(agg.get(col) or 0) for name, col in _CUE_FINAL_COLS.items()
            },
        },
        "reliability": {
            "resolved_by": agg.get("resolved_by"),
            "per_label": per_label_resolution(raw_annotators, raw_adjudicators, human),
            "sarcasm_votes": agg.get("sarcasm_votes"),
            "n_annotators": agg.get("n_annotators"),
            "mean_confidence": agg.get("mean_confidence"),
            "needs_human": bool(agg.get("needs_human") or 0),
            "annotators": annotator_rows,
            "adjudicator": adjudicator_rows[0] if adjudicator_rows else None,
        },
        "human_gold": (
            {
                "sarcastic": None if human.get("sarcastic") is None else bool(human["sarcastic"]),
                "language": human.get("language"),
                "literal_sentiment": human.get("literal_sentiment"),
                "intended_sentiment": human.get("intended_sentiment"),
                "cues": {name: bool(human.get(col) or 0) for name, col in _CUE_COLS.items()},
                "notes": human.get("notes"),
            }
            if human is not None and human.get("is_gold")
            else None
        ),
        "aux": {
            "tx_sentiment": (
                {
                    "label": tx["label"],
                    "p_pos": tx["p_pos"],
                    "p_neu": tx["p_neu"],
                    "p_neg": tx["p_neg"],
                    "model": tx["model_name"],
                }
                if tx
                else None
            ),
            "lid": (
                {
                    "auto_label": lid["language"],
                    "language": lid["language"],
                    "en_ratio": lid["en_ratio"],
                    "tl_ratio": lid["tl_ratio"],
                    "other_ratio": lid["other_ratio"],
                    "confidence": lid["confidence"],
                }
                if lid
                else None
            ),
        },
        "context": context,
        "provenance": {
            "prompt_version": agg.get("prompt_version"),
            "dataset_version": dataset_version,
        },
    }


def _corpus_dump_row(record: dict[str, Any]) -> dict[str, Any]:
    """Whitelisted corpus row — enough to rebuild author history (temporal
    context) and thread structure in leische, nothing identity-bearing."""
    return {
        "reddit_fullname": record["reddit_fullname"],
        "record_type": record["record_type"],
        "subreddit": record["subreddit"],
        "submission_fullname": record.get("submission_fullname"),
        "parent_fullname": record.get("parent_fullname"),
        "depth": record.get("depth"),
        "created_utc": record.get("created_utc"),
        "author_hash": record.get("author_hash"),
        "is_submitter": record.get("is_submitter"),
        "score": record.get("score"),
        "sampling_strategy": record.get("sampling_strategy"),
        "matched_query_or_keyword": record.get("matched_query_or_keyword"),
        "is_text_only": is_text_only(record),
        "title": record.get("title"),
        "selftext": record.get("selftext"),
        "text": record["text"],
        "permalink": record.get("permalink"),
    }


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def temporal_context_coverage(
    db: AnnotationDatabase,
    fullnames: list[str],
    *,
    window_hours: float = 48.0,
    max_posts: int = 5,
) -> dict[str, Any]:
    """Thesis §3.4(2): up to `max_posts` same-author posts within `window_hours`
    BEFORE the target, drawn from the collected corpus only. Reports what share
    of the exported rows actually have that history — the temporal channel is
    only as thick as the collection window (handoff H8)."""
    import bisect
    from collections import defaultdict
    from datetime import timedelta

    by_author: dict[str, list[datetime]] = defaultdict(list)
    for row in db.conn.execute(
        "SELECT author_hash, created_utc FROM corpus_index "
        "WHERE author_hash IS NOT NULL AND created_utc IS NOT NULL"
    ):
        ts = _parse_utc(row["created_utc"])
        if ts is not None:
            by_author[str(row["author_hash"])].append(ts)
    for stamps in by_author.values():
        stamps.sort()

    counts: list[int] = []
    for fullname in fullnames:
        record = db.get_record(fullname)
        if record is None:
            continue
        ts = _parse_utc(record.get("created_utc"))
        author = record.get("author_hash")
        if ts is None or not author:
            counts.append(0)
            continue
        stamps = by_author.get(str(author), [])
        lo = bisect.bisect_left(stamps, ts - timedelta(hours=window_hours))
        hi = bisect.bisect_left(stamps, ts)
        counts.append(min(max_posts, max(0, hi - lo)))

    n = len(counts)
    share = lambda k: (round(sum(1 for c in counts if c >= k) / n, 4) if n else None)  # noqa: E731
    return {
        "rule": f"<= {max_posts} same-author posts within {window_hours:g} h before the target",
        "n_rows": n,
        "share_with_at_least_1": share(1),
        "share_with_at_least_3": share(3),
        "share_with_full_5": share(max_posts),
        "mean_available": round(sum(counts) / n, 3) if n else None,
    }


def _collection_window(db: AnnotationDatabase, fullnames: list[str]) -> dict[str, Any]:
    corpus = db.conn.execute(
        "SELECT MIN(created_utc) AS lo, MAX(created_utc) AS hi FROM corpus_index "
        "WHERE created_utc >= '2000'"
    ).fetchone()
    exported: dict[str, str | None] = {"from": None, "to": None}
    if fullnames:
        stamps = sorted(
            str(r["created_utc"])
            for r in db.conn.execute(
                "SELECT created_utc FROM corpus_index WHERE created_utc IS NOT NULL"
            )
            if str(r["created_utc"]) >= "2000"
        )
        keep = set(fullnames)
        stamps = [
            str(r["created_utc"])
            for r in db.conn.execute(
                "SELECT reddit_fullname, created_utc FROM corpus_index "
                "WHERE created_utc IS NOT NULL"
            )
            if str(r["reddit_fullname"]) in keep
        ]
        if stamps:
            exported = {"from": min(stamps), "to": max(stamps)}
    return {
        "corpus": {"from": corpus["lo"], "to": corpus["hi"]},
        "exported_rows": exported,
    }


def _label_distributions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def count_by(key_fn: Any) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in rows:
            key = str(key_fn(row))
            out[key] = out.get(key, 0) + 1
        return dict(sorted(out.items()))

    return {
        "sarcastic": count_by(lambda r: r["labels"]["sarcastic"]),
        "language": count_by(lambda r: r["labels"]["language"]),
        "literal_sentiment": count_by(lambda r: r["labels"]["literal_sentiment"]),
        "intended_sentiment": count_by(lambda r: r["labels"]["intended_sentiment"]),
        "resolved_by": count_by(lambda r: r["reliability"]["resolved_by"]),
        "subreddit": count_by(lambda r: r["subreddit"]),
        "record_type": count_by(lambda r: r["record_type"]),
        "sampling_strategy": count_by(lambda r: r["sampling_strategy"]),
        "sarcastic_by_language": count_by(
            lambda r: f"{r['labels']['language']}|sarcastic={r['labels']['sarcastic']}"
        ),
        "is_text_only": count_by(lambda r: r["is_text_only"]),
        "sarcastic_per_label_resolution": count_by(
            lambda r: r["reliability"]["per_label"]["sarcastic"]
        ),
    }


def _readiness(rows: list[dict[str, Any]], agreement: dict[str, Any]) -> dict[str, Any]:
    """The numbers the model repository's readiness gate checks (no verdicts here)."""
    positives = sum(1 for r in rows if r["labels"]["sarcastic"] is True)
    cells: dict[str, int] = {}
    for r in rows:
        key = f"{r['labels']['language']}|{r['labels']['sarcastic']}"
        cells[key] = cells.get(key, 0) + 1
    gold = agreement.get("gold_vs_ensemble_cohen_kappa") or {}
    return {
        "exported_rows": len(rows),
        "sarcastic_positives": positives,
        "min_language_x_sarcastic_cell": min(cells.values()) if cells else 0,
        "language_x_sarcastic_cells": dict(sorted(cells.items())),
        "gold_items_labeled": gold.get("n_gold_items", 0),
        "gold_sarcastic_cohen_kappa": gold.get("sarcastic"),
        "rows_with_annotator_snapshot_context": sum(
            1 for r in rows if r["context"].get("source") == "annotator_snapshot"
        ),
    }


def run_export(
    cfg: AnnotationConfig,
    prompt_version: str,
    *,
    dataset_version: str = "v1",
    formats: tuple[str, ...] = ("jsonl", "parquet"),
    include_unresolved: bool = False,
) -> dict[str, Any]:
    out_dir = cfg.export.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    with AnnotationDatabase(cfg.db_path) as db:
        aggregates = db.all_aggregates(prompt_version)
        rows: list[dict[str, Any]] = []
        skipped_unresolved = 0
        for agg in aggregates:
            if not include_unresolved and agg.get("resolved_by") is None:
                skipped_unresolved += 1
                continue
            row = build_export_row(db, agg, dataset_version)
            if row is not None:
                rows.append(row)

        jsonl_path = out_dir / f"dataset-{dataset_version}.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        parquet_path: Path | None = None
        if "parquet" in formats and rows:
            try:
                import pandas as pd

                parquet_path = out_dir / f"dataset-{dataset_version}.parquet"
                flat = [
                    {
                        **{k: v for k, v in row.items() if not isinstance(v, dict)},
                        **{f"label_{k}": v for k, v in row["labels"].items() if k != "cues"},
                        **{f"cue_{k}": v for k, v in row["labels"]["cues"].items()},
                        "resolved_by": row["reliability"]["resolved_by"],
                        "sarcasm_votes": row["reliability"]["sarcasm_votes"],
                        "mean_confidence": row["reliability"]["mean_confidence"],
                        "reliability_json": json.dumps(row["reliability"], ensure_ascii=False),
                        "human_gold_json": json.dumps(row["human_gold"], ensure_ascii=False),
                        "aux_json": json.dumps(row["aux"], ensure_ascii=False),
                        "context_json": json.dumps(row["context"], ensure_ascii=False),
                    }
                    for row in rows
                ]
                pd.DataFrame(flat).to_parquet(parquet_path, index=False)
            except ImportError:
                logger.warning("parquet_skipped_missing_pandas_pyarrow")
                parquet_path = None

        corpus_path = out_dir / f"corpus-{dataset_version}.jsonl"
        corpus_rows = db.conn.execute("SELECT * FROM corpus_index").fetchall()
        with corpus_path.open("w", encoding="utf-8") as fh:
            for record in corpus_rows:
                fh.write(json.dumps(_corpus_dump_row(dict(record)), ensure_ascii=False) + "\n")

        annotator_provenance = [
            dict(r)
            for r in db.conn.execute(
                """
                SELECT model_key, role, model_digest, endpoint, ollama_version,
                       COUNT(*) AS annotations
                FROM llm_annotations WHERE prompt_version = ?
                GROUP BY model_key, role, model_digest, endpoint, ollama_version
                """,
                (prompt_version,),
            ).fetchall()
        ]
        candidate_counts = db.candidate_counts()
        corpus_counts = db.corpus_counts()
        exported_fullnames = [str(r["reddit_fullname"]) for r in rows]
        collection_window = _collection_window(db, exported_fullnames)
        temporal_coverage = temporal_context_coverage(db, exported_fullnames)

    agreement = compute_agreement(cfg, prompt_version)
    card = {
        "dataset_version": dataset_version,
        "prompt_version": prompt_version,
        "created_at": datetime.now(UTC).isoformat(),
        "uyam_commit": _git_commit(),
        "counts": {
            "corpus": corpus_counts,
            "candidates": candidate_counts,
            "exported_rows": len(rows),
            "skipped_unresolved": skipped_unresolved,
        },
        "label_distributions": _label_distributions(rows) if rows else {},
        "agreement": agreement,
        "readiness": _readiness(rows, agreement),
        "collection_window": collection_window,
        "temporal_context_coverage": temporal_coverage,
        "annotator_provenance": annotator_provenance,
        "candidate_filters": {
            "min_chars": cfg.candidate_filters.min_chars,
            "min_tokens": cfg.candidate_filters.min_tokens,
            "exclude_authors": cfg.candidate_filters.exclude_authors,
            "include_link_posts": cfg.candidate_filters.include_link_posts,
        },
        "notes": [
            "Labels are LLM-ensemble annotations (3 annotators, unanimity-or-escalate on "
            "sarcasm, majority on 3-class labels, blind large-model adjudication, human "
            "final call on low-confidence items).",
            "intended_sentiment is the ground-truth sentiment for RQ3; literal_sentiment "
            "is the surface reading.",
            "Self-reported LLM confidence is weakly calibrated; prefer sarcasm_votes and "
            "resolved_by as reliability signals.",
            "Temperature-0 decoding is near- but not bit-reproducible across Ollama "
            "versions/GPUs; provenance (digests + options + prompt_version) is the "
            "reproducibility contract.",
            "context.source == 'annotator_snapshot' is the block the annotator prompt saw; "
            "never rebuild conversational context from corpus-*.jsonl.",
            "human_gold rows are evaluation-only: never train on them.",
            "reliability.per_label gives the resolution of EACH label; "
            "reliability.resolved_by is the joint resolution of all four.",
        ],
    }
    card_path = out_dir / "dataset_card.json"
    card_path.write_text(json.dumps(card, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "rows": len(rows),
        "skipped_unresolved": skipped_unresolved,
        "jsonl": str(jsonl_path),
        "parquet": str(parquet_path) if parquet_path else None,
        "corpus": str(corpus_path),
        "card": str(card_path),
    }
