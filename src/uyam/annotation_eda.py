"""Exploratory analysis of the LLM annotation (read-only), for the Annotation EDA page.

How each model voted, how confident it said it was, how the three votes
combine, and how all of that lines up with Gio's gold labels. Databases are
opened read-only; nothing here writes. The page (annotation_eda_ui.py) only
renders these frames.
"""

from __future__ import annotations

import json
import sqlite3
from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from uyam.progress_notes import GoldItem, cohen_kappa, f1

MODELS = ["qwen3", "sealion", "llamasealion"]
MODEL_NAMES = {
    "qwen3": "qwen3 8B",
    "sealion": "Gemma-SEA-LION 9B",
    "llamasealion": "Llama-SEA-LION 8B",
}


def _ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)


def load_votes(db: Path, prompt_version: str, keys: list[str] = MODELS) -> pd.DataFrame:
    """One row per (post, model) vote: sarcastic 0/1, self-reported confidence."""
    cols = ["fullname", "model", "sarcastic", "confidence"]
    if not db.exists():
        return pd.DataFrame(columns=cols)
    conn = _ro(db)
    try:
        marks = ",".join("?" for _ in keys)
        frame = pd.read_sql_query(
            "SELECT reddit_fullname AS fullname, model_key AS model, sarcastic, confidence "
            "FROM llm_annotations WHERE prompt_version = ? AND role = 'annotator' "
            f"AND model_key IN ({marks}) AND sarcastic IS NOT NULL",
            conn,
            params=(prompt_version, *keys),
        )
    finally:
        conn.close()
    return frame[cols]


def vote_wide(votes: pd.DataFrame, keys: list[str] = MODELS) -> pd.DataFrame:
    """One row per post that has every model's vote: each model's vote and the yes count."""
    if votes.empty:
        return pd.DataFrame(columns=[*keys, "n_yes"])
    wide = votes.pivot_table(index="fullname", columns="model", values="sarcastic", aggfunc="first")
    wide = wide.reindex(columns=keys).dropna().astype(int)
    wide["n_yes"] = wide[keys].sum(axis=1)
    return wide


def model_summary(votes: pd.DataFrame) -> pd.DataFrame:
    """Per model: how often it says sarcastic and how confident it claims to be."""
    rows = []
    for model, part in votes.groupby("model"):
        yes = part[part["sarcastic"] == 1]["confidence"]
        no = part[part["sarcastic"] == 0]["confidence"]
        rows.append(
            {
                "model": MODEL_NAMES.get(str(model), str(model)),
                "votes": len(part),
                "sarcastic rate": round(float(part["sarcastic"].mean()), 3),
                "mean confidence": round(float(part["confidence"].mean()), 3),
                "confidence when 'sarcastic'": round(float(yes.mean()), 3) if len(yes) else None,
                "confidence when 'not'": round(float(no.mean()), 3) if len(no) else None,
            }
        )
    return pd.DataFrame(rows)


def pairwise_kappas(wide: pd.DataFrame, keys: list[str] = MODELS) -> pd.DataFrame:
    """Cohen's kappa between every pair of annotators, on posts all three labeled."""
    rows = []
    for a, b in combinations(keys, 2):
        k = cohen_kappa(list(wide[a]), list(wide[b])) if len(wide) else None
        agree = float((wide[a] == wide[b]).mean()) if len(wide) else None
        rows.append(
            {
                "pair": f"{MODEL_NAMES[a]} × {MODEL_NAMES[b]}",
                "Cohen's κ": None if k is None else round(k, 3),
                "raw agreement": None if agree is None else round(agree, 3),
            }
        )
    return pd.DataFrame(rows)


def vote_count_vs_gold(wide: pd.DataFrame, gold: dict[str, GoldItem]) -> pd.DataFrame:
    """For 0-3 'sarcastic' votes: share of the big set, and how often Gio agrees it is sarcastic."""
    rows = []
    total = len(wide)
    for k in range(4):
        posts = wide.index[wide["n_yes"] == k]
        in_gold = [f for f in posts if f in gold]
        human = sum(gold[f].sarcastic for f in in_gold)
        rows.append(
            {
                "'sarcastic' votes": f"{k} of 3",
                "posts (all)": len(posts),
                "share of all": round(len(posts) / total, 3) if total else None,
                "gold posts": len(in_gold),
                "Gio says sarcastic": human,
                "P(sarcastic per Gio)": round(human / len(in_gold), 3) if in_gold else None,
            }
        )
    return pd.DataFrame(rows)


def estimate_human_rate(by_votes: pd.DataFrame) -> dict[str, Any]:
    """Project Gio's rate per vote count onto the whole set (post-stratified estimate).

    The gold sample was not uniform (sarcastic items were weighted ×2), so its
    raw sarcasm rate is not the set's rate. Weighting Gio's per-stratum rate by
    each vote count's share of the full set corrects for that, to the extent
    the vote count captures how the gold was drawn. An estimate, not a count.
    """
    est = 0.0
    est_in_majority = 0.0
    majority = 0
    total = int(by_votes["posts (all)"].sum())
    for _, row in by_votes.iterrows():
        p = row["P(sarcastic per Gio)"]
        if p is None or pd.isna(p):
            continue
        n = int(row["posts (all)"])
        est += p * n
        if row["'sarcastic' votes"] in ("2 of 3", "3 of 3"):
            est_in_majority += p * n
            majority += n
    return {
        "posts": total,
        "estimated human-sarcastic posts": round(est),
        "estimated human rate": round(est / total, 3) if total else None,
        "majority-label positives": majority,
        "estimated precision of the majority label": round(est_in_majority / majority, 3)
        if majority
        else None,
        "estimated recall of the majority label": round(est_in_majority / est, 3) if est else None,
    }


def confidence_vs_gold(votes: pd.DataFrame, gold: dict[str, GoldItem]) -> pd.DataFrame:
    """Per model on gold posts: is self-reported confidence higher when the vote matches Gio?"""
    part = votes[votes["fullname"].isin(gold)].copy()
    if part.empty:
        return pd.DataFrame()
    part["gio"] = part["fullname"].map(lambda f: gold[f].sarcastic)
    part["matches Gio"] = part["sarcastic"] == part["gio"]
    rows = []
    for model, m in part.groupby("model"):
        yes = m[m["sarcastic"] == 1]
        hi = yes[yes["confidence"] >= 0.9]
        lo = yes[yes["confidence"] < 0.9]
        right = m[m["matches Gio"]]["confidence"]
        wrong = m[~m["matches Gio"]]["confidence"]
        rows.append(
            {
                "model": MODEL_NAMES.get(str(model), str(model)),
                "confidence when right": round(float(right.mean()), 3) if len(right) else None,
                "confidence when wrong": round(float(wrong.mean()), 3) if len(wrong) else None,
                "'sarcastic' votes ≥ 0.9 conf": len(hi),
                "…of which Gio agrees": round(float(hi["gio"].mean()), 2) if len(hi) else None,
                "'sarcastic' votes < 0.9 conf": len(lo),
                "…of which Gio agrees ": round(float(lo["gio"].mean()), 2) if len(lo) else None,
            }
        )
    return pd.DataFrame(rows)


def confidence_histogram(votes: pd.DataFrame) -> pd.DataFrame:
    """Share of each model's votes per confidence band (rows: band, columns: model)."""
    if votes.empty:
        return pd.DataFrame()
    bands = pd.cut(
        votes["confidence"].astype(float),
        [0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0],
        labels=["≤0.5", "0.5–0.6", "0.6–0.7", "0.7–0.8", "0.8–0.9", "0.9–0.95", "0.95–1"],
        include_lowest=True,
    )
    names = votes["model"].map(lambda m: MODEL_NAMES.get(m, m))
    table = pd.crosstab(bands, names, normalize="columns")
    return table.round(3)


def load_post_meta(dataset_jsonl: Path) -> dict[str, dict[str, Any]]:
    """reddit_fullname -> post_type / text_only / language_rule, parsed cheaply per line."""
    meta: dict[str, dict[str, Any]] = {}
    if not dataset_jsonl.exists():
        return meta
    with dataset_jsonl.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            media = row.get("media_context") or {}
            lid = (row.get("aux") or {}).get("lid") or {}
            meta[str(row["reddit_fullname"])] = {
                "post_type": media.get("post_type"),
                "text_only": media.get("text_only"),
                "language": lid.get("language_rule") or lid.get("language"),
            }
    return meta


def compare_gold_to_all(
    meta: dict[str, dict[str, Any]],
    wide: pd.DataFrame,
    gold: dict[str, GoldItem],
    field: str,
) -> pd.DataFrame:
    """Share of posts and sarcasm rates per category: whole set vs gold (LLM label and Gio)."""
    rows = []
    majority = wide["n_yes"] >= 2 if len(wide) else pd.Series(dtype=bool)
    values = sorted({str(m.get(field)) for m in meta.values() if m.get(field) is not None})
    all_ids = [f for f in wide.index if f in meta]
    gold_ids = [f for f in gold if f in meta and f in wide.index]
    for value in values:
        a = [f for f in all_ids if str(meta[f].get(field)) == value]
        g = [f for f in gold_ids if str(meta[f].get(field)) == value]
        rows.append(
            {
                field: value,
                "share of all posts": round(len(a) / len(all_ids), 3) if all_ids else None,
                "share of gold": round(len(g) / len(gold_ids), 3) if gold_ids else None,
                "LLM sarcastic rate (all)": round(float(majority[a].mean()), 3) if a else None,
                "LLM sarcastic rate (gold)": round(float(majority[g].mean()), 3) if g else None,
                "Gio's sarcastic rate (gold)": round(
                    sum(gold[f].sarcastic for f in g) / len(g), 3
                )
                if g
                else None,
                "gold posts": len(g),
            }
        )
    return pd.DataFrame(rows)


def label_set_metrics(labels: dict[str, int], gold: dict[str, GoldItem]) -> dict[str, Any]:
    ids = [f for f in gold if f in labels]
    human = [gold[f].sarcastic for f in ids]
    model = [labels[f] for f in ids]
    tp = sum(1 for h, m in zip(human, model, strict=True) if h and m)
    k = cohen_kappa(human, model)
    score = f1(human, model)
    return {
        "gold κ": None if k is None else round(k, 3),
        "gold F1": None if score is None else round(score, 3),
        "gold precision": round(tp / sum(model), 3) if sum(model) else None,
        "gold recall": round(tp / sum(human), 3) if sum(human) else None,
    }


def _rate(labels: dict[str, int], part: list[str]) -> str | None:
    return f"{100 * sum(labels[f] for f in part) / len(part):.1f}%" if part else None


def version_table(
    versions: dict[str, tuple[Path, str]],
    gold: dict[str, GoldItem],
    meta: dict[str, dict[str, Any]],
    cards: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    """One row per dataset version: labels, code-switching profile, context, agreement."""
    rows = []
    for version, (db, prompt_version) in versions.items():
        if not db.exists():
            continue
        conn = _ro(db)
        try:
            labels = {
                str(f): int(s)
                for f, s in conn.execute(
                    "SELECT reddit_fullname, sarcastic_final FROM aggregates "
                    "WHERE prompt_version = ? AND sarcastic_final IS NOT NULL",
                    (prompt_version,),
                )
            }
        finally:
            conn.close()
        if not labels:
            continue
        ids = list(labels)
        pos = sum(labels.values())
        by_lang: dict[str, str] = {}
        for lang in ("english", "tagalog", "taglish"):
            part = [f for f in ids if (meta.get(f) or {}).get("language") == lang]
            if part:
                by_lang[lang] = f"{100 * sum(labels[f] for f in part) / len(part):.1f}%"
        text_only = [f for f in ids if (meta.get(f) or {}).get("text_only") is True]
        media = [f for f in ids if (meta.get(f) or {}).get("text_only") is False]

        card = cards.get(version) or {}
        rows.append(
            {
                "version": version,
                "labeled posts": len(ids),
                "sarcastic": f"{pos:,} ({100 * pos / len(ids):.1f}%)",
                "Fleiss κ (LLMs)": card.get("Fleiss κ (sarcasm)"),
                **label_set_metrics(labels, gold),
                "sarcastic: English": by_lang.get("english"),
                "sarcastic: Tagalog": by_lang.get("tagalog"),
                "sarcastic: Taglish": by_lang.get("taglish"),
                "sarcastic: text-only posts": _rate(labels, text_only),
                "sarcastic: under media": _rate(labels, media),
                "author history ≤ 48 h": card.get("history 48 h"),
            }
        )
    return pd.DataFrame(rows)
