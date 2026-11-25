"""Final dataset export for the leische (model) repository.

Produces, under data/annotated/:
  dataset-<version>.jsonl    one row per resolved annotation target
  dataset-<version>.parquet  same rows (needs pandas+pyarrow; skipped otherwise)
  corpus-<version>.jsonl     pseudonymized corpus dump (temporal-context source)
  dataset_card.json          counts, distributions, agreement stats, provenance
  dataset_card-<version>.json  the same card, kept per version
  uyam-dataset-<version>.zip all of the above + SHA256SUMS.txt, one download

Every row is built from an explicit whitelist — the raw JSONL contains real
usernames in `author`, which must never reach an export artifact. Only
`author_hash` ships.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uyam.annotate.aggregate import compute_agreement, per_label_resolution
from uyam.annotate.config import AggregationConfig, AnnotationConfig
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.media import load_post_urls, media_context
from uyam.annotate.media import summarize as summarize_media

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


def majority_label(
    annotator_votes: list[bool], shipped: bool | None
) -> tuple[bool | None, float | None]:
    """U1: (majority of the annotator sarcasm votes, share voting sarcastic).

    Ties (and no votes) fall back to the shipped label, as leische specified.
    """
    if not annotator_votes:
        return shipped, None
    yes = sum(1 for v in annotator_votes if v)
    share = round(yes / len(annotator_votes), 4)
    no = len(annotator_votes) - yes
    if yes == no:
        return shipped, share
    return yes > no, share


def _human_labels(labels: dict[str, Any]) -> dict[str, Any]:
    """A human_reviews row (or a first_pass_json dict) in the export's label shape."""
    return {
        "sarcastic": None if labels.get("sarcastic") is None else bool(labels["sarcastic"]),
        "language": labels.get("language"),
        "literal_sentiment": labels.get("literal_sentiment"),
        "intended_sentiment": labels.get("intended_sentiment"),
        "cues": {name: bool(labels.get(col) or 0) for name, col in _CUE_COLS.items()},
        "sarcastic_span": labels.get("sarcastic_span"),
        "unsure": bool(labels.get("unsure") or 0),
    }


def _media_context(
    db: AnnotationDatabase,
    record: dict[str, Any],
    context: dict[str, Any],
    post_urls: dict[str, str] | None,
) -> dict[str, Any]:
    is_post = record.get("record_type") == "submission"
    post_fullname = str(
        record.get("reddit_fullname") if is_post else record.get("submission_fullname")
    )
    post = record if is_post else db.get_record(post_fullname)
    return media_context(
        is_post=is_post,
        target_text=record.get("text"),
        post_is_self=(post or {}).get("is_self"),
        post_url=(post_urls or {}).get(post_fullname),
        post_body=None if is_post else (post or {}).get("selftext"),
        parent_texts=[str(p.get("text") or "") for p in context.get("parent_chain") or []],
    )


def _gold_kappa_by_media(db: AnnotationDatabase, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Human-vs-shipped sarcasm kappa on the gold items, text-only vs media-dependent.

    A much lower kappa under image/video posts would mean the LLM labels there
    are guesses about content nobody saw (the blind first pass, as elsewhere).
    """
    from uyam.annotate.evaluation import cohen_kappa

    gold, _ = db.blind_gold_labels()
    out: dict[str, Any] = {}
    for name, keep in (("text_only", True), ("media_dependent", False)):
        pairs = [
            (int(gold[r["reddit_fullname"]]["sarcastic"]), int(bool(r["labels"]["sarcastic"])))
            for r in rows
            if r["reddit_fullname"] in gold
            and gold[r["reddit_fullname"]].get("sarcastic") is not None
            and r["labels"].get("sarcastic") is not None
            and bool((r.get("media_context") or {}).get("text_only")) is keep
        ]
        out[name] = {
            "n": len(pairs),
            "sarcastic_kappa": cohen_kappa([h for h, _ in pairs], [e for _, e in pairs])
            if len(pairs) >= 2
            else None,
        }
    return out


def _phatic_flag(db: AnnotationDatabase, fullname: str) -> bool:
    row = db.conn.execute(
        "SELECT phatic FROM candidates WHERE reddit_fullname = ?", (fullname,)
    ).fetchone()
    return bool(row and row["phatic"])


def _gold_queue_flag(db: AnnotationDatabase, fullname: str) -> bool:
    """In the gold queue, labeled or not (leische request 2026-11-24, §5e.4).

    leische holds every gold-queue post out of training, so a gold label added
    later is never on a post the model has seen. ``human_gold`` alone marks
    only the labeled ones.
    """
    row = db.conn.execute(
        "SELECT 1 FROM review_queue WHERE reddit_fullname = ? AND reason = 'gold'", (fullname,)
    ).fetchone()
    return row is not None


def build_export_row(
    db: AnnotationDatabase,
    agg: dict[str, Any],
    dataset_version: str,
    *,
    aggregation: AggregationConfig | None = None,
    annotator_keys: list[str] | None = None,
    post_urls: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """One dataset row. ``annotator_keys`` limits the shipped annotator votes to
    the configured lineup, and ``aggregation`` sets how per-label resolution is
    described. Both default to v1/v2 behaviour."""
    rules = aggregation or AggregationConfig()
    fullname = str(agg["reddit_fullname"])
    record = db.get_record(fullname)
    if record is None:
        return None

    annotations = db.annotations_for_item(fullname, str(agg["prompt_version"]))
    raw_annotators = [
        r
        for r in annotations
        if r["role"] == "annotator"
        and (annotator_keys is None or str(r["model_key"]) in annotator_keys)
    ]
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
    shipped = None if agg.get("sarcastic_final") is None else bool(agg["sarcastic_final"])
    majority, vote_share = majority_label(
        [bool(r["sarcastic"]) for r in raw_annotators if r.get("sarcastic") is not None],
        shipped,
    )
    first_pass: Any = None
    if human is not None and human.get("first_pass_json"):
        try:
            first_pass = _human_labels(json.loads(str(human["first_pass_json"])))
        except ValueError:
            first_pass = None

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
            "sarcastic": shipped,
            # U1: the annotators' own majority (ties -> shipped) and vote share.
            "sarcastic_majority": majority,
            "sarcasm_vote_share": vote_share,
            "language": agg.get("language_final"),
            "literal_sentiment": agg.get("literal_final"),
            "intended_sentiment": agg.get("intended_final"),
            "cues": {
                name: bool(agg.get(col) or 0) for name, col in _CUE_FINAL_COLS.items()
            },
        },
        "reliability": {
            "resolved_by": agg.get("resolved_by"),
            "per_label": per_label_resolution(
                raw_annotators,
                raw_adjudicators,
                human,
                sarcasm_rule=rules.sarcasm_rule,
                tie_break=rules.tie_break,
            ),
            "sarcasm_votes": agg.get("sarcasm_votes"),
            "n_annotators": agg.get("n_annotators"),
            "mean_confidence": agg.get("mean_confidence"),
            "needs_human": bool(agg.get("needs_human") or 0),
            "annotators": annotator_rows,
            "adjudicator": adjudicator_rows[0] if adjudicator_rows else None,
        },
        "human_gold": (
            {
                **_human_labels(human),
                "notes": human.get("notes"),
                # The blind first pass, same shape as the labels above; the
                # card's gold kappa uses it (null: rows saved before it was
                # recorded, whose labels above were saved blind).
                "first_pass": first_pass,
                "assist_seen": bool(human.get("assist_seen") or 0),
                # Model votes were on screen at the first save: not blind,
                # left out of the gold kappa.
                "votes_seen": bool(human.get("votes_seen") or 0),
                # The reviewer chose to see the Claude suggestion before this
                # first pass (a "second judgement"): Claude-informed, not blind.
                "assist_before_first_pass": bool(human.get("assist_before_first_pass") or 0),
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
                    # GUIDELINE L2-L4b on the same tokens (None if not computed).
                    "language_rule": lid["language_rule"],
                    "rule_version": lid["rule_version"],
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
        # U9: a phatic item (greeting / thanks / HBD only) that was already
        # labeled is kept with its label and flagged, never dropped.
        "flags": {
            "phatic": _phatic_flag(db, fullname),
            "gold_queue": _gold_queue_flag(db, fullname),
        },
        # What kind of post the item hangs on, and whether media appears in
        # it or its thread: nobody (annotators or model) saw images or video.
        "media_context": _media_context(db, record, context, post_urls),
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


# Windows reported next to the thesis rule (48 h): the history window is a
# leische/method decision, and with the 2026-06 -> 11 backfill it decides how
# much of the corpus can reach the model (UYAM_LATEST.md §2.1).
COVERAGE_WINDOWS_HOURS: tuple[float | None, ...] = (48.0, 7 * 24.0, 30 * 24.0, None)


def _window_label(hours: float | None) -> str:
    if hours is None:
        return "unbounded"
    return f"{hours / 24:g}d" if hours >= 24 and hours % 24 == 0 and hours != 48 else f"{hours:g}h"


def temporal_context_coverage(
    db: AnnotationDatabase,
    fullnames: list[str],
    *,
    window_hours: float = 48.0,
    max_posts: int = 5,
    extra_windows: tuple[float | None, ...] = COVERAGE_WINDOWS_HOURS,
) -> dict[str, Any]:
    """Thesis §3.4(2): up to `max_posts` same-author posts within `window_hours`
    BEFORE the target, drawn from the collected corpus only. Reports what share
    of the exported rows actually have that history — the temporal channel is
    only as thick as the collection window (handoff H8). The top-level keys
    are the thesis rule; `by_window` repeats the measurement for wider windows
    (None = no time limit), from the same author index."""
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

    targets: list[tuple[datetime | None, str | None]] = []
    for fullname in fullnames:
        record = db.get_record(fullname)
        if record is None:
            continue
        author = record.get("author_hash")
        targets.append((_parse_utc(record.get("created_utc")), str(author) if author else None))

    def measure(hours: float | None) -> dict[str, Any]:
        counts: list[int] = []
        for ts, author in targets:
            if ts is None or not author:
                counts.append(0)
                continue
            stamps = by_author.get(author, [])
            lo = 0 if hours is None else bisect.bisect_left(stamps, ts - timedelta(hours=hours))
            hi = bisect.bisect_left(stamps, ts)
            counts.append(min(max_posts, max(0, hi - lo)))
        n = len(counts)

        def share(k: int) -> float | None:
            return round(sum(1 for c in counts if c >= k) / n, 4) if n else None

        return {
            "n_rows": n,
            "share_with_at_least_1": share(1),
            "share_with_at_least_3": share(3),
            "share_with_full_5": share(max_posts),
            "mean_available": round(sum(counts) / n, 3) if n else None,
        }

    base = measure(window_hours)
    return {
        "rule": f"<= {max_posts} same-author posts within {window_hours:g} h before the target",
        **base,
        "by_window": {_window_label(h): measure(h) for h in extra_windows},
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


def _label_sets(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """U1: shipped (adjudicated) vs annotator-majority sarcasm labels."""
    pairs = [
        (r["labels"]["sarcastic"], r["labels"]["sarcastic_majority"])
        for r in rows
        if r["labels"]["sarcastic"] is not None and r["labels"]["sarcastic_majority"] is not None
    ]
    n = len(pairs)
    if not n:
        return {"n": 0}
    both = sum(1 for a, b in pairs if a and b)
    shipped_pos = sum(1 for a, _ in pairs if a)
    majority_pos = sum(1 for _, b in pairs if b)
    agree = sum(1 for a, b in pairs if a == b)
    p_o = agree / n
    p_e = (shipped_pos / n) * (majority_pos / n) + (1 - shipped_pos / n) * (1 - majority_pos / n)
    kappa = None if p_e == 1 else round((p_o - p_e) / (1 - p_e), 4)
    return {
        "n": n,
        "shipped_positives": shipped_pos,
        "majority_positives": majority_pos,
        "both_positive": both,
        "rows_that_differ": n - agree,
        "cohen_kappa_shipped_vs_majority": kappa,
        "note": "labels.sarcastic is the adjudicated (shipped) label; "
        "labels.sarcastic_majority is the annotators' own majority, ties -> shipped.",
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_bundles(out_dir: Path) -> list[Path]:
    """Every finished uyam-dataset-*.zip in out_dir, newest first."""
    bundles = [p for p in out_dir.glob("uyam-dataset-*.zip") if p.is_file()]
    return sorted(bundles, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)


def list_side_files(out_dir: Path) -> list[Path]:
    """Per-row side files shipped outside a bundle (media_context-v2.jsonl, ...).

    v2 was exported before rows carried media_context inline, so its flags
    live in a separate file that leische joins on reddit_fullname.
    """
    return sorted(p for p in out_dir.glob("media_context-*.jsonl") if p.is_file())


def latest_bundle(out_dir: Path) -> Path | None:
    """The most recently written uyam-dataset-*.zip in out_dir, or None.

    Lets the app offer the bundle to any browser (e.g. the leische machine over
    Tailscale), not only to the session that ran the export.
    """
    bundles = list_bundles(out_dir)
    return bundles[0] if bundles else None


def bundle_info(path: Path) -> dict[str, Any]:
    """Version, size, time and the card's row/positive counts of one bundle.

    The counts come from the versioned card inside the zip, so the picker shows
    what will actually be downloaded. A zip without a readable card still
    lists, with None counts.
    """
    stat = path.stat()
    version = path.stem.removeprefix("uyam-dataset-")
    info: dict[str, Any] = {
        "version": version,
        "size_mb": round(stat.st_size / 1e6, 1),
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "rows": None,
        "sarcastic": None,
    }
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            member = f"dataset_card-{version}.json"
            member = member if member in names else "dataset_card.json"
            card = json.loads(zf.read(member).decode("utf-8"))
    except (KeyError, OSError, ValueError, zipfile.BadZipFile):
        return info
    info["rows"] = (card.get("counts") or {}).get("exported_rows")
    info["sarcastic"] = (card.get("readiness") or {}).get("sarcastic_positives")
    return info


def write_bundle(out_dir: Path, dataset_version: str, members: Sequence[Path]) -> Path:
    """Zip every export artifact into one file for transfer to the leische device.

    The bundle is the unit of handoff: one download instead of four, with
    SHA256SUMS.txt so the receiving machine can verify the copy arrived intact.
    """
    zip_path = out_dir / f"uyam-dataset-{dataset_version}.zip"
    present = [m for m in members if m.exists()]
    sums = "".join(f"{_sha256(m)}  {m.name}\n" for m in present)
    tmp_path = zip_path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for member in present:
            zf.write(member, arcname=member.name)
        zf.writestr("SHA256SUMS.txt", sums)
    tmp_path.replace(zip_path)
    logger.info(
        "export_bundle_written path=%s members=%d bytes=%d",
        zip_path,
        len(present),
        zip_path.stat().st_size,
    )
    return zip_path


def run_export(
    cfg: AnnotationConfig,
    prompt_version: str,
    *,
    dataset_version: str = "v1",
    formats: tuple[str, ...] = ("jsonl", "parquet"),
    include_unresolved: bool = False,
    bundle: bool = True,
    corpus_scope: str = "labeled_authors",
) -> dict[str, Any]:
    if corpus_scope not in ("labeled_authors", "all"):
        raise ValueError(f"corpus_scope must be 'labeled_authors' or 'all', got {corpus_scope!r}")
    out_dir = cfg.export.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    with AnnotationDatabase(cfg.db_path) as db:
        aggregates = db.all_aggregates(prompt_version)
        # Aggregates outlive eligibility: an item annotated before a filter was
        # added (e.g. `phatic`) keeps its aggregate row, so the export applies
        # the candidate filters as they stand now.
        eligible = set(db.eligible_fullnames())
        rows: list[dict[str, Any]] = []
        skipped_unresolved = 0
        skipped_ineligible = 0
        post_urls = load_post_urls(cfg.data_dir)
        for agg in aggregates:
            if agg["reddit_fullname"] not in eligible:
                skipped_ineligible += 1
                continue
            if not include_unresolved and agg.get("resolved_by") is None:
                skipped_unresolved += 1
                continue
            row = build_export_row(
                db,
                agg,
                dataset_version,
                aggregation=cfg.aggregation,
                annotator_keys=cfg.annotator_keys,
                post_urls=post_urls,
            )
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
                        "flag_phatic": row["flags"]["phatic"],
                        "flag_gold_queue": row["flags"]["gold_queue"],
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

        # The corpus dump only feeds the temporal channel (same-author
        # history); conversational context ships in each row's snapshot. With
        # ~590k records, the default keeps the exported rows plus everything
        # by their authors (leische's own loader plan, 2026-11-17).
        corpus_path = out_dir / f"corpus-{dataset_version}.jsonl"
        exported = {str(r["reddit_fullname"]) for r in rows}
        authors = {str(r["author_hash"]) for r in rows if r.get("author_hash")}
        corpus_written = 0
        reader = db.conn.cursor()
        reader.execute("SELECT * FROM corpus_index")
        with corpus_path.open("w", encoding="utf-8") as fh:
            while batch := reader.fetchmany(5000):
                for record in batch:
                    rec = dict(record)
                    keep = (
                        corpus_scope == "all"
                        or rec["reddit_fullname"] in exported
                        or (rec.get("author_hash") and str(rec["author_hash"]) in authors)
                    )
                    if keep:
                        fh.write(json.dumps(_corpus_dump_row(rec), ensure_ascii=False) + "\n")
                        corpus_written += 1

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
        media_summary = {
            **summarize_media(rows),
            "gold_kappa": _gold_kappa_by_media(db, rows),
        }

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
            "skipped_ineligible": skipped_ineligible,
            "phatic_flagged_rows": sum(1 for r in rows if r["flags"]["phatic"]),
            "gold_queue_rows": sum(1 for r in rows if r["flags"]["gold_queue"]),
            "corpus_dump_rows": corpus_written,
        },
        "corpus_dump_scope": corpus_scope,
        "label_sets": _label_sets(rows),
        "media_context": media_summary,
        "label_distributions": _label_distributions(rows) if rows else {},
        "agreement": agreement,
        "readiness": _readiness(rows, agreement),
        "collection_window": collection_window,
        "temporal_context_coverage": temporal_coverage,
        "annotator_provenance": annotator_provenance,
        "aggregation": {
            "sarcasm_rule": cfg.aggregation.sarcasm_rule,
            "tie_break": cfg.aggregation.tie_break,
            "annotators": cfg.annotator_keys,
            "adjudicator": cfg.adjudicator.key if cfg.adjudicator else None,
        },
        "candidate_filters": {
            "min_chars": cfg.candidate_filters.min_chars,
            "min_tokens": cfg.candidate_filters.min_tokens,
            "exclude_authors": cfg.candidate_filters.exclude_authors,
            "include_link_posts": cfg.candidate_filters.include_link_posts,
            "exclude_phatic": cfg.candidate_filters.exclude_phatic,
            "phatic_max_tokens": cfg.candidate_filters.phatic_max_tokens,
            "phatic_max_leftover": cfg.candidate_filters.phatic_max_leftover,
        },
        "notes": [
            (
                "Labels are LLM-ensemble annotations (3 annotators, unanimity-or-escalate on "
                "sarcasm, majority on 3-class labels, blind large-model adjudication, human "
                "final call on low-confidence items)."
                if cfg.aggregation.sarcasm_rule == "unanimous"
                else f"Labels are LLM-ensemble annotations ({len(cfg.annotators)} "
                "equal-size annotators, majority vote on sarcasm and on 3-class labels, "
                f"1-1-1 ties broken by {cfg.aggregation.tie_break}, no adjudicator)."
            ),
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
    # dataset_card.json is always the latest export (readers expect that name);
    # dataset_card-<version>.json keeps each version's card side by side.
    card_text = json.dumps(card, indent=2, ensure_ascii=False)
    card_path = out_dir / "dataset_card.json"
    card_path.write_text(card_text, encoding="utf-8")
    versioned_card_path = out_dir / f"dataset_card-{dataset_version}.json"
    versioned_card_path.write_text(card_text, encoding="utf-8")

    zip_path: Path | None = None
    if bundle:
        zip_path = write_bundle(
            out_dir,
            dataset_version,
            [
                p
                for p in (jsonl_path, parquet_path, corpus_path, card_path, versioned_card_path)
                if p is not None
            ],
        )

    return {
        "rows": len(rows),
        "skipped_unresolved": skipped_unresolved,
        "skipped_ineligible": skipped_ineligible,
        "jsonl": str(jsonl_path),
        "parquet": str(parquet_path) if parquet_path else None,
        "corpus": str(corpus_path),
        "corpus_rows": corpus_written,
        "card": str(card_path),
        "card_versioned": str(versioned_card_path),
        "zip": str(zip_path) if zip_path else None,
    }
