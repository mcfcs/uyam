"""Live numbers for the Progress notes tab (consultation and defense notes).

Everything here is read-only: databases are opened with ``mode=ro`` and the
dataset cards are only read. The tab's prose lives in progress_notes_ui.py;
this module holds the parts that are computed, so they can be tested offline.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SARC_V2 = "sarc-v2"
GUIDE = "guide-v1.1"


@contextmanager
def _ro(db: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        yield conn
    finally:
        conn.close()


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


@dataclass(frozen=True)
class GoldItem:
    sarcastic: int
    blind: bool  # no Claude suggestion was showing at the first save


def load_gold(main_db: Path) -> tuple[dict[str, GoldItem], dict[str, int]]:
    """Gio's gold labels as every human-vs-model score uses them.

    The first pass (first_pass_json) when stored, else the saved label; items
    whose model votes were shown before the first save (votes_seen) are left
    out. ``blind`` is False when the Claude suggestion was showing first
    (Gio counts those as his labels; the blind view is a sensitivity check).
    """
    gold: dict[str, GoldItem] = {}
    stats = {"queue": 0, "labeled": 0, "votes_seen": 0}
    if not main_db.exists():
        return gold, stats
    with _ro(main_db) as conn:
        conn.row_factory = sqlite3.Row
        if _has_table(conn, "review_queue"):
            stats["queue"] = int(
                conn.execute(
                    "SELECT COUNT(*) FROM review_queue WHERE reason = 'gold'"
                ).fetchone()[0]
            )
        if not _has_table(conn, "human_reviews"):
            return gold, stats
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(human_reviews)")}
        for row in conn.execute("SELECT * FROM human_reviews WHERE is_gold = 1"):
            stats["labeled"] += 1
            if "votes_seen" in cols and row["votes_seen"]:
                stats["votes_seen"] += 1
                continue
            raw = row["first_pass_json"] if "first_pass_json" in cols else None
            first = json.loads(str(raw)) if raw else {"sarcastic": row["sarcastic"]}
            if first.get("sarcastic") is None:
                continue
            assisted = bool(row["assist_before_first_pass"]) if (
                "assist_before_first_pass" in cols
            ) else False
            gold[str(row["reddit_fullname"])] = GoldItem(int(first["sarcastic"]), not assisted)
    return gold, stats


def cohen_kappa(a: list[int], b: list[int]) -> float | None:
    """Binary Cohen's kappa; None when undefined (fewer than 2 items or no variance)."""
    n = len(a)
    if n < 2 or n != len(b):
        return None
    po = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1:
        return None
    return (po - pe) / (1 - pe)


def f1(human: list[int], model: list[int]) -> float | None:
    tp = sum(1 for h, m in zip(human, model, strict=True) if h and m)
    fp = sum(1 for h, m in zip(human, model, strict=True) if not h and m)
    fn = sum(1 for h, m in zip(human, model, strict=True) if h and not m)
    if tp + fp + fn == 0:
        return None
    return 2 * tp / (2 * tp + fp + fn)


@dataclass(frozen=True)
class LabelSource:
    name: str
    group: str
    db: Path
    sql: str
    params: tuple[Any, ...]


def default_sources(db_dir: Path) -> list[LabelSource]:
    """Every label set and annotator worth scoring against the gold."""
    main = db_dir / "annotation.sqlite3"
    v3 = db_dir / "annotation-v3.sqlite3"
    v4 = db_dir / "annotation-v4.sqlite3"
    v5 = db_dir / "annotation-v5.sqlite3"
    agg = (
        "SELECT reddit_fullname, sarcastic_final FROM aggregates "
        "WHERE prompt_version = ? AND sarcastic_final IS NOT NULL"
    )
    vote = (
        "SELECT reddit_fullname, sarcastic FROM llm_annotations "
        "WHERE prompt_version = ? AND role = 'annotator' AND model_key = ?"
    )
    sets = "Label sets (what the model trains on)"
    old = "Single annotators, old prompt (sarc-v2)"
    guide = "Single annotators, guideline prompt (guide-v1.1)"
    return [
        LabelSource("v1/v2 label (unanimous + 32B adjudicator)", sets, main, agg, (SARC_V2,)),
        LabelSource("v3 label (equal-size, majority)", sets, v3, agg, (SARC_V2,)),
        LabelSource("v4 label (guideline, majority)", sets, v4, agg, (GUIDE,)),
        LabelSource("v5 label (guideline, majority)", sets, v5, agg, (GUIDE,)),
        LabelSource("gemma3:27b (v1/v2 only)", old, main, vote, (SARC_V2, "gemma3")),
        LabelSource("qwen3:8b", old, main, vote, (SARC_V2, "qwen3")),
        LabelSource("SEA-LION v3 9B", old, main, vote, (SARC_V2, "sealion")),
        LabelSource("Llama-SEA-LION v3 8B (v3 on)", old, v3, vote, (SARC_V2, "llamasealion")),
        LabelSource("qwen3:8b", guide, v4, vote, (GUIDE, "qwen3")),
        LabelSource("SEA-LION v3 9B", guide, v4, vote, (GUIDE, "sealion")),
        LabelSource("Llama-SEA-LION v3 8B", guide, v4, vote, (GUIDE, "llamasealion")),
    ]


def _labels(source: LabelSource) -> dict[str, int] | None:
    if not source.db.exists():
        return None
    with _ro(source.db) as conn:
        table = "aggregates" if "FROM aggregates" in source.sql else "llm_annotations"
        if not _has_table(conn, table):
            return None
        rows = conn.execute(source.sql, source.params).fetchall()
    return {str(r[0]): int(r[1]) for r in rows if r[1] is not None} or None


def gold_agreement(gold: dict[str, GoldItem], sources: list[LabelSource]) -> list[dict[str, Any]]:
    """Each source's agreement with the gold: all items, and blind first passes only."""
    out: list[dict[str, Any]] = []
    for source in sources:
        labels = _labels(source)
        if not labels:
            continue
        shared = [f for f in gold if f in labels]
        if not shared:
            continue
        human = [gold[f].sarcastic for f in shared]
        model = [labels[f] for f in shared]
        blind = [f for f in shared if gold[f].blind]
        kb = cohen_kappa([gold[f].sarcastic for f in blind], [labels[f] for f in blind])
        ka = cohen_kappa(human, model)
        f1_all = f1(human, model)
        out.append(
            {
                "group": source.group,
                "source": source.name,
                "gold items": len(shared),
                "human +": sum(human),
                "model +": sum(model),
                "F1 vs gold": None if f1_all is None else round(f1_all, 3),
                "κ vs gold (all)": None if ka is None else round(ka, 3),
                "blind items": len(blind),
                "κ vs gold (blind)": None if kb is None else round(kb, 3),
            }
        )
    return out


def run_progress(db: Path, prompt_version: str, keys: list[str], target: int) -> dict[str, Any]:
    """Per-annotator done counts toward a target (read-only); empty if no DB yet."""
    if not db.exists():
        return {}
    with _ro(db) as conn:
        if not _has_table(conn, "llm_annotations"):
            return {}
        done = {
            str(r[0]): int(r[1])
            for r in conn.execute(
                "SELECT model_key, COUNT(*) FROM llm_annotations "
                "WHERE prompt_version = ? AND role = 'annotator' GROUP BY model_key",
                (prompt_version,),
            )
        }
        aggregated = 0
        if _has_table(conn, "aggregates"):
            aggregated = int(
                conn.execute(
                    "SELECT COUNT(*) FROM aggregates WHERE prompt_version = ?", (prompt_version,)
                ).fetchone()[0]
            )
    models = {k: min(done.get(k, 0), target) for k in keys}
    total = sum(models.values())
    return {
        "models": models,
        "target": target,
        "share": total / (target * len(keys)) if keys and target else 0.0,
        "aggregated": aggregated,
    }


def card_facts(out_dir: Path) -> dict[str, dict[str, Any]]:
    """Coverage, language, media and agreement figures per exported version."""
    facts: dict[str, dict[str, Any]] = {}
    for path in sorted(out_dir.glob("dataset_card-v*.json")):
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        version = str(card.get("dataset_version") or path.stem.removeprefix("dataset_card-"))
        cov = (card.get("temporal_context_coverage") or {}).get("by_window") or {}
        agreement = card.get("agreement") or {}
        labels = agreement.get("labels") or {}
        media = card.get("media_context") or {}
        counts = card.get("counts") or {}
        facts[version] = {
            "rows": counts.get("exported_rows"),
            "sarcastic": (card.get("readiness") or {}).get("sarcastic_positives"),
            "history 48 h": (cov.get("48h") or {}).get("share_with_at_least_1"),
            "history 7 d": (cov.get("7d") or {}).get("share_with_at_least_1"),
            "history 30 d": (cov.get("30d") or {}).get("share_with_at_least_1"),
            "corpus dump": counts.get("corpus_dump_rows"),
            "Fleiss κ (sarcasm)": (labels.get("sarcastic") or {}).get("fleiss_kappa"),
            "text-only rows": media.get("text_only_rows"),
            "text-only sarcastic": media.get("text_only_sarcastic"),
            "language rule κ vs gold": (
                agreement.get("language_rule_vs_human_gold") or {}
            ).get("cohen_kappa"),
            "exclusions": counts.get("candidates") or {},
        }
    return facts
