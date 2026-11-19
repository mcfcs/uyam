"""Evaluation-only annotators: compare a new model without touching the dataset.

An evaluator (annotation.yaml `evaluators:`) is run exactly like a base
annotator (same system prompt, PROMPT_VERSION and prompt-hash guard, the
persisted context snapshot the base annotators saw, the same Ollama client,
options, retries and schema-constrained parsing) but its votes are written to
`eval_annotations`, never to `llm_annotations`. Every dataset query
(aggregation, target set, agreement stats, export, progress) reads
`llm_annotations` only, so an evaluator cannot change a single shipped label.
Its failures go to `llm_failures` with role 'evaluator', which no annotator or
adjudicator queue filters on.

`build_eval_report` then scores the base annotators, their majority vote, the
shipped final label and every evaluator against the human gold labels.

All eval_annotations SQL lives in this module (db.py owns only the schema).
"""

from __future__ import annotations

import logging
import math
import sqlite3
import warnings
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from uyam.annotate.config import AnnotationConfig, AnnotatorConfig
from uyam.annotate.db import AnnotationDatabase
from uyam.annotate.ollama_client import AnnotationCallError, AnnotationResult
from uyam.annotate.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    prompt_content_hash,
    render_user_prompt,
)
from uyam.annotate.runner import (
    AnnotatorClient,
    annotation_row,
    check_prompt_contract,
    make_client,
    make_progress,
)

logger = logging.getLogger(__name__)
console = Console()

EVALUATOR_ROLE = "evaluator"  # llm_failures.role for evaluator failures
GOLD_ITEMS = "gold"

_EVAL_COLS = (
    "reddit_fullname", "model_key", "prompt_version",
    "language", "literal_sentiment", "intended_sentiment", "sarcastic",
    "cue_polarity_inversion", "cue_rhetorical_intent",
    "cue_contextual_incongruity", "cue_hyperbole",
    "confidence", "rationale", "raw_json", "model_digest", "endpoint",
    "ollama_version", "options_json", "attempts", "duration_ms",
    "prompt_tokens", "completion_tokens",
)


# ---------------------------------------------------------------------------
# Storage helpers (eval_annotations)
# ---------------------------------------------------------------------------

def insert_eval_annotation(db: AnnotationDatabase, row: dict[str, Any]) -> bool:
    """Insert one evaluator vote. Returns False when (item, model, prompt) exists."""
    placeholders = ", ".join("?" for _ in _EVAL_COLS) + ", ?"
    try:
        db.conn.execute(
            f"INSERT INTO eval_annotations ({', '.join(_EVAL_COLS)}, created_at) "
            f"VALUES ({placeholders})",
            tuple(row.get(c) for c in _EVAL_COLS) + (datetime.now(UTC).isoformat(),),
        )
        db.conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def eval_done_items(db: AnnotationDatabase, model_key: str, prompt_version: str) -> set[str]:
    rows = db.conn.execute(
        "SELECT reddit_fullname FROM eval_annotations WHERE model_key = ? AND prompt_version = ?",
        (model_key, prompt_version),
    ).fetchall()
    return {str(r["reddit_fullname"]) for r in rows}


def eval_failed_items(db: AnnotationDatabase, model_key: str, prompt_version: str) -> set[str]:
    rows = db.conn.execute(
        """
        SELECT DISTINCT reddit_fullname FROM llm_failures
        WHERE model_key = ? AND prompt_version = ? AND role = ?
        """,
        (model_key, prompt_version, EVALUATOR_ROLE),
    ).fetchall()
    return {str(r["reddit_fullname"]) for r in rows}


def eval_votes(db: AnnotationDatabase, prompt_version: str) -> dict[str, dict[str, int]]:
    """{model_key: {reddit_fullname: sarcastic}} for every evaluator vote."""
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for r in db.conn.execute(
        """
        SELECT model_key, reddit_fullname, sarcastic FROM eval_annotations
        WHERE prompt_version = ? AND sarcastic IS NOT NULL
        """,
        (prompt_version,),
    ):
        out[str(r["model_key"])][str(r["reddit_fullname"])] = int(r["sarcastic"])
    return dict(out)


def gold_queue_items(db: AnnotationDatabase) -> list[str]:
    """Every item drawn into the gold sample, labeled by the human or not."""
    rows = db.conn.execute(
        "SELECT reddit_fullname FROM review_queue WHERE reason = 'gold' ORDER BY reddit_fullname"
    ).fetchall()
    return [str(r["reddit_fullname"]) for r in rows]


def read_fullnames_file(path: Path) -> list[str]:
    """One reddit_fullname per line; blank lines and '#' comments ignored, order kept."""
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            names.append(name)
    return list(dict.fromkeys(names))


def select_eval_items(db: AnnotationDatabase, items: str | Path) -> list[str]:
    """`gold` -> the gold review queue; anything else -> a txt file of fullnames."""
    if str(items) == GOLD_ITEMS:
        return gold_queue_items(db)
    path = Path(items)
    if not path.is_file():
        raise FileNotFoundError(f"--items must be 'gold' or a file of reddit_fullnames: {path}")
    return read_fullnames_file(path)


# ---------------------------------------------------------------------------
# Running an evaluator
# ---------------------------------------------------------------------------

def _eval_row(
    fullname: str,
    evaluator: AnnotatorConfig,
    result: AnnotationResult,
    *,
    endpoint_url: str,
    model_digest: str | None,
    ollama_version: str | None,
) -> dict[str, Any]:
    row = annotation_row(
        fullname,
        evaluator,
        EVALUATOR_ROLE,
        result,
        endpoint_url=endpoint_url,
        model_digest=model_digest,
        ollama_version=ollama_version,
    )
    row.pop("role")  # eval_annotations has no role column
    return row


def run_evaluator(
    cfg: AnnotationConfig,
    evaluator_key: str,
    *,
    items: str | Path = GOLD_ITEMS,
    limit: int | None = None,
    retry_failed: bool = False,
    client_factory: Callable[[AnnotationConfig, AnnotatorConfig], AnnotatorClient] | None = None,
) -> dict[str, int]:
    """Label the selected items with one evaluator into eval_annotations.

    Resumable: items already in eval_annotations for (model_key, prompt_version)
    and previously failed items (unless retry_failed) are skipped. Items with no
    context snapshot for this prompt version are skipped and counted, never
    rebuilt: an evaluator must see exactly what the base annotators saw.
    """
    check_prompt_contract(cfg)
    evaluator = cfg.evaluator(evaluator_key)
    stats = {
        "selected": 0,
        "done": 0,
        "failed": 0,
        "already_done": 0,
        "skipped_failed": 0,
        "no_context": 0,
        "no_record": 0,
    }

    with AnnotationDatabase(cfg.db_path) as db:
        db.check_prompt_version(PROMPT_VERSION, prompt_content_hash())

        if retry_failed:
            cleared = db.clear_failures(evaluator.key, PROMPT_VERSION, EVALUATOR_ROLE)
            if cleared:
                console.print(f"Cleared {cleared} recorded failures for {evaluator.key}")

        selected = select_eval_items(db, items)
        stats["selected"] = len(selected)
        done = eval_done_items(db, evaluator.key, PROMPT_VERSION)
        failed = eval_failed_items(db, evaluator.key, PROMPT_VERSION)

        queue: list[tuple[str, dict[str, Any], str]] = []
        for fullname in selected:
            if fullname in done:
                stats["already_done"] += 1
                continue
            if fullname in failed:
                stats["skipped_failed"] += 1
                continue
            record = db.get_record(fullname)
            if record is None:
                stats["no_record"] += 1
                continue
            context = db.get_context(fullname)
            if context is None or context["prompt_version"] != PROMPT_VERSION:
                stats["no_context"] += 1
                continue
            queue.append((fullname, record, str(context["context_text"])))
        if limit is not None:
            queue = queue[:limit]

        skip_note = (
            f"{stats['already_done']} already done, {stats['skipped_failed']} failed before"
            + (" (re-run with --retry-failed)" if stats["skipped_failed"] else "")
            + f", {stats['no_context']} without a context snapshot for {PROMPT_VERSION}"
            + f", {stats['no_record']} not in the corpus index"
        )
        if not queue:
            console.print(
                f"[green]Nothing pending[/green] for evaluator {evaluator.key} "
                f"({stats['selected']} selected: {skip_note})"
            )
            return stats

        client = (client_factory or make_client)(cfg, evaluator)
        ollama_version = client.server_version()
        model_digest = client.model_digest(evaluator.model)
        endpoint_url = cfg.endpoint_url(evaluator.endpoint)
        console.print(
            f"Evaluating with [bold]{evaluator.model}[/bold] ({evaluator.key}, evaluation-only) "
            f"on {evaluator.endpoint} — {len(queue)} to label of {stats['selected']} selected "
            f"({skip_note})"
        )

        progress = make_progress()
        try:
            with progress:
                task = progress.add_task(evaluator.key, total=len(queue))
                for fullname, record, context_text in queue:
                    prompt = render_user_prompt(
                        context_text, str(record["record_type"]), str(record["text"])
                    )
                    try:
                        result = client.annotate(evaluator, SYSTEM_PROMPT, prompt, cfg.options)
                    except AnnotationCallError as exc:
                        db.record_failure(
                            fullname, evaluator.key, PROMPT_VERSION, EVALUATOR_ROLE, str(exc)
                        )
                        stats["failed"] += 1
                        progress.advance(task)
                        continue
                    inserted = insert_eval_annotation(
                        db,
                        _eval_row(
                            fullname,
                            evaluator,
                            result,
                            endpoint_url=endpoint_url,
                            model_digest=model_digest,
                            ollama_version=ollama_version,
                        ),
                    )
                    stats["done" if inserted else "already_done"] += 1
                    progress.advance(task)
        except KeyboardInterrupt:
            console.print(
                f"\n[yellow]Interrupted.[/yellow] {stats['done']} stored this session — "
                f"re-run the same command to resume."
            )
            return stats

        console.print(
            f"[green]Done[/green] evaluator {evaluator.key}: {stats['done']} stored, "
            f"{stats['failed']} failed ({skip_note})"
        )
        logger.info("evaluator_run_done", extra={"model_key": evaluator.key, **stats})
        return stats


# ---------------------------------------------------------------------------
# Comparison report
# ---------------------------------------------------------------------------

def cohen_kappa(a: list[int], b: list[int]) -> float | None:
    """Cohen's kappa for binary labels; None when undefined (n=0, or both one class)."""
    if not a:
        return None
    try:
        from sklearn.metrics import cohen_kappa_score  # type: ignore[import-untyped]

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # 0/0 when both raters use one class
            value = float(cohen_kappa_score(a, b, labels=[0, 1]))
    except Exception as exc:  # noqa: BLE001 - stats are best-effort reporting
        logger.warning("cohen_kappa_failed", extra={"error": str(exc)})
        return None
    return None if math.isnan(value) else round(value, 4)


def sarcasm_metrics(human: list[int], predicted: list[int]) -> dict[str, Any]:
    """n, accuracy, F1 of the sarcastic class, and Cohen's kappa vs the human label."""
    n = len(human)
    if n == 0:
        return {"n": 0, "accuracy": None, "f1_sarcastic": None, "kappa": None}
    tp = sum(1 for h, p in zip(human, predicted, strict=True) if h == 1 and p == 1)
    fp = sum(1 for h, p in zip(human, predicted, strict=True) if h == 0 and p == 1)
    fn = sum(1 for h, p in zip(human, predicted, strict=True) if h == 1 and p == 0)
    correct = sum(1 for h, p in zip(human, predicted, strict=True) if h == p)
    f1_denominator = 2 * tp + fp + fn  # 0 only when nobody ever says sarcastic
    return {
        "n": n,
        "accuracy": round(correct / n, 4),
        "f1_sarcastic": round(2 * tp / f1_denominator, 4) if f1_denominator else None,
        "kappa": cohen_kappa(human, predicted),
    }


def _majority(values: list[int]) -> int | None:
    """Strict majority of binary votes; None on a tie."""
    yes = sum(values)
    no = len(values) - yes
    if yes == no:
        return None
    return int(yes > no)


def _score(source: str, kind: str, gold: dict[str, int], votes: dict[str, int]) -> dict[str, Any]:
    shared = sorted(set(gold) & set(votes))
    metrics = sarcasm_metrics([gold[f] for f in shared], [votes[f] for f in shared])
    return {"source": source, "kind": kind, **metrics}


def build_eval_report(cfg: AnnotationConfig, prompt_version: str) -> dict[str, Any]:
    """Sarcasm scores vs the human gold labels, plus evaluator-vs-shipped agreement.

    Read-only. Gold = the blind first-pass sarcasm labels of the gold items
    (db.blind_gold_labels), the same labels the card's gold kappa uses.
    """
    with AnnotationDatabase(cfg.db_path) as db:
        blind, _ = db.blind_gold_labels()
        gold = {
            fullname: int(labels["sarcastic"])
            for fullname, labels in blind.items()
            if labels.get("sarcastic") is not None
        }
        base: dict[str, dict[str, int]] = defaultdict(dict)
        for r in db.conn.execute(
            """
            SELECT reddit_fullname, model_key, sarcastic FROM llm_annotations
            WHERE prompt_version = ? AND role = 'annotator' AND sarcastic IS NOT NULL
            """,
            (prompt_version,),
        ):
            base[str(r["model_key"])][str(r["reddit_fullname"])] = int(r["sarcastic"])
        shipped = {
            str(r["reddit_fullname"]): int(r["sarcastic_final"])
            for r in db.conn.execute(
                "SELECT reddit_fullname, sarcastic_final FROM aggregates "
                "WHERE prompt_version = ? AND sarcastic_final IS NOT NULL",
                (prompt_version,),
            )
        }
        evaluators = eval_votes(db, prompt_version)

    by_item: dict[str, list[int]] = defaultdict(list)
    for votes in base.values():
        for fullname, value in votes.items():
            by_item[fullname].append(value)
    majority = {
        fullname: m for fullname, values in by_item.items() if (m := _majority(values)) is not None
    }

    evaluator_keys = [e.key for e in cfg.evaluators]
    evaluator_keys += sorted(k for k in evaluators if k not in evaluator_keys)

    rows = [_score(a.key, "annotator", gold, base.get(a.key, {})) for a in cfg.annotators]
    rows.append(_score("annotator majority", "majority", gold, majority))
    rows.append(_score("shipped final", "final", gold, shipped))
    rows += [_score(k, "evaluator", gold, evaluators.get(k, {})) for k in evaluator_keys]

    vs_final = []
    for key in evaluator_keys:
        votes = evaluators.get(key, {})
        shared = sorted(set(votes) & set(shipped))
        agree = sum(1 for f in shared if votes[f] == shipped[f])
        vs_final.append(
            {
                "evaluator": key,
                "n_labeled": len(votes),
                "n": len(shared),
                "agreement": round(agree / len(shared), 4) if shared else None,
                "kappa": cohen_kappa([shipped[f] for f in shared], [votes[f] for f in shared]),
            }
        )

    return {
        "prompt_version": prompt_version,
        "n_gold": len(gold),
        "n_gold_sarcastic": sum(gold.values()),
        "vs_human": rows,
        "evaluators_vs_final": vs_final,
    }


def fmt_metric(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def render_eval_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Evaluation-only annotators — sarcasm vs human gold ({report['prompt_version']})",
        "",
        f"Human gold items: {report['n_gold']} ({report['n_gold_sarcastic']} sarcastic). "
        "Evaluator votes live in `eval_annotations` and never reach the dataset.",
        "",
        "| Source | Kind | n | Accuracy | F1 (sarcastic) | Cohen's κ vs human |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for r in report["vs_human"]:
        lines.append(
            f"| {r['source']} | {r['kind']} | {r['n']} | {fmt_metric(r['accuracy'])} | "
            f"{fmt_metric(r['f1_sarcastic'])} | {fmt_metric(r['kappa'])} |"
        )
    lines += [
        "",
        "## Evaluators vs the shipped final label (all items each evaluator labeled)",
        "",
        "| Evaluator | Labeled | n with a final label | Agreement | Cohen's κ |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in report["evaluators_vs_final"]:
        lines.append(
            f"| {r['evaluator']} | {r['n_labeled']} | {r['n']} | "
            f"{fmt_metric(r['agreement'])} | {fmt_metric(r['kappa'])} |"
        )
    return "\n".join(lines) + "\n"
